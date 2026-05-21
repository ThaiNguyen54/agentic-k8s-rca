package controller

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/go-logr/logr"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/client-go/tools/cache"
)

type FaultDetector struct {
	Log         logr.Logger
	ClusterName string

	dedupeMu sync.Mutex
	dedupe   map[string]time.Time
}

type FaultSignal struct {
	FaultType     string            `json:"faultType"`
	FaultObject   string            `json:"faultObject"`
	RootCauseHint string            `json:"rootCauseHint"`
	Severity      string            `json:"severity"`
	Detail        string            `json:"detail"`
	Evidence      map[string]string `json:"evidence,omitempty"`
}

func (f *FaultDetector) init() {
	if f.dedupe == nil {
		f.dedupe = make(map[string]time.Time)
	}
}

func (f *FaultDetector) dedupeTTL() time.Duration {
	if raw := os.Getenv("FAULT_DEDUP_TTL"); raw != "" {
		if d, err := time.ParseDuration(raw); err == nil && d > 0 {
			return d
		}
	}
	return 10 * time.Minute
}

func (f *FaultDetector) shouldSend(key string) bool {
	f.init()
	f.dedupeMu.Lock()
	defer f.dedupeMu.Unlock()

	now := time.Now().UTC()
	ttl := f.dedupeTTL()

	for k, ts := range f.dedupe {
		if now.Sub(ts) > ttl {
			delete(f.dedupe, k)
		}
	}

	if ts, exists := f.dedupe[key]; exists && now.Sub(ts) <= ttl {
		return false
	}

	f.dedupe[key] = now
	return true
}

func (f *FaultDetector) buildDedupKey(namespace string, fault FaultSignal) string {
	parts := []string{f.ClusterName, namespace, fault.FaultObject, fault.RootCauseHint, fault.Severity}
	if len(fault.Evidence) > 0 {
		keys := make([]string, 0, len(fault.Evidence))
		for k := range fault.Evidence {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		for _, k := range keys {
			parts = append(parts, fmt.Sprintf("%s=%s", k, fault.Evidence[k]))
		}
	}
	return strings.Join(parts, "|")
}

// SendWebhook fires the fault event to the webhook receiver asynchronously.
// Reads WEBHOOK_URL from env — if unset, fault is only logged locally.
func (f *FaultDetector) SendWebhook(namespace string, fault FaultSignal) {
	webhookURL := os.Getenv("WEBHOOK_URL")
	if webhookURL == "" {
		f.Log.Info("WEBHOOK_URL not set — skipping webhook dispatch",
			"cluster", f.ClusterName,
			"fault", fault.FaultObject,
		)
		return
	}

	if namespace == "" {
		namespace = "cluster-scoped"
	}

	dedupKey := f.buildDedupKey(namespace, fault)
	if !f.shouldSend(dedupKey) {
		f.Log.Info("duplicate fault suppressed",
			"cluster", f.ClusterName,
			"namespace", namespace,
			"fault_object", fault.FaultObject,
			"hint", fault.RootCauseHint,
		)
		return
	}

	payload := map[string]interface{}{
		"version":  "4",
		"groupKey": fmt.Sprintf("cluster=%s,object=%s", f.ClusterName, fault.FaultObject),
		"status":   "firing",
		"alerts": []map[string]interface{}{
			{
				"status": "firing",
				"labels": map[string]string{
					"cluster":         f.ClusterName,
					"namespace":       namespace,
					"fault_type":      fault.FaultType,
					"fault_object":    fault.FaultObject,
					"root_cause_hint": fault.RootCauseHint,
					"symptom_hint":    fault.RootCauseHint,
					"severity":        fault.Severity,
				},
				"annotations": map[string]interface{}{
					"summary": fmt.Sprintf("[%s] %s detected on %s",
						f.ClusterName, fault.RootCauseHint, fault.FaultObject),
					"detail":   fault.Detail,
					"evidence": fault.Evidence,
				},
				"startsAt": time.Now().UTC().Format(time.RFC3339),
			},
		},
	}

	body, err := json.Marshal(payload)
	if err != nil {
		f.Log.Error(err, "failed to marshal webhook payload",
			"cluster", f.ClusterName,
			"fault", fault.FaultObject,
		)
		return
	}

	go func() {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		f.Log.Info("sending webhook payload",
			"url", webhookURL,
			"cluster", f.ClusterName,
			"fault_object", fault.FaultObject,
			"payload", string(body),
		)

		req, err := http.NewRequestWithContext(ctx, http.MethodPost, webhookURL, bytes.NewReader(body))
		if err != nil {
			f.Log.Error(err, "failed to create webhook request", "url", webhookURL)
			return
		}
		req.Header.Set("Content-Type", "application/json")

		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			f.Log.Error(err, "failed to send webhook",
				"url", webhookURL,
				"cluster", f.ClusterName,
				"fault", fault.FaultObject,
			)
			return
		}
		defer resp.Body.Close()

		respBody, _ := io.ReadAll(resp.Body)

		if resp.StatusCode >= 300 {
			f.Log.Error(fmt.Errorf("webhook returned non-2xx"),
				"webhook dispatch failed",
				"status", resp.StatusCode,
				"response_body", string(respBody),
				"cluster", f.ClusterName,
				"fault_object", fault.FaultObject,
				"hint", fault.RootCauseHint,
			)
			return
		}

		f.Log.Info("webhook dispatched",
			"status", resp.StatusCode,
			"response_body", string(respBody),
			"cluster", f.ClusterName,
			"fault_object", fault.FaultObject,
			"hint", fault.RootCauseHint,
		)
	}()
}

// ─── Pod handler ─────────────────────────────────────────────────────────────

func (f *FaultDetector) PodEventHandler() cache.ResourceEventHandler {
	return cache.ResourceEventHandlerFuncs{
		AddFunc:    func(obj interface{}) { f.onPodAdd(obj) },
		UpdateFunc: func(oldObj, newObj interface{}) { f.onPodUpdate(oldObj, newObj) },
		DeleteFunc: func(obj interface{}) {},
	}
}

func (f *FaultDetector) onPodAdd(obj interface{}) {
	if !initialScanEnabled() {
		return
	}
	pod, ok := obj.(*corev1.Pod)
	if !ok {
		return
	}
	f.emitPodFaults(pod, detectPodFaults(pod))
}

func (f *FaultDetector) onPodUpdate(oldObj, newObj interface{}) {
	oldPod, okOld := oldObj.(*corev1.Pod)
	newPod, okNew := newObj.(*corev1.Pod)
	if !okOld || !okNew {
		return
	}

	oldFaults := faultMap(detectPodFaults(oldPod))
	for _, fault := range detectPodFaults(newPod) {
		key := fault.FaultObject + "|" + fault.RootCauseHint
		if _, exists := oldFaults[key]; exists {
			continue
		}
		f.emitPodFaults(newPod, []FaultSignal{fault})
	}
}

func (f *FaultDetector) emitPodFaults(pod *corev1.Pod, faults []FaultSignal) {
	for _, fault := range faults {
		f.Log.Info("POD FAULT DETECTED",
			"cluster", f.ClusterName,
			"pod", pod.Name,
			"namespace", pod.Namespace,
			"node", pod.Spec.NodeName,
			"fault_type", fault.FaultType,
			"fault_object", fault.FaultObject,
			"root_cause_hint", fault.RootCauseHint,
			"detail", fault.Detail,
		)
		f.SendWebhook(pod.Namespace, fault)
	}
}

// ─── Node handler ─────────────────────────────────────────────────────────────

func (f *FaultDetector) NodeEventHandler() cache.ResourceEventHandler {
	return cache.ResourceEventHandlerFuncs{
		AddFunc:    func(obj interface{}) { f.onNodeAdd(obj) },
		UpdateFunc: func(oldObj, newObj interface{}) { f.onNodeUpdate(oldObj, newObj) },
		DeleteFunc: func(obj interface{}) {},
	}
}

func (f *FaultDetector) onNodeAdd(obj interface{}) {
	if !initialScanEnabled() {
		return
	}
	node, ok := obj.(*corev1.Node)
	if !ok {
		return
	}
	f.emitNodeFaults(node, detectNodeFaults(node))
}

func (f *FaultDetector) onNodeUpdate(oldObj, newObj interface{}) {
	oldNode, okOld := oldObj.(*corev1.Node)
	newNode, okNew := newObj.(*corev1.Node)
	if !okOld || !okNew {
		return
	}

	if !nodeBecameUnhealthy(oldNode, newNode) && !pressureTransition(oldNode, newNode) && !cordonTransition(oldNode, newNode) {
		return
	}

	f.emitNodeFaults(newNode, detectNodeFaults(newNode))
}

func (f *FaultDetector) emitNodeFaults(node *corev1.Node, faults []FaultSignal) {
	for _, fault := range faults {
		f.Log.Info("NODE FAULT DETECTED",
			"cluster", f.ClusterName,
			"node", node.Name,
			"fault_type", fault.FaultType,
			"fault_object", fault.FaultObject,
			"root_cause_hint", fault.RootCauseHint,
			"detail", fault.Detail,
		)
		f.SendWebhook("", fault)
	}
}

// ─── Endpoint handler ─────────────────────────────────────────────────────────

//	func (f *FaultDetector) EndpointEventHandler() cache.ResourceEventHandler {
//		return cache.ResourceEventHandlerFuncs{
//			AddFunc:    func(obj interface{}) { f.onEndpointAdd(obj) },
//			UpdateFunc: func(oldObj, newObj interface{}) { f.onEndpointUpdate(oldObj, newObj) },
//			DeleteFunc: func(obj interface{}) {},
//		}
//	}
func (f *FaultDetector) EndpointEventHandler() cache.ResourceEventHandler {
	return cache.ResourceEventHandlerFuncs{
		AddFunc:    func(obj interface{}) {},
		UpdateFunc: func(oldObj, newObj interface{}) {},
		DeleteFunc: func(obj interface{}) {},
	}
}
func (f *FaultDetector) onEndpointAdd(obj interface{}) {
	if !initialScanEnabled() {
		return
	}
	ep, ok := obj.(*corev1.Endpoints)
	if !ok {
		return
	}
	if fault, shouldSend := detectEndpointFault(ep); shouldSend {
		f.emitEndpointFault(ep, fault)
	}
}

func (f *FaultDetector) onEndpointUpdate(oldObj, newObj interface{}) {
	oldEp, okOld := oldObj.(*corev1.Endpoints)
	newEp, okNew := newObj.(*corev1.Endpoints)
	if !okOld || !okNew {
		return
	}

	oldReady, oldNotReady := endpointCounts(oldEp)
	newReady, newNotReady := endpointCounts(newEp)

	if !(newReady == 0 && (oldReady > 0 || oldNotReady != newNotReady || len(oldEp.Subsets) != len(newEp.Subsets))) {
		return
	}

	if fault, shouldSend := detectEndpointFault(newEp); shouldSend {
		f.emitEndpointFault(newEp, fault)
	}
}

func (f *FaultDetector) emitEndpointFault(ep *corev1.Endpoints, fault FaultSignal) {
	f.Log.Info("ENDPOINT FAULT DETECTED",
		"cluster", f.ClusterName,
		"service", ep.Name,
		"namespace", ep.Namespace,
		"fault_type", fault.FaultType,
		"fault_object", fault.FaultObject,
		"root_cause_hint", fault.RootCauseHint,
		"detail", fault.Detail,
	)
	f.SendWebhook(ep.Namespace, fault)
}

// ─── Pod fault detection ──────────────────────────────────────────────────────

func detectPodFaults(pod *corev1.Pod) []FaultSignal {
	var faults []FaultSignal
	podObj := fmt.Sprintf("pod/%s", pod.Name)

	if pod.Status.Phase == corev1.PodFailed && (pod.Status.Reason != "" || pod.Status.Message != "") {
		faults = append(faults, FaultSignal{
			FaultType:     "Application_Fault",
			FaultObject:   podObj,
			RootCauseHint: "pod_failed",
			Severity:      "warning",
			Detail:        fmt.Sprintf("phase=Failed reason=%s message=%s", pod.Status.Reason, pod.Status.Message),
			Evidence: map[string]string{
				"phase":   string(pod.Status.Phase),
				"reason":  pod.Status.Reason,
				"message": pod.Status.Message,
			},
		})
	}

	for _, cs := range pod.Status.ContainerStatuses {
		if cs.State.Waiting != nil && cs.State.Waiting.Reason == "CrashLoopBackOff" {
			faults = append(faults, FaultSignal{
				FaultType:     "Application_Fault",
				FaultObject:   podObj,
				RootCauseHint: "crashloopbackoff",
				Severity:      "warning",
				Detail:        fmt.Sprintf("container=%s restartCount=%d", cs.Name, cs.RestartCount),
				Evidence: map[string]string{
					"container":    cs.Name,
					"restartCount": fmt.Sprintf("%d", cs.RestartCount),
				},
			})
		}
		if cs.State.Terminated != nil && cs.State.Terminated.Reason == "OOMKilled" {
			faults = append(faults, FaultSignal{
				FaultType:     "Application_Fault",
				FaultObject:   podObj,
				RootCauseHint: "oomkilled",
				Severity:      "warning",
				Detail:        fmt.Sprintf("container=%s exitCode=%d", cs.Name, cs.State.Terminated.ExitCode),
				Evidence: map[string]string{
					"container": cs.Name,
					"exitCode":  fmt.Sprintf("%d", cs.State.Terminated.ExitCode),
				},
			})
		}
		if cs.State.Waiting != nil && (cs.State.Waiting.Reason == "ImagePullBackOff" || cs.State.Waiting.Reason == "ErrImagePull") {
			faults = append(faults, FaultSignal{
				FaultType:     "Configuration_Fault",
				FaultObject:   podObj,
				RootCauseHint: strings.ToLower(cs.State.Waiting.Reason),
				Severity:      "warning",
				Detail:        fmt.Sprintf("container=%s image=%s", cs.Name, cs.Image),
				Evidence: map[string]string{
					"container": cs.Name,
					"image":     cs.Image,
				},
			})
		}
		if cs.RestartCount > 5 {
			faults = append(faults, FaultSignal{
				FaultType:     "Application_Fault",
				FaultObject:   podObj,
				RootCauseHint: "high_restart_count",
				Severity:      "warning",
				Detail:        fmt.Sprintf("container=%s restartCount=%d", cs.Name, cs.RestartCount),
				Evidence: map[string]string{
					"container":    cs.Name,
					"restartCount": fmt.Sprintf("%d", cs.RestartCount),
				},
			})
		}
	}
	return faults
}

// ─── Node fault detection ─────────────────────────────────────────────────────

func detectNodeFaults(node *corev1.Node) []FaultSignal {
	var faults []FaultSignal
	nodeObj := fmt.Sprintf("node/%s", node.Name)

	for _, condition := range node.Status.Conditions {
		switch condition.Type {
		case corev1.NodeReady:
			if condition.Status != corev1.ConditionTrue {
				severity := "warning"
				if condition.Status == corev1.ConditionUnknown {
					severity = "critical"
				}
				faults = append(faults, FaultSignal{
					FaultType:     "Infrastructure_Fault",
					FaultObject:   nodeObj,
					RootCauseHint: "node_not_ready",
					Severity:      severity,
					Detail:        fmt.Sprintf("NodeReady=%s reason=%s message=%s", condition.Status, condition.Reason, condition.Message),
					Evidence: map[string]string{
						"conditionType":      string(condition.Type),
						"conditionStatus":    string(condition.Status),
						"reason":             condition.Reason,
						"message":            condition.Message,
						"lastTransitionTime": condition.LastTransitionTime.UTC().Format(time.RFC3339),
						"lastHeartbeatTime":  condition.LastHeartbeatTime.UTC().Format(time.RFC3339),
						"taints":             formatNodeTaints(node.Spec.Taints),
					},
				})
			}
		case corev1.NodeMemoryPressure:
			if condition.Status == corev1.ConditionTrue {
				faults = append(faults, FaultSignal{
					FaultType:     "Infrastructure_Fault",
					FaultObject:   nodeObj,
					RootCauseHint: "node_memory_pressure",
					Severity:      "warning",
					Detail:        fmt.Sprintf("MemoryPressure=True reason=%s", condition.Reason),
					Evidence: map[string]string{
						"reason":             condition.Reason,
						"lastTransitionTime": condition.LastTransitionTime.UTC().Format(time.RFC3339),
					},
				})
			}
		case corev1.NodeDiskPressure:
			if condition.Status == corev1.ConditionTrue {
				faults = append(faults, FaultSignal{
					FaultType:     "Infrastructure_Fault",
					FaultObject:   nodeObj,
					RootCauseHint: "node_disk_pressure",
					Severity:      "warning",
					Detail:        fmt.Sprintf("DiskPressure=True reason=%s", condition.Reason),
					Evidence: map[string]string{
						"reason":             condition.Reason,
						"lastTransitionTime": condition.LastTransitionTime.UTC().Format(time.RFC3339),
					},
				})
			}
		case corev1.NodeNetworkUnavailable:
			if condition.Status == corev1.ConditionTrue {
				faults = append(faults, FaultSignal{
					FaultType:     "Infrastructure_Fault",
					FaultObject:   nodeObj,
					RootCauseHint: "node_network_unavailable",
					Severity:      "warning",
					Detail:        fmt.Sprintf("NetworkUnavailable=True reason=%s", condition.Reason),
					Evidence: map[string]string{
						"reason":             condition.Reason,
						"lastTransitionTime": condition.LastTransitionTime.UTC().Format(time.RFC3339),
					},
				})
			}
		}
	}

	if node.Spec.Unschedulable {
		faults = append(faults, FaultSignal{
			FaultType:     "Infrastructure_Fault",
			FaultObject:   nodeObj,
			RootCauseHint: "node_cordoned",
			Severity:      "info",
			Detail:        "node.Spec.Unschedulable=true",
			Evidence: map[string]string{
				"unschedulable": "true",
			},
		})
	}
	return faults
}

func detectEndpointFault(ep *corev1.Endpoints) (FaultSignal, bool) {
	if ep.Namespace == "kube-system" || ep.Name == "kubernetes" {
		return FaultSignal{}, false
	}

	totalReady, totalNotReady := endpointCounts(ep)
	if totalReady > 0 {
		return FaultSignal{}, false
	}

	hint := "service_no_ready_endpoints"
	detail := fmt.Sprintf("readyAddresses=0 notReadyAddresses=%d", totalNotReady)
	if totalNotReady > 0 {
		hint = "service_only_notready_endpoints"
		detail = fmt.Sprintf("readyAddresses=0 notReadyAddresses=%d", totalNotReady)
	}
	if len(ep.Subsets) == 0 {
		hint = "service_empty_endpoints"
		detail = "readyAddresses=0 notReadyAddresses=0 subsets=0"
	}

	return FaultSignal{
		FaultType:     "Network_Fault",
		FaultObject:   fmt.Sprintf("endpoints/%s", ep.Name),
		RootCauseHint: hint,
		Severity:      "warning",
		Detail:        detail,
		Evidence: map[string]string{
			"service":           ep.Name,
			"namespace":         ep.Namespace,
			"readyAddresses":    fmt.Sprintf("%d", totalReady),
			"notReadyAddresses": fmt.Sprintf("%d", totalNotReady),
			"subsets":           fmt.Sprintf("%d", len(ep.Subsets)),
		},
	}, true
}

func endpointCounts(ep *corev1.Endpoints) (int, int) {
	totalReady := 0
	totalNotReady := 0
	for _, subset := range ep.Subsets {
		totalReady += len(subset.Addresses)
		totalNotReady += len(subset.NotReadyAddresses)
	}
	return totalReady, totalNotReady
}

func nodeBecameUnhealthy(oldNode, newNode *corev1.Node) bool {
	oldCond, okOld := getNodeCondition(oldNode, corev1.NodeReady)
	newCond, okNew := getNodeCondition(newNode, corev1.NodeReady)
	if !okNew {
		return false
	}
	if !okOld {
		return newCond.Status != corev1.ConditionTrue
	}
	if oldCond.Status == corev1.ConditionTrue && newCond.Status != corev1.ConditionTrue {
		return true
	}
	if oldCond.Status != newCond.Status && newCond.Status != corev1.ConditionTrue {
		return true
	}
	return oldCond.LastTransitionTime != newCond.LastTransitionTime && newCond.Status != corev1.ConditionTrue
}

func pressureTransition(oldNode, newNode *corev1.Node) bool {
	for _, t := range []corev1.NodeConditionType{corev1.NodeMemoryPressure, corev1.NodeDiskPressure, corev1.NodeNetworkUnavailable} {
		oldCond, okOld := getNodeCondition(oldNode, t)
		newCond, okNew := getNodeCondition(newNode, t)
		if !okNew {
			continue
		}
		if !okOld && newCond.Status == corev1.ConditionTrue {
			return true
		}
		if okOld && oldCond.Status != corev1.ConditionTrue && newCond.Status == corev1.ConditionTrue {
			return true
		}
	}
	return false
}

func cordonTransition(oldNode, newNode *corev1.Node) bool {
	return !oldNode.Spec.Unschedulable && newNode.Spec.Unschedulable
}

func getNodeCondition(node *corev1.Node, conditionType corev1.NodeConditionType) (corev1.NodeCondition, bool) {
	for _, condition := range node.Status.Conditions {
		if condition.Type == conditionType {
			return condition, true
		}
	}
	return corev1.NodeCondition{}, false
}

func formatNodeTaints(taints []corev1.Taint) string {
	if len(taints) == 0 {
		return ""
	}
	parts := make([]string, 0, len(taints))
	for _, t := range taints {
		if t.Value != "" {
			parts = append(parts, fmt.Sprintf("%s=%s:%s", t.Key, t.Value, t.Effect))
			continue
		}
		parts = append(parts, fmt.Sprintf("%s:%s", t.Key, t.Effect))
	}
	sort.Strings(parts)
	return strings.Join(parts, ",")
}

func initialScanEnabled() bool {
	return strings.EqualFold(os.Getenv("ENABLE_INITIAL_FAULT_SCAN"), "true")
}

func faultMap(faults []FaultSignal) map[string]FaultSignal {
	out := make(map[string]FaultSignal, len(faults))
	for _, fault := range faults {
		out[fault.FaultObject+"|"+fault.RootCauseHint] = fault
	}
	return out
}

func containsAny(s string, keywords []string) bool {
	lower := strings.ToLower(s)
	for _, kw := range keywords {
		if strings.Contains(lower, strings.ToLower(kw)) {
			return true
		}
	}
	return false
}

// package controller

// import (
// 	"bytes"
// 	"context"
// 	"encoding/json"
// 	"fmt"
// 	"net/http"
// 	"os"
// 	"strings"
// 	"time"

// 	"github.com/go-logr/logr"
// 	corev1 "k8s.io/api/core/v1"
// 	"k8s.io/client-go/tools/cache"
// )

// type FaultDetector struct {
// 	Log         logr.Logger
// 	ClusterName string
// }

// type FaultSignal struct {
// 	FaultType     string
// 	FaultObject   string
// 	RootCauseHint string
// 	Detail        string
// }

// // SendWebhook fires the fault event to the webhook receiver asynchronously.
// // Reads WEBHOOK_URL from env — if unset, fault is only logged locally.
// func (f *FaultDetector) SendWebhook(namespace string, fault FaultSignal) {
// 	webhookURL := os.Getenv("WEBHOOK_URL")
// 	if webhookURL == "" {
// 		f.Log.Info("WEBHOOK_URL not set — skipping webhook dispatch",
// 			"cluster", f.ClusterName,
// 			"fault", fault.FaultObject,
// 		)
// 		return
// 	}

// 	payload := map[string]interface{}{
// 		"version":  "4",
// 		"groupKey": fmt.Sprintf("cluster=%s,object=%s", f.ClusterName, fault.FaultObject),
// 		"status":   "firing",
// 		"alerts": []map[string]interface{}{
// 			{
// 				"status": "firing",
// 				"labels": map[string]string{
// 					"cluster":         f.ClusterName,
// 					"namespace":       namespace,
// 					"fault_type":      fault.FaultType,
// 					"fault_object":    fault.FaultObject,
// 					"root_cause_hint": fault.RootCauseHint,
// 				},
// 				"annotations": map[string]string{
// 					"summary": fmt.Sprintf("[%s] %s detected on %s",
// 						f.ClusterName, fault.RootCauseHint, fault.FaultObject),
// 					"detail": fault.Detail,
// 				},
// 				"startsAt": time.Now().UTC().Format(time.RFC3339),
// 			},
// 		},
// 	}

// 	body, err := json.Marshal(payload)
// 	if err != nil {
// 		f.Log.Error(err, "failed to marshal webhook payload",
// 			"cluster", f.ClusterName,
// 			"fault", fault.FaultObject,
// 		)
// 		return
// 	}

// 	// Fire async — never block the reconcile loop
// 	go func() {
// 		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
// 		defer cancel()

// 		req, err := http.NewRequestWithContext(ctx,
// 			http.MethodPost, webhookURL, bytes.NewReader(body))
// 		if err != nil {
// 			f.Log.Error(err, "failed to create webhook request", "url", webhookURL)
// 			return
// 		}
// 		req.Header.Set("Content-Type", "application/json")

// 		resp, err := http.DefaultClient.Do(req)
// 		if err != nil {
// 			f.Log.Error(err, "failed to send webhook",
// 				"url", webhookURL,
// 				"cluster", f.ClusterName,
// 				"fault", fault.FaultObject,
// 			)
// 			return
// 		}
// 		defer resp.Body.Close()

// 		f.Log.Info("webhook dispatched",
// 			"status", resp.StatusCode,
// 			"cluster", f.ClusterName,
// 			"fault_object", fault.FaultObject,
// 			"hint", fault.RootCauseHint,
// 		)
// 	}()
// }

// // ─── Pod handler ─────────────────────────────────────────────────────────────

// func (f *FaultDetector) PodEventHandler() cache.ResourceEventHandler {
// 	return cache.ResourceEventHandlerFuncs{
// 		AddFunc:    func(obj interface{}) { f.onPod(obj) },
// 		UpdateFunc: func(_, newObj interface{}) { f.onPod(newObj) },
// 		DeleteFunc: func(obj interface{}) {},
// 	}
// }

// func (f *FaultDetector) onPod(obj interface{}) {
// 	pod, ok := obj.(*corev1.Pod)
// 	if !ok {
// 		return
// 	}
// 	for _, fault := range detectPodFaults(pod) {
// 		f.Log.Info("POD FAULT DETECTED",
// 			"cluster", f.ClusterName,
// 			"pod", pod.Name,
// 			"namespace", pod.Namespace,
// 			"node", pod.Spec.NodeName,
// 			"fault_type", fault.FaultType,
// 			"fault_object", fault.FaultObject,
// 			"root_cause_hint", fault.RootCauseHint,
// 			"detail", fault.Detail,
// 		)
// 		f.SendWebhook(pod.Namespace, fault)
// 	}
// }

// // ─── Node handler ─────────────────────────────────────────────────────────────

// func (f *FaultDetector) NodeEventHandler() cache.ResourceEventHandler {
// 	return cache.ResourceEventHandlerFuncs{
// 		AddFunc:    func(obj interface{}) { f.onNode(obj) },
// 		UpdateFunc: func(_, newObj interface{}) { f.onNode(newObj) },
// 		DeleteFunc: func(obj interface{}) {},
// 	}
// }

// func (f *FaultDetector) onNode(obj interface{}) {
// 	node, ok := obj.(*corev1.Node)
// 	if !ok {
// 		return
// 	}
// 	for _, fault := range detectNodeFaults(node) {
// 		f.Log.Info("NODE FAULT DETECTED",
// 			"cluster", f.ClusterName,
// 			"node", node.Name,
// 			"fault_type", fault.FaultType,
// 			"fault_object", fault.FaultObject,
// 			"root_cause_hint", fault.RootCauseHint,
// 			"detail", fault.Detail,
// 		)
// 		// Nodes are cluster-scoped — use "default" as namespace
// 		f.SendWebhook("default", fault)
// 	}
// }

// // ─── Endpoint handler ─────────────────────────────────────────────────────────

// func (f *FaultDetector) EndpointEventHandler() cache.ResourceEventHandler {
// 	return cache.ResourceEventHandlerFuncs{
// 		AddFunc:    func(obj interface{}) { f.onEndpoint(obj) },
// 		UpdateFunc: func(_, newObj interface{}) { f.onEndpoint(newObj) },
// 		DeleteFunc: func(obj interface{}) {},
// 	}
// }

// func (f *FaultDetector) onEndpoint(obj interface{}) {
// 	ep, ok := obj.(*corev1.Endpoints)
// 	if !ok {
// 		return
// 	}
// 	if ep.Namespace == "kube-system" || ep.Name == "kubernetes" {
// 		return
// 	}
// 	if len(ep.Subsets) == 0 {
// 		return
// 	}

// 	totalReady := 0
// 	totalNotReady := 0
// 	for _, subset := range ep.Subsets {
// 		totalReady += len(subset.Addresses)
// 		totalNotReady += len(subset.NotReadyAddresses)
// 	}

// 	if totalReady == 0 {
// 		hint := "no_ready_endpoints"
// 		detail := fmt.Sprintf(
// 			"readyAddresses=0 notReadyAddresses=%d — port mismatch or pods not ready, Ingress will 502",
// 			totalNotReady,
// 		)
// 		if totalNotReady > 0 {
// 			hint = "endpoint_port_mismatch_or_probe_fail"
// 			detail = fmt.Sprintf(
// 				"readyAddresses=0 notReadyAddresses=%d — check Service targetPort vs container port",
// 				totalNotReady,
// 			)
// 		}

// 		fault := FaultSignal{
// 			FaultType:     "Network_Fault",
// 			FaultObject:   fmt.Sprintf("endpoints/%s", ep.Name),
// 			RootCauseHint: hint,
// 			Detail:        detail,
// 		}

// 		f.Log.Info("ENDPOINT FAULT DETECTED — likely 502 source",
// 			"cluster", f.ClusterName,
// 			"service", ep.Name,
// 			"namespace", ep.Namespace,
// 			"fault_type", fault.FaultType,
// 			"fault_object", fault.FaultObject,
// 			"root_cause_hint", fault.RootCauseHint,
// 			"detail", fault.Detail,
// 		)
// 		f.SendWebhook(ep.Namespace, fault)
// 	}
// }

// // ─── Pod fault detection ──────────────────────────────────────────────────────

// func detectPodFaults(pod *corev1.Pod) []FaultSignal {
// 	var faults []FaultSignal
// 	podObj := fmt.Sprintf("pod/%s", pod.Name)

// 	if pod.Status.Phase == corev1.PodFailed &&
// 		(pod.Status.Reason != "" || pod.Status.Message != "") {
// 		faults = append(faults, FaultSignal{
// 			FaultType:     "Application_Fault",
// 			FaultObject:   podObj,
// 			RootCauseHint: "pod_failed",
// 			Detail:        fmt.Sprintf("phase=Failed reason=%s message=%s", pod.Status.Reason, pod.Status.Message),
// 		})
// 	}

// 	for _, cs := range pod.Status.ContainerStatuses {
// 		if cs.State.Waiting != nil && cs.State.Waiting.Reason == "CrashLoopBackOff" {
// 			faults = append(faults, FaultSignal{
// 				FaultType:     "Application_Fault",
// 				FaultObject:   podObj,
// 				RootCauseHint: "CrashLoopBackOff",
// 				Detail:        fmt.Sprintf("container=%s restartCount=%d", cs.Name, cs.RestartCount),
// 			})
// 		}
// 		if cs.State.Terminated != nil && cs.State.Terminated.Reason == "OOMKilled" {
// 			faults = append(faults, FaultSignal{
// 				FaultType:     "Application_Fault",
// 				FaultObject:   podObj,
// 				RootCauseHint: "OOMKilled",
// 				Detail:        fmt.Sprintf("container=%s exitCode=%d", cs.Name, cs.State.Terminated.ExitCode),
// 			})
// 		}
// 		if cs.State.Waiting != nil &&
// 			(cs.State.Waiting.Reason == "ImagePullBackOff" || cs.State.Waiting.Reason == "ErrImagePull") {
// 			faults = append(faults, FaultSignal{
// 				FaultType:     "Configuration_Fault",
// 				FaultObject:   podObj,
// 				RootCauseHint: strings.ToLower(cs.State.Waiting.Reason),
// 				Detail:        fmt.Sprintf("container=%s image=%s", cs.Name, cs.Image),
// 			})
// 		}
// 		if cs.RestartCount > 5 {
// 			faults = append(faults, FaultSignal{
// 				FaultType:     "Application_Fault",
// 				FaultObject:   podObj,
// 				RootCauseHint: "high_restart_count",
// 				Detail:        fmt.Sprintf("container=%s restartCount=%d", cs.Name, cs.RestartCount),
// 			})
// 		}
// 	}
// 	return faults
// }

// // ─── Node fault detection ─────────────────────────────────────────────────────

// func detectNodeFaults(node *corev1.Node) []FaultSignal {
// 	var faults []FaultSignal
// 	nodeObj := fmt.Sprintf("node/%s", node.Name)

// 	for _, condition := range node.Status.Conditions {
// 		switch condition.Type {
// 		case corev1.NodeReady:
// 			if condition.Status != corev1.ConditionTrue {
// 				faults = append(faults, FaultSignal{
// 					FaultType:     "Infrastructure_Fault",
// 					FaultObject:   nodeObj,
// 					RootCauseHint: "node_not_ready",
// 					Detail:        fmt.Sprintf("NodeReady=%s reason=%s message=%s", condition.Status, condition.Reason, condition.Message),
// 				})
// 				if containsAny(condition.Message, []string{"containerd", "container runtime", "runtime"}) {
// 					faults = append(faults, FaultSignal{
// 						FaultType:     "Infrastructure_Fault",
// 						FaultObject:   nodeObj,
// 						RootCauseHint: "containerd_unavailable",
// 						Detail:        fmt.Sprintf("runtime hint in NotReady message: %s", condition.Message),
// 					})
// 				}
// 			}
// 		case corev1.NodeMemoryPressure:
// 			if condition.Status == corev1.ConditionTrue {
// 				faults = append(faults, FaultSignal{
// 					FaultType:     "Infrastructure_Fault",
// 					FaultObject:   nodeObj,
// 					RootCauseHint: "node_memory_pressure",
// 					Detail:        fmt.Sprintf("MemoryPressure=True reason=%s", condition.Reason),
// 				})
// 			}
// 		case corev1.NodeDiskPressure:
// 			if condition.Status == corev1.ConditionTrue {
// 				faults = append(faults, FaultSignal{
// 					FaultType:     "Infrastructure_Fault",
// 					FaultObject:   nodeObj,
// 					RootCauseHint: "node_disk_pressure",
// 					Detail:        fmt.Sprintf("DiskPressure=True reason=%s", condition.Reason),
// 				})
// 			}
// 		case corev1.NodeNetworkUnavailable:
// 			if condition.Status == corev1.ConditionTrue {
// 				faults = append(faults, FaultSignal{
// 					FaultType:     "Infrastructure_Fault",
// 					FaultObject:   nodeObj,
// 					RootCauseHint: "node_network_unavailable",
// 					Detail:        fmt.Sprintf("NetworkUnavailable=True reason=%s", condition.Reason),
// 				})
// 			}
// 		}
// 	}

// 	if node.Spec.Unschedulable {
// 		faults = append(faults, FaultSignal{
// 			FaultType:     "Infrastructure_Fault",
// 			FaultObject:   nodeObj,
// 			RootCauseHint: "node_cordoned",
// 			Detail:        "node.Spec.Unschedulable=true",
// 		})
// 	}
// 	return faults
// }

// func containsAny(s string, keywords []string) bool {
// 	lower := strings.ToLower(s)
// 	for _, kw := range keywords {
// 		if strings.Contains(lower, strings.ToLower(kw)) {
// 			return true
// 		}
// 	}
// 	return false
// }
