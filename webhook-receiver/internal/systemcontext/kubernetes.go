package systemcontext

import (
	"context"
	"errors"
	"strings"
)

type StaticKubernetesProvider struct {
	Catalog ClusterCatalog
}

type ClusterCatalog struct {
	Clusters map[string]ClusterInfo
}

type ClusterInfo struct {
	NodeIPs    map[string]string
	NodeRoles  map[string][]string
	NodeZones  map[string]string
	NodeLabels map[string]map[string]string

	Services   map[string]ServiceProfile
	Topologies map[string]TopologyContext
	CRDs       []CRDInfo
}

func NewStaticKubernetesProvider(catalog ClusterCatalog) *StaticKubernetesProvider {
	return &StaticKubernetesProvider{Catalog: catalog}
}

func (p *StaticKubernetesProvider) ResolveAffectedNode(_ context.Context, in IncidentEvent) (*NodeContext, error) {
	clusterInfo, ok := p.Catalog.Clusters[in.Cluster]
	if !ok {
		return inferNodeFromIncident(in), nil
	}

	nodeName := firstNonEmpty(
		in.Labels["node"],
		in.Labels["kubernetes_node"],
		in.Labels["incident_node"],
	)

	if nodeName == "" && strings.Contains(strings.ToLower(in.FaultType), "node") {
		nodeName = strings.TrimSpace(in.FaultObject)
	}

	if nodeName == "" {
		return nil, nil
	}

	node := &NodeContext{
		Name:       nodeName,
		InternalIP: clusterInfo.NodeIPs[nodeName],
		Roles:      clusterInfo.NodeRoles[nodeName],
		Zone:       clusterInfo.NodeZones[nodeName],
		Labels:     clusterInfo.NodeLabels[nodeName],
	}

	return node, nil
}

func (p *StaticKubernetesProvider) BuildServiceProfile(_ context.Context, in IncidentEvent) (*ServiceProfile, error) {
	clusterInfo, ok := p.Catalog.Clusters[in.Cluster]
	if !ok {
		return inferServiceProfile(in), nil
	}

	ns := firstNonEmpty(in.Namespace, in.Labels["namespace"], "default")
	serviceName := inferServiceName(in)
	if serviceName == "" {
		return nil, nil
	}

	key := namespacedKey(ns, serviceName)
	if svc, ok := clusterInfo.Services[key]; ok {
		cp := svc
		if cp.Namespace == "" {
			cp.Namespace = ns
		}
		if cp.Name == "" {
			cp.Name = serviceName
		}
		return &cp, nil
	}

	return &ServiceProfile{
		Name:      serviceName,
		Namespace: ns,
	}, nil
}

func (p *StaticKubernetesProvider) BuildTopology(_ context.Context, in IncidentEvent) (*TopologyContext, error) {
	clusterInfo, ok := p.Catalog.Clusters[in.Cluster]
	if !ok {
		return inferTopology(in), nil
	}

	ns := firstNonEmpty(in.Namespace, in.Labels["namespace"], "default")
	serviceName := inferServiceName(in)
	if serviceName != "" {
		key := namespacedKey(ns, serviceName)
		if topo, ok := clusterInfo.Topologies[key]; ok {
			cp := topo
			return &cp, nil
		}
	}

	if ing := strings.TrimSpace(in.Labels["ingress"]); ing != "" {
		key := namespacedKey(ns, ing)
		if topo, ok := clusterInfo.Topologies[key]; ok {
			cp := topo
			return &cp, nil
		}
	}

	return inferTopology(in), nil
}

func (p *StaticKubernetesProvider) ListRelevantCRDs(_ context.Context, cluster string, in IncidentEvent) ([]CRDInfo, error) {
	clusterInfo, ok := p.Catalog.Clusters[cluster]
	if !ok {
		return nil, nil
	}
	if len(clusterInfo.CRDs) == 0 {
		return nil, nil
	}

	keywords := strings.Fields(strings.ToLower(strings.Join([]string{
		in.FaultType,
		in.FaultObject,
		in.RootCauseHint,
		in.Annotations["summary"],
		in.Annotations["detail"],
	}, " ")))

	if len(keywords) == 0 {
		return clusterInfo.CRDs, nil
	}

	var matched []CRDInfo
	for _, crd := range clusterInfo.CRDs {
		hay := strings.ToLower(crd.Group + " " + crd.Version + " " + crd.Kind)
		for _, kw := range keywords {
			if kw == "" {
				continue
			}
			if strings.Contains(hay, kw) {
				matched = append(matched, crd)
				break
			}
		}
	}

	if len(matched) == 0 {
		return clusterInfo.CRDs, nil
	}

	return matched, nil
}

func inferNodeFromIncident(in IncidentEvent) *NodeContext {
	nodeName := firstNonEmpty(
		in.Labels["node"],
		in.Labels["kubernetes_node"],
		in.Labels["incident_node"],
	)
	if nodeName == "" && strings.Contains(strings.ToLower(in.FaultType), "node") {
		nodeName = strings.TrimSpace(in.FaultObject)
	}
	if nodeName == "" {
		return nil
	}
	return &NodeContext{Name: nodeName}
}

func inferServiceProfile(in IncidentEvent) *ServiceProfile {
	serviceName := inferServiceName(in)
	if serviceName == "" {
		return nil
	}
	ns := firstNonEmpty(in.Namespace, in.Labels["namespace"], "default")
	return &ServiceProfile{
		Name:      serviceName,
		Namespace: ns,
	}
}

func inferTopology(in IncidentEvent) *TopologyContext {
	ns := firstNonEmpty(in.Namespace, in.Labels["namespace"], "default")
	svc := inferServiceName(in)
	ing := strings.TrimSpace(in.Labels["ingress"])
	workload := firstNonEmpty(in.Labels["workload"], in.Labels["deployment"], in.Labels["pod"])
	node := firstNonEmpty(in.Labels["node"], in.Labels["incident_node"])

	var path []string
	if ing != "" {
		path = append(path, "Ingress/"+ns+"/"+ing)
	}
	if svc != "" {
		path = append(path, "Service/"+ns+"/"+svc)
	}
	if workload != "" {
		path = append(path, "Workload/"+ns+"/"+workload)
	}
	if node != "" {
		path = append(path, "Node/"+node)
	}

	if len(path) == 0 {
		path = append(path, "Namespace/"+ns)
	}

	return &TopologyContext{
		EntryPoints:     maybeOne(ing, "client"),
		Upstreams:       maybeOne("Client"),
		Downstreams:     maybeOne(workload, node),
		ServicePathHint: path,
	}
}

func inferServiceName(in IncidentEvent) string {
	ft := strings.ToLower(in.FaultType)
	obj := strings.TrimSpace(in.FaultObject)

	if svc := strings.TrimSpace(in.Labels["service"]); svc != "" {
		return svc
	}
	if svc := strings.TrimSpace(in.Labels["app"]); svc != "" {
		return svc
	}
	if strings.Contains(ft, "service") && obj != "" {
		return obj
	}
	if strings.Contains(ft, "endpoint") && obj != "" {
		return obj
	}
	return ""
}

func namespacedKey(namespace, name string) string {
	return strings.TrimSpace(namespace) + "/" + strings.TrimSpace(name)
}

func firstNonEmpty(values ...string) string {
	for _, v := range values {
		v = strings.TrimSpace(v)
		if v != "" {
			return v
		}
	}
	return ""
}

func maybeOne(values ...string) []string {
	var out []string
	for _, v := range values {
		v = strings.TrimSpace(v)
		if v != "" {
			out = append(out, v)
		}
	}
	return out
}

var _ KubernetesProvider = (*StaticKubernetesProvider)(nil)

func ValidateCatalog(c ClusterCatalog) error {
	if len(c.Clusters) == 0 {
		return errors.New("cluster catalog is empty")
	}
	return nil
}
