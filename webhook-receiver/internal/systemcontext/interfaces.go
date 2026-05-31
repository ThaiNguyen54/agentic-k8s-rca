package systemcontext

import "context"

type Manager interface {
	BuildContext(ctx context.Context, in IncidentEvent) (*IncidentContext, error)
}

type KubernetesProvider interface {
	ResolveAffectedNode(ctx context.Context, in IncidentEvent) (*NodeContext, error)
	BuildServiceProfile(ctx context.Context, in IncidentEvent) (*ServiceProfile, error)
	BuildTopology(ctx context.Context, in IncidentEvent) (*TopologyContext, error)
	ListRelevantCRDs(ctx context.Context, cluster string, in IncidentEvent) ([]CRDInfo, error)
}

type TelemetryProvider interface {
	GetEndpoints(ctx context.Context, cluster string) (TelemetryContext, error)
}

type EvidenceCollector interface {
	CollectEvidence(ctx context.Context, in IncidentEvent) (map[string]string, error)
}
