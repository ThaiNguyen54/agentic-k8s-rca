package systemcontext

import (
	"fmt"
	"strings"
)

func Summarize(in *IncidentContext) string {
	if in == nil {
		return ""
	}

	var parts []string

	if in.ClusterContext != "" {
		parts = append(parts, fmt.Sprintf("cluster context=%s", in.ClusterContext))
	}
	if in.IncidentScope.Level != "" {
		parts = append(parts, fmt.Sprintf("scope=%s", in.IncidentScope.Level))
	}
	if in.IncidentScope.BlastRadius != "" {
		parts = append(parts, fmt.Sprintf("blast-radius=%s", in.IncidentScope.BlastRadius))
	}
	if in.AffectedNode != nil && in.AffectedNode.Name != "" {
		parts = append(parts, fmt.Sprintf("node=%s", in.AffectedNode.Name))
	}
	if in.ServiceProfile != nil && in.ServiceProfile.Name != "" {
		parts = append(parts, fmt.Sprintf("service=%s/%s", in.ServiceProfile.Namespace, in.ServiceProfile.Name))
	}
	if in.SystemTopology != nil && len(in.SystemTopology.ServicePathHint) > 0 {
		parts = append(parts, "path="+strings.Join(in.SystemTopology.ServicePathHint, " -> "))
	}
	if in.TelemetryEndpoints.Grafana != "" || in.TelemetryEndpoints.Prometheus != "" {
		var telemetry []string
		if in.TelemetryEndpoints.Prometheus != "" {
			telemetry = append(telemetry, "prometheus")
		}
		if in.TelemetryEndpoints.Loki != "" {
			telemetry = append(telemetry, "loki")
		}
		if in.TelemetryEndpoints.Tempo != "" {
			telemetry = append(telemetry, "tempo")
		}
		if in.TelemetryEndpoints.Grafana != "" {
			telemetry = append(telemetry, "grafana")
		}
		if in.TelemetryEndpoints.Otel != "" {
			telemetry = append(telemetry, "otel")
		}
		parts = append(parts, "telemetry="+strings.Join(telemetry, ","))
	}
	if len(in.CRDRegistry) > 0 {
		var kinds []string
		for _, crd := range in.CRDRegistry {
			if crd.Kind != "" {
				kinds = append(kinds, crd.Kind)
			}
		}
		if len(kinds) > 0 {
			parts = append(parts, "crds="+strings.Join(uniqueStrings(kinds), ","))
		}
	}
	if len(in.Warnings) > 0 {
		parts = append(parts, fmt.Sprintf("warnings=%d", len(in.Warnings)))
	}

	return strings.Join(parts, " | ")
}
