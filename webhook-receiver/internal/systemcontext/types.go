package systemcontext

type IncidentEvent struct {
	IncidentID    string
	DetectedAt    string
	Cluster       string
	Namespace     string
	FaultType     string
	FaultObject   string
	RootCauseHint string
	Labels        map[string]string
	Annotations   map[string]string
}

type IncidentContext struct {
	ClusterContext     string
	IncidentScope      IncidentScope
	AffectedNode       *NodeContext
	ServiceProfile     *ServiceProfile
	SystemTopology     *TopologyContext
	TelemetryEndpoints TelemetryContext
	CRDRegistry        []CRDInfo
	Summary            string
	Warnings           []string
}

type IncidentScope struct {
	Level            string
	BlastRadius      string
	ImpactedKinds    []string
	ImpactedServices []string
}

type NodeContext struct {
	Name       string
	InternalIP string
	Roles      []string
	Zone       string
	ProviderID string
	Labels     map[string]string
}

type ServiceProfile struct {
	Name         string
	Namespace    string
	ServiceType  string
	Ports        []string
	Selector     map[string]string
	Owner        string
	Tier         string
	RunbookURL   string
	SLO          string
	Dependencies []string
}

type TopologyContext struct {
	EntryPoints     []string
	Upstreams       []string
	Downstreams     []string
	ServicePathHint []string
}

type TelemetryContext struct {
	Prometheus string
	Loki       string
	Tempo      string
	Grafana    string
	Otel       string
}

type CRDInfo struct {
	Group   string
	Version string
	Kind    string
}
