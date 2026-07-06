// internal/prompt/builder.go
package prompt

import (
	"fmt"
	"sort"
	"strings"

	"github.com/ThaiNguyen54/webhook-receiver/internal/systemcontext"
)

// buildBase renders the incident report + system context sections, without any
// Kubernetes evidence appended. The evidence section is chosen by the caller:
// either the raw collect-all dump (BuildPrompt) or the distilled Evidence-First
// block (BuildPromptWithDistilledEvidence).
func buildBase(labels, annotations map[string]string, startsAt string, ctx *systemcontext.IncidentContext) string {
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
- Identify PRIMARY root causes — failures that have distinct, independent origins.
- Distinguish root causes from cascading effects. Do NOT list downstream symptoms as separate faults.
  Example: if a node goes NotReady, pod failures and CNI errors on that node are cascading effects of that one root cause, not independent faults.
- A second root cause is only valid if its origin is clearly unrelated to the first (different component, different failure mode, different evidence trail).
- If all symptoms trace back to a single origin, report only one root cause.
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
1. Primary Root Cause(s) — one entry per independent origin; if all symptoms share one origin, list only one
2. Cascading Effects — downstream symptoms per root cause (brief list, not expanded into sub-faults)
3. Impacted Services — which services/pods are affected and how
4. Scope Assessment — blast radius per root cause
5. Remediation Steps — address each root cause independently, in priority order
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

	return base
}

// BuildPrompt is the original behavior: base prompt + the raw collect-all
// evidence dump. Retained as the fallback path when the Evidence-First service
// is unavailable.
func BuildPrompt(labels, annotations map[string]string, startsAt string, ctx *systemcontext.IncidentContext) string {
	base := buildBase(labels, annotations, startsAt, ctx)
	if ctx != nil && len(ctx.RawEvidence) > 0 {
		base = appendEvidence(base, ctx.RawEvidence)
	}
	return base
}

// BuildPromptWithDistilledEvidence uses the Evidence-First Retrieval Pipeline's
// distilled block in place of the raw evidence dump. If distilled is empty it
// falls back to the raw dump so a retrieval failure never yields an
// evidence-less prompt.
func BuildPromptWithDistilledEvidence(labels, annotations map[string]string, startsAt string, ctx *systemcontext.IncidentContext, distilled string) string {
	base := buildBase(labels, annotations, startsAt, ctx)
	if strings.TrimSpace(distilled) != "" {
		return base + "\n\n" + distilled
	}
	if ctx != nil && len(ctx.RawEvidence) > 0 {
		return appendEvidence(base, ctx.RawEvidence)
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
