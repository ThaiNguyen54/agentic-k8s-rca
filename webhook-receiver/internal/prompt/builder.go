// internal/prompt/builder.go
package prompt

import (
	"fmt"
	"strings"

	"github.com/ThaiNguyen54/webhook-receiver/internal/systemcontext"
)

func BuildPrompt(labels, annotations map[string]string, startsAt string, ctx *systemcontext.IncidentContext) string {
	cluster := labels["cluster"]
	namespace := labels["namespace"]
	faultType := labels["fault_type"]
	faultObj := labels["fault_object"]
	hint := labels["root_cause_hint"]
	summary := annotations["summary"]
	detail := annotations["detail"]

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

	return fmt.Sprintf(`You are a Kubernetes SRE agent performing autonomous root cause analysis.

Rules:
- Use all available tools to gather live evidence before concluding.
- Treat the context snapshot as guidance, not proof.
- Verify all hypotheses with live cluster data before concluding.
- Do not ask for approval. Proceed autonomously.

## Incident Report
Detected At: %s
Cluster: %s
Kubeconfig Context: %s
Namespace: %s
Fault Type: %s
// Affected Object: %s
// Root Cause Hint: %s
Summary: %s

## Fault Detail
%s

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
1. Root Cause
2. Impacted Path
3. Scope Assessment
4. Evidence
5. Remediation Steps
6. Prevention
7. Confidence Level`,
		startsAt,
		cluster,
		cluster,
		namespace,
		faultType,
		faultObj,
		hint,
		summary,
		detail,
		scope,
		node,
		topology,
		serviceProfile,
		telemetry,
		crds,
		contextSummary,
	)
}

// package prompt

// import "fmt"

// // BuildPrompt constructs the incident prompt for the k8s-agent RCA invocation.
// // Key rules:
// //   - Include "Kubeconfig Context" so the agent picks the right cluster context
// //     from /tmp/multi-cluster.kubeconfig (e.g. "edge1", not "default")
// //   - Tell the agent to act autonomously — do not ask for approval
// //   - Use all available tools to gather live evidence before concluding
// func BuildPrompt(labels, annotations map[string]string, startsAt string) string {
// 	cluster := labels["cluster"]
// 	namespace := labels["namespace"]
// 	faultType := labels["fault_type"]
// 	faultObj := labels["fault_object"]
// 	hint := labels["root_cause_hint"]
// 	summary := annotations["summary"]
// 	detail := annotations["detail"]

// 	return fmt.Sprintf(
// 		`You are a Kubernetes SRE agent. Analyze the following incident and produce a root cause analysis with remediation steps.

// Use all available tools to gather live evidence from the cluster before concluding.
// Do not ask for approval. Proceed autonomously.

// ## Incident Report

// **Detected At**:        %s
// **Cluster**:            %s
// **Kubeconfig Context**: %s
// **Namespace**:          %s
// **Fault Type**:         %s
// **Affected Object**:    %s
// **Root Cause Hint**:    %s
// **Summary**:            %s

// ## Fault Detail
// %s

// ## Expected Output

// Provide a structured response with:
// 1. **Root Cause**: precise technical explanation confirmed by live tool evidence
// 2. **Impacted Path**: which hop failed (Client → Ingress → Service → Pod)
// 3. **Evidence**: what the tools returned that confirms the root cause
// 4. **Remediation Steps**: exact kubectl commands to fix the issue
// 5. **Prevention**: one architectural recommendation to avoid recurrence`,
// 		startsAt,
// 		cluster,
// 		cluster, // context name matches cluster name in multi-cluster.kubeconfig
// 		namespace,
// 		faultType,
// 		faultObj,
// 		hint,
// 		summary,
// 		detail,
// 	)
// }
