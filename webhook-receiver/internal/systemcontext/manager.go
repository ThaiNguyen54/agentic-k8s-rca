package systemcontext

import (
	"context"
	"errors"
	"fmt"
	"strings"
)

type DefaultManager struct {
	K8s       KubernetesProvider
	Telemetry TelemetryProvider
}

func NewDefaultManager(k8s KubernetesProvider, telemetry TelemetryProvider) *DefaultManager {
	return &DefaultManager{
		K8s:       k8s,
		Telemetry: telemetry,
	}
}

func (m *DefaultManager) BuildContext(ctx context.Context, in IncidentEvent) (*IncidentContext, error) {
	if strings.TrimSpace(in.Cluster) == "" {
		return nil, errors.New("incident cluster is required")
	}

	out := &IncidentContext{
		ClusterContext: defaultClusterContext(in),
		IncidentScope:  classifyScope(in),
	}

	var warnings []string

	if m.K8s != nil {
		node, err := m.K8s.ResolveAffectedNode(ctx, in)
		if err != nil {
			warnings = append(warnings, fmt.Sprintf("resolve affected node: %v", err))
		} else {
			out.AffectedNode = node
		}

		service, err := m.K8s.BuildServiceProfile(ctx, in)
		if err != nil {
			warnings = append(warnings, fmt.Sprintf("build service profile: %v", err))
		} else {
			out.ServiceProfile = service
			if service != nil && service.Name != "" {
				out.IncidentScope.ImpactedServices = uniqueStrings(append(out.IncidentScope.ImpactedServices, service.Name))
			}
		}

		topology, err := m.K8s.BuildTopology(ctx, in)
		if err != nil {
			warnings = append(warnings, fmt.Sprintf("build topology: %v", err))
		} else {
			out.SystemTopology = topology
		}

		crds, err := m.K8s.ListRelevantCRDs(ctx, in.Cluster, in)
		if err != nil {
			warnings = append(warnings, fmt.Sprintf("list relevant CRDs: %v", err))
		} else {
			out.CRDRegistry = crds
		}
	}

	if m.Telemetry != nil {
		telemetry, err := m.Telemetry.GetEndpoints(ctx, in.Cluster)
		if err != nil {
			warnings = append(warnings, fmt.Sprintf("get telemetry endpoints: %v", err))
		} else {
			out.TelemetryEndpoints = telemetry
		}
	}

	out.Warnings = uniqueStrings(warnings)
	out.Summary = Summarize(out)
	return out, nil
}

func defaultClusterContext(in IncidentEvent) string {
	if v := strings.TrimSpace(in.Labels["cluster_context"]); v != "" {
		return v
	}
	return strings.TrimSpace(in.Cluster)
}

func classifyScope(in IncidentEvent) IncidentScope {
	ft := normalize(in.FaultType)
	obj := normalize(in.FaultObject)

	scope := IncidentScope{
		Level:         "namespace",
		BlastRadius:   "unknown",
		ImpactedKinds: []string{"Workload"},
	}

	switch {
	case strings.Contains(ft, "node"), strings.Contains(obj, "node"):
		scope.Level = "node"
		scope.BlastRadius = "single-node"
		scope.ImpactedKinds = []string{"Node", "Pod", "Service"}
	case strings.Contains(ft, "ingress"), strings.Contains(ft, "gateway"):
		scope.Level = "service-path"
		scope.BlastRadius = "north-south"
		scope.ImpactedKinds = []string{"Ingress", "Service", "Pod"}
	case strings.Contains(ft, "service"), strings.Contains(ft, "endpoint"):
		scope.Level = "service"
		scope.BlastRadius = "single-service"
		scope.ImpactedKinds = []string{"Service", "EndpointSlice", "Pod"}
	case strings.Contains(ft, "pod"), strings.Contains(ft, "crash"), strings.Contains(ft, "imagepull"):
		scope.Level = "workload"
		scope.BlastRadius = "single-workload"
		scope.ImpactedKinds = []string{"Pod", "ReplicaSet", "Deployment"}
	case strings.Contains(ft, "dns"), strings.Contains(ft, "cni"), strings.Contains(ft, "network"):
		scope.Level = "network"
		scope.BlastRadius = "cross-service"
		scope.ImpactedKinds = []string{"Service", "Pod", "Node"}
	case strings.Contains(ft, "control-plane"):
		scope.Level = "cluster"
		scope.BlastRadius = "cluster-wide"
		scope.ImpactedKinds = []string{"Cluster"}
	}

	if svc := strings.TrimSpace(in.Labels["service"]); svc != "" {
		scope.ImpactedServices = append(scope.ImpactedServices, svc)
	}

	return scope
}

func normalize(s string) string {
	return strings.ToLower(strings.TrimSpace(s))
}

func uniqueStrings(in []string) []string {
	seen := make(map[string]struct{}, len(in))
	out := make([]string, 0, len(in))
	for _, s := range in {
		s = strings.TrimSpace(s)
		if s == "" {
			continue
		}
		if _, ok := seen[s]; ok {
			continue
		}
		seen[s] = struct{}{}
		out = append(out, s)
	}
	return out
}
