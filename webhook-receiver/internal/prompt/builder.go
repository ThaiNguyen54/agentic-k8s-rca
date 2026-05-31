// internal/prompt/builder.go
package prompt

import (
	"fmt"
	"sort"
	"strings"

	"github.com/ThaiNguyen54/webhook-receiver/internal/systemcontext"
)

func BuildPrompt(labels, annotations map[string]string, startsAt string, ctx *systemcontext.IncidentContext) string {
	cluster := labels["cluster"]
	namespace := labels["namespace"]
	summary := annotations["summary"]

	scope := "unknown"
	node := "n/a"
	topology := "n/a"
	serviceProfile := "n/a"
	telemetry := "n/a"
	crds := "n/a"
	contextSummary := "n/a"

	if ctx != nil {
		scope = fmt.Sprintf("%s (blast radius: %s)", ctx.IncidentScope.Level, ctx.IncidentScope.BlastRadius)

		if ctx.AffectedNode != nil && ctx.AffectedNode.Name != "" {
			node = ctx.AffectedNode.Name
			if ctx.AffectedNode.InternalIP != "" {
				node += " [" + ctx.AffectedNode.InternalIP + "]"
			}
		}

		if ctx.SystemTopology != nil && len(ctx.SystemTopology.ServicePathHint) > 0 {
			topology = strings.Join(ctx.SystemTopology.ServicePathHint, " -> ")
		}

		if ctx.ServiceProfile != nil {
			serviceProfile = fmt.Sprintf(
				"name=%s/%s type=%s owner=%s tier=%s ports=%v deps=%v",
				ctx.ServiceProfile.Namespace,
				ctx.ServiceProfile.Name,
				ctx.ServiceProfile.ServiceType,
				ctx.ServiceProfile.Owner,
				ctx.ServiceProfile.Tier,
				ctx.ServiceProfile.Ports,
				ctx.ServiceProfile.Dependencies,
			)
		}

		var telemetryParts []string
		if ctx.TelemetryEndpoints.Prometheus != "" {
			telemetryParts = append(telemetryParts, "Prometheus="+ctx.TelemetryEndpoints.Prometheus)
		}
		if ctx.TelemetryEndpoints.Loki != "" {
			telemetryParts = append(telemetryParts, "Loki="+ctx.TelemetryEndpoints.Loki)
		}
		if ctx.TelemetryEndpoints.Tempo != "" {
			telemetryParts = append(telemetryParts, "Tempo="+ctx.TelemetryEndpoints.Tempo)
		}
		if ctx.TelemetryEndpoints.Grafana != "" {
			telemetryParts = append(telemetryParts, "Grafana="+ctx.TelemetryEndpoints.Grafana)
		}
		if ctx.TelemetryEndpoints.Otel != "" {
			telemetryParts = append(telemetryParts, "OTel="+ctx.TelemetryEndpoints.Otel)
		}
		if len(telemetryParts) > 0 {
			telemetry = strings.Join(telemetryParts, ", ")
		}

		if len(ctx.CRDRegistry) > 0 {
			var crdKinds []string
			for _, c := range ctx.CRDRegistry {
				crdKinds = append(crdKinds, c.Kind)
			}
			crds = strings.Join(crdKinds, ", ")
		}

		if ctx.Summary != "" {
			contextSummary = ctx.Summary
		}
	}

	base := fmt.Sprintf(`You are a Kubernetes SRE agent performing autonomous root cause analysis.

Rules:
- Analyze the Kubernetes evidence sections at the bottom of this prompt first.
- There may be multiple independent faults — identify each one separately.
- If multiple services are affected, determine whether they share a root cause or are independent.
- Cite specific evidence sections to support every conclusion.
- You may use tools to gather additional live evidence if the embedded evidence is insufficient.
- Do not ask for approval. Proceed autonomously.

## Incident Report
Detected At: %s
Cluster: %s
Kubeconfig Context: %s
Namespace: %s
Summary: %s

## System Context
Incident Scope: %s
Affected Node: %s
System Topology: %s
Service Profile: %s
Telemetry Endpoints: %s
CRD Registry: %s
Context Summary: %s

## Expected Output
Provide a structured response with:
1. Fault List — enumerate all distinct faults observed in the evidence
2. Root Cause — the underlying cause per fault, citing specific evidence sections
3. Impacted Path — services/pods affected per fault
4. Scope Assessment — blast radius
5. Remediation Steps — per fault, in priority order
6. Prevention
7. Confidence Level`,
		startsAt,
		cluster,
		cluster,
		namespace,
		summary,
		scope,
		node,
		topology,
		serviceProfile,
		telemetry,
		crds,
		contextSummary,
	)

	if ctx != nil && len(ctx.RawEvidence) > 0 {
		base = appendEvidence(base, ctx.RawEvidence)
	}

	return base
}

func appendEvidence(base string, evidence map[string]string) string {
	var sb strings.Builder
	sb.WriteString(base)
	sb.WriteString("\n\n## Kubernetes Evidence\n")
	sb.WriteString("The following raw evidence was collected from the cluster at alert time.\n")
	sb.WriteString("Analyze all sections carefully — multiple faults may be present.\n")

	// Fixed ordering for well-known keys
	ordered := []string{
		"nodes.txt",
		"pods-all.txt",
		"events.txt",
		"services.txt",
		"endpoints.txt",
		"deployments.txt",
	}
	written := make(map[string]bool, len(evidence))

	for _, key := range ordered {
		if content, ok := evidence[key]; ok {
			fmt.Fprintf(&sb, "\n=== EVIDENCE: %s ===\n", key)
			sb.WriteString(content)
			written[key] = true
		}
	}

	// Remaining keys (node describes, pod describes, logs) sorted for determinism
	remaining := make([]string, 0, len(evidence))
	for key := range evidence {
		if !written[key] {
			remaining = append(remaining, key)
		}
	}
	sort.Strings(remaining)

	for _, key := range remaining {
		fmt.Fprintf(&sb, "\n=== EVIDENCE: %s ===\n", key)
		sb.WriteString(evidence[key])
	}

	return sb.String()
}
