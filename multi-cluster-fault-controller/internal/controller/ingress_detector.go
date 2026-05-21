package controller

import (
	"context"
	"fmt"

	networkingv1 "k8s.io/api/networking/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/tools/cache"
)

// IngressChecker holds a clientset for active cross-checking
type IngressChecker struct {
	FaultDetector *FaultDetector
	Client        kubernetes.Interface
}

func (ic *IngressChecker) IngressEventHandler() cache.ResourceEventHandler {
	return cache.ResourceEventHandlerFuncs{
		AddFunc:    func(obj interface{}) { ic.onIngress(obj) },
		UpdateFunc: func(_, newObj interface{}) { ic.onIngress(newObj) },
		DeleteFunc: func(obj interface{}) {},
	}
}

func (ic *IngressChecker) ServiceEventHandler() cache.ResourceEventHandler {
	return cache.ResourceEventHandlerFuncs{
		UpdateFunc: func(_, newObj interface{}) { ic.checkAllIngresses() },
		AddFunc:    func(obj interface{}) {},
		DeleteFunc: func(obj interface{}) {},
	}
}

func (ic *IngressChecker) onIngress(obj interface{}) {
	ing, ok := obj.(*networkingv1.Ingress)
	if !ok {
		return
	}
	ic.checkIngress(ing)
}

func (ic *IngressChecker) checkAllIngresses() {
	ingList, err := ic.Client.NetworkingV1().Ingresses("").List(context.Background(), metav1.ListOptions{})
	if err != nil {
		return
	}
	for i := range ingList.Items {
		ic.checkIngress(&ingList.Items[i])
	}
}

func (ic *IngressChecker) checkIngress(ing *networkingv1.Ingress) {
	f := ic.FaultDetector

	for _, rule := range ing.Spec.Rules {
		if rule.HTTP == nil {
			continue
		}
		for _, path := range rule.HTTP.Paths {
			svcName := path.Backend.Service.Name
			svcPort := int(path.Backend.Service.Port.Number)
			ns := ing.Namespace

			// ── Get the Service ──────────────────────────────────────────
			svc, err := ic.Client.CoreV1().Services(ns).Get(
				context.Background(), svcName, metav1.GetOptions{},
			)
			if err != nil {
				fault := FaultSignal{
					FaultType:     "Configuration_Fault",
					FaultObject:   fmt.Sprintf("ingress/%s", ing.Name),
					RootCauseHint: "ingress_backend_service_missing",
					Detail:        fmt.Sprintf("service/%s not found — Ingress will 503/502", svcName),
				}
				f.Log.Info("INGRESS FAULT DETECTED",
					"cluster", f.ClusterName,
					"ingress", ing.Name,
					"namespace", ns,
					"fault_type", fault.FaultType,
					"fault_object", fault.FaultObject,
					"root_cause_hint", fault.RootCauseHint,
					"detail", fault.Detail,
				)
				f.SendWebhook(ns, fault)
				continue
			}

			// ── Find the targetPort for this service port ─────────────────
			var targetPort int
			var targetPortName string
			for _, sp := range svc.Spec.Ports {
				if int(sp.Port) == svcPort {
					targetPort = sp.TargetPort.IntValue()
					targetPortName = sp.TargetPort.String()
					break
				}
			}

			if targetPort == 0 {
				fault := FaultSignal{
					FaultType:     "Configuration_Fault",
					FaultObject:   fmt.Sprintf("service/%s", svcName),
					RootCauseHint: "service_port_not_found",
					Detail: fmt.Sprintf(
						"Ingress references service/%s port=%d but that port is not defined in the Service",
						svcName, svcPort,
					),
				}
				f.Log.Info("INGRESS FAULT DETECTED",
					"cluster", f.ClusterName,
					"ingress", ing.Name,
					"namespace", ns,
					"fault_type", fault.FaultType,
					"fault_object", fault.FaultObject,
					"root_cause_hint", fault.RootCauseHint,
					"detail", fault.Detail,
				)
				f.SendWebhook(ns, fault)
				continue
			}

			// ── Get pods selected by this service ──────────────────────────
			selector := svc.Spec.Selector
			if len(selector) == 0 {
				continue
			}
			labelSel := ""
			for k, v := range selector {
				if labelSel != "" {
					labelSel += ","
				}
				labelSel += k + "=" + v
			}

			pods, err := ic.Client.CoreV1().Pods(ns).List(
				context.Background(),
				metav1.ListOptions{LabelSelector: labelSel},
			)
			if err != nil || len(pods.Items) == 0 {
				continue
			}

			// ── Cross-check targetPort vs actual containerPort ─────────────
			for _, pod := range pods.Items {
				for _, container := range pod.Spec.Containers {
					for _, cp := range container.Ports {
						if int(cp.ContainerPort) == targetPort {
							goto portFound
						}
					}
				}

				// No container exposes targetPort — 502 root cause
				{
					fault := FaultSignal{
						FaultType:     "Configuration_Fault",
						FaultObject:   fmt.Sprintf("service/%s", svcName),
						RootCauseHint: "service_targetPort_mismatch",
						Detail: fmt.Sprintf(
							"Ingress→service/%s port=%d targetPort=%s(%d) but no container in pod/%s exposes port %d — connection will be refused → 502",
							svcName, svcPort, targetPortName, targetPort, pod.Name, targetPort,
						),
					}
					f.Log.Info("SERVICE PORT MISMATCH DETECTED — 502 root cause",
						"cluster", f.ClusterName,
						"ingress", ing.Name,
						"service", svcName,
						"namespace", ns,
						"fault_type", fault.FaultType,
						"fault_object", fault.FaultObject,
						"root_cause_hint", fault.RootCauseHint,
						"detail", fault.Detail,
					)
					f.SendWebhook(ns, fault)
				}
				break // report once per service, not per pod
			portFound:
				break
			}
		}
	}
}

// package controller

// import (
// 	"context"
// 	"fmt"

// 	networkingv1 "k8s.io/api/networking/v1"
// 	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
// 	"k8s.io/client-go/kubernetes"
// 	"k8s.io/client-go/tools/cache"
// )

// // IngressChecker holds a clientset for active cross-checking
// type IngressChecker struct {
// 	FaultDetector *FaultDetector
// 	Client        kubernetes.Interface
// }

// func (ic *IngressChecker) IngressEventHandler() cache.ResourceEventHandler {
// 	return cache.ResourceEventHandlerFuncs{
// 		AddFunc:    func(obj interface{}) { ic.onIngress(obj) },
// 		UpdateFunc: func(_, newObj interface{}) { ic.onIngress(newObj) },
// 		DeleteFunc: func(obj interface{}) {},
// 	}
// }

// func (ic *IngressChecker) ServiceEventHandler() cache.ResourceEventHandler {
// 	return cache.ResourceEventHandlerFuncs{
// 		// Re-check ingresses whenever a service changes
// 		UpdateFunc: func(_, newObj interface{}) { ic.checkAllIngresses() },
// 		AddFunc:    func(obj interface{}) {},
// 		DeleteFunc: func(obj interface{}) {},
// 	}
// }

// func (ic *IngressChecker) onIngress(obj interface{}) {
// 	ing, ok := obj.(*networkingv1.Ingress)
// 	if !ok {
// 		return
// 	}
// 	ic.checkIngress(ing)
// }

// func (ic *IngressChecker) checkAllIngresses() {
// 	ingList, err := ic.Client.NetworkingV1().Ingresses("").List(context.Background(), metav1.ListOptions{})
// 	if err != nil {
// 		return
// 	}
// 	for i := range ingList.Items {
// 		ic.checkIngress(&ingList.Items[i])
// 	}
// }

// func (ic *IngressChecker) checkIngress(ing *networkingv1.Ingress) {
// 	f := ic.FaultDetector

// 	for _, rule := range ing.Spec.Rules {
// 		if rule.HTTP == nil {
// 			continue
// 		}
// 		for _, path := range rule.HTTP.Paths {
// 			svcName := path.Backend.Service.Name
// 			svcPort := int(path.Backend.Service.Port.Number)
// 			ns := ing.Namespace

// 			// ── Get the Service ──────────────────────────────────────────
// 			svc, err := ic.Client.CoreV1().Services(ns).Get(
// 				context.Background(), svcName, metav1.GetOptions{},
// 			)
// 			if err != nil {
// 				f.Log.Info("INGRESS FAULT DETECTED",
// 					"cluster", f.ClusterName,
// 					"ingress", ing.Name,
// 					"namespace", ns,
// 					"fault_type", "Configuration_Fault",
// 					"fault_object", fmt.Sprintf("ingress/%s", ing.Name),
// 					"root_cause_hint", "ingress_backend_service_missing",
// 					"detail", fmt.Sprintf("service/%s not found — Ingress will 503/502", svcName),
// 				)
// 				continue
// 			}

// 			// ── Find the targetPort for this service port ─────────────────
// 			var targetPort int
// 			var targetPortName string
// 			for _, sp := range svc.Spec.Ports {
// 				if int(sp.Port) == svcPort {
// 					targetPort = sp.TargetPort.IntValue()
// 					targetPortName = sp.TargetPort.String()
// 					break
// 				}
// 			}

// 			if targetPort == 0 {
// 				f.Log.Info("INGRESS FAULT DETECTED",
// 					"cluster", f.ClusterName,
// 					"ingress", ing.Name,
// 					"namespace", ns,
// 					"fault_type", "Configuration_Fault",
// 					"fault_object", fmt.Sprintf("service/%s", svcName),
// 					"root_cause_hint", "service_port_not_found",
// 					"detail", fmt.Sprintf(
// 						"Ingress references service/%s port=%d but that port is not defined in the Service",
// 						svcName, svcPort,
// 					),
// 				)
// 				continue
// 			}

// 			// ── Get pods selected by this service ──────────────────────────
// 			selector := svc.Spec.Selector
// 			if len(selector) == 0 {
// 				continue // headless or external-name service
// 			}
// 			labelSel := ""
// 			for k, v := range selector {
// 				if labelSel != "" {
// 					labelSel += ","
// 				}
// 				labelSel += k + "=" + v
// 			}

// 			pods, err := ic.Client.CoreV1().Pods(ns).List(
// 				context.Background(),
// 				metav1.ListOptions{LabelSelector: labelSel},
// 			)
// 			if err != nil || len(pods.Items) == 0 {
// 				continue
// 			}

// 			// ── Cross-check targetPort vs actual containerPort ─────────────
// 			for _, pod := range pods.Items {
// 				for _, container := range pod.Spec.Containers {
// 					for _, cp := range container.Ports {
// 						if int(cp.ContainerPort) == targetPort {
// 							goto portFound // targetPort matches a real container port
// 						}
// 					}
// 				}
// 				// No container exposes targetPort — this is the 502 cause
// 				f.Log.Info("SERVICE PORT MISMATCH DETECTED — 502 root cause",
// 					"cluster", f.ClusterName,
// 					"ingress", ing.Name,
// 					"service", svcName,
// 					"namespace", ns,
// 					"fault_type", "Configuration_Fault",
// 					"fault_object", fmt.Sprintf("service/%s", svcName),
// 					"root_cause_hint", "service_targetPort_mismatch",
// 					"detail", fmt.Sprintf(
// 						"Ingress→service/%s port=%d targetPort=%s(%d) but no container in pod/%s exposes port %d — connection will be refused → 502",
// 						svcName, svcPort, targetPortName, targetPort, pod.Name, targetPort,
// 					),
// 				)
// 				break // report once per service, not per pod
// 			portFound:
// 				break
// 			}
// 		}
// 	}
// }
