package systemcontext

import (
	"bytes"
	"context"
	"fmt"
	"os/exec"
	"strconv"
	"strings"
	"sync"
	"time"
)

type KubectlEvidenceCollector struct {
	KubeconfigPath string
	Timeout        time.Duration
}

func NewKubectlEvidenceCollector(kubeconfigPath string) *KubectlEvidenceCollector {
	return &KubectlEvidenceCollector{
		KubeconfigPath: kubeconfigPath,
		Timeout:        45 * time.Second,
	}
}

var _ EvidenceCollector = (*KubectlEvidenceCollector)(nil)

// clusterScopedNamespace returns true for synthetic namespace labels that the
// fault controller uses to indicate cluster-level faults (e.g. node failures).
// These are not real Kubernetes namespaces, so namespace-scoped kubectl commands
// must use --all-namespaces instead.
func clusterScopedNamespace(ns string) bool {
	switch ns {
	case "", "cluster-scoped", "cluster", "cluster-wide":
		return true
	}
	return false
}

func (c *KubectlEvidenceCollector) CollectEvidence(ctx context.Context, in IncidentEvent) (map[string]string, error) {
	ns := strings.TrimSpace(in.Namespace)
	cluster := strings.TrimSpace(in.Cluster)

	ctx, cancel := context.WithTimeout(ctx, c.Timeout)
	defer cancel()

	evidence := make(map[string]string)
	var mu sync.Mutex
	var wg sync.WaitGroup

	store := func(key, content string) {
		mu.Lock()
		evidence[key] = content
		mu.Unlock()
	}

	run := func(key string, args ...string) {
		wg.Add(1)
		go func() {
			defer wg.Done()
			out, err := c.kubectl(ctx, cluster, args...)
			if err != nil {
				store(key, fmt.Sprintf("# kubectl %s: %v\n", strings.Join(args, " "), err))
			} else {
				store(key, out)
			}
		}()
	}

	// For cluster-scoped faults (node failures etc.) the namespace label is a
	// synthetic value, not a real namespace — use --all-namespaces instead.
	var nsArgs []string
	if clusterScopedNamespace(ns) {
		nsArgs = []string{"--all-namespaces"}
	} else {
		nsArgs = []string{"-n", ns}
	}

	// Phase 1: cluster-wide and namespace snapshots (concurrent)
	run("nodes.txt", "get", "nodes", "-o", "wide")
	run("pods-all.txt", append([]string{"get", "pods", "-o", "wide"}, nsArgs...)...)
	run("events.txt", append([]string{"get", "events", "--sort-by=.lastTimestamp"}, nsArgs...)...)
	run("services.txt", append([]string{"get", "svc"}, nsArgs...)...)
	run("endpoints.txt", append([]string{"get", "endpoints"}, nsArgs...)...)
	run("deployments.txt", append([]string{"get", "deployments"}, nsArgs...)...)
	wg.Wait()

	// Phase 2: per-pod evidence for troubled pods
	troubled := parseTroubledPods(evidence["pods-all.txt"], in.FaultObject, ns)
	for _, ref := range troubled {
		ref := ref

		wg.Add(1)
		go func() {
			defer wg.Done()
			out, err := c.kubectl(ctx, cluster, "describe", "pod", ref.Name, "-n", ref.Namespace)
			if err != nil {
				store(ref.Name+"-describe.txt", fmt.Sprintf("# error: %v\n", err))
			} else {
				store(ref.Name+"-describe.txt", out)
			}
		}()

		wg.Add(1)
		go func() {
			defer wg.Done()
			out, err := c.kubectl(ctx, cluster, "logs", ref.Name, "-n", ref.Namespace, "--tail=150", "--all-containers=true")
			if err != nil {
				store(ref.Name+"-logs.txt", fmt.Sprintf("# error: %v\n", err))
			} else {
				store(ref.Name+"-logs.txt", out)
			}
		}()

		wg.Add(1)
		go func() {
			defer wg.Done()
			out, err := c.kubectl(ctx, cluster, "logs", ref.Name, "-n", ref.Namespace, "--previous", "--tail=80", "--all-containers=true")
			if err == nil && strings.TrimSpace(out) != "" {
				store(ref.Name+"-logs-previous.txt", out)
			}
		}()
	}

	// Phase 2: node describe for node-level faults
	if strings.Contains(strings.ToLower(in.FaultType), "node") {
		nodeName := strings.TrimPrefix(strings.TrimSpace(in.FaultObject), "node/")
		if nodeName != "" {
			wg.Add(1)
			go func() {
				defer wg.Done()
				out, err := c.kubectl(ctx, cluster, "describe", "node", nodeName)
				if err != nil {
					store("node-"+nodeName+".txt", fmt.Sprintf("# error: %v\n", err))
				} else {
					store("node-"+nodeName+".txt", out)
				}
			}()
		}
	}

	wg.Wait()
	return evidence, nil
}

func (c *KubectlEvidenceCollector) kubectl(ctx context.Context, kubeContext string, args ...string) (string, error) {
	cmdArgs := make([]string, 0, len(args)+4)
	if kubeContext != "" {
		cmdArgs = append(cmdArgs, "--context="+kubeContext)
	}
	if c.KubeconfigPath != "" {
		cmdArgs = append(cmdArgs, "--kubeconfig="+c.KubeconfigPath)
	}
	cmdArgs = append(cmdArgs, "--request-timeout=10s")
	cmdArgs = append(cmdArgs, args...)

	cmd := exec.CommandContext(ctx, "kubectl", cmdArgs...)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		return "", fmt.Errorf("%w: %s", err, strings.TrimSpace(stderr.String()))
	}
	return stdout.String(), nil
}

type podRef struct {
	Namespace string
	Name      string
}

// parseTroubledPods parses kubectl get pods output (both namespaced and
// --all-namespaces formats). It detects the format from the header row,
// then returns faultObject-matching pods first, followed by any pod whose
// status is not Running/Completed/Succeeded or whose restart count > 0.
// fallbackNamespace is used when the output has no NAMESPACE column.
// Capped at 8 pods.
func parseTroubledPods(podListOutput, faultObject, fallbackNamespace string) []podRef {
	seen := make(map[string]struct{})
	var pods []podRef

	add := func(ns, name string) {
		name = strings.TrimPrefix(strings.TrimSpace(name), "pod/")
		if name == "" {
			return
		}
		key := ns + "/" + name
		if _, ok := seen[key]; !ok {
			seen[key] = struct{}{}
			pods = append(pods, podRef{Namespace: ns, Name: name})
		}
	}

	foPrefix := strings.TrimPrefix(strings.TrimSpace(faultObject), "pod/")
	foPrefix = strings.TrimPrefix(foPrefix, "deployment/")

	goodStatus := map[string]bool{
		"Running":   true,
		"Completed": true,
		"Succeeded": true,
	}

	lines := strings.Split(podListOutput, "\n")

	// Detect --all-namespaces format: header starts with "NAMESPACE"
	allNamespaces := len(lines) > 0 && strings.HasPrefix(strings.TrimSpace(lines[0]), "NAMESPACE")

	// Field column indices differ between the two formats:
	//   normal:         NAME STATUS RESTARTS ...  → name=0, status=2, restarts=3
	//   --all-namespaces: NAMESPACE NAME READY STATUS RESTARTS → ns=0, name=1, status=3, restarts=4
	nsCol, nameCol, statusCol, restartCol := -1, 0, 2, 3
	if allNamespaces {
		nsCol, nameCol, statusCol, restartCol = 0, 1, 3, 4
	}

	getPodNs := func(fields []string) string {
		if nsCol >= 0 && len(fields) > nsCol {
			return fields[nsCol]
		}
		return fallbackNamespace
	}

	// First pass: faultObject-matching pods (regardless of status)
	if foPrefix != "" {
		for i, line := range lines {
			if i == 0 || strings.TrimSpace(line) == "" {
				continue
			}
			fields := strings.Fields(line)
			if len(fields) <= nameCol {
				continue
			}
			if strings.HasPrefix(fields[nameCol], foPrefix) {
				add(getPodNs(fields), fields[nameCol])
			}
		}
	}

	// Second pass: all other troubled pods
	for i, line := range lines {
		if i == 0 || strings.TrimSpace(line) == "" {
			continue
		}
		fields := strings.Fields(line)
		if len(fields) <= restartCol {
			continue
		}
		podNs := getPodNs(fields)
		podName := fields[nameCol]
		status := fields[statusCol]
		restartStr := fields[restartCol]

		if !goodStatus[status] {
			add(podNs, podName)
			continue
		}
		restartCount, _ := strconv.Atoi(restartStr)
		if restartCount > 0 {
			add(podNs, podName)
		}
	}

	if len(pods) > 8 {
		pods = pods[:8]
	}
	return pods
}
