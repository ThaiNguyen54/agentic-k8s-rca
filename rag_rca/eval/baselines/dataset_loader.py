# eval/baselines/dataset_loader.py
"""Load the 20 real labeled incidents (dataset/incidents.csv + evidence dirs)
into EvidenceBundle + ground-truth, for the LLM-system and degraded-MicroRCA
comparison.
"""
import csv
import os

from app.schemas import EvidenceBundle, FaultSignature

DATASET = os.getenv("DATASET_DIR", "/home/ubuntu/thai/dataset")

# The actual failing workload/object per incident (the thing an RCA should name),
# distinct from incidents.csv target_name (which is the fault *parameter*).
CULPRIT = {
    "edge1-cpu-quota-001": "cpu-hog (ResourceQuota cpu-quota-small)",
    "edge1-memory-quota-001": "mem-hog (ResourceQuota mem-quota-small)",
    "edge1-pod-quota-001": "pod-hog (ResourceQuota pod-quota-small)",
    "edge1-missing-sa-001": "sa-hog",
    "edge1-node-selector-mismatch-001": "node-selector-hog",
    "edge1-node-affinity-mismatch-001": "node-affinity-hog",
    "edge1-taint-toleration-mismatch-001": "taint-hog",
    "edge1-cpu-capacity-mismatch-001": "cpu-capacity-hog",
    "edge1-incorrect-image-reference-001": "image-ref-hog",
    "edge1-missing-image-pull-secret-001": "private-image-hog",
    "edge1-missing-secret-binding-001": "secret-hog",
    "edge1-oomkilled-memory-001": "mem-oom-hog",
    "edge1-liveness-probe-incorrect-port-001": "probe-hog",
    "edge1-readiness-probe-incorrect-port-001": "readiness-hog",
    "edge1-deployment-zero-replicas-001": "db-hog",
    "edge1-service-selector-mismatch-001": "frontend Service",
    "edge1-service-port-mapping-mismatch-001": "frontend Service",
    "edge1-service-dns-resolution-failure-001": "dns-hog",
    "edge1-pod-cpu-overload-001": "stress-hog",
    "edge1-pod-network-delay-001": "frontend pod",
}

# evidence files worth loading into the bundle (kubectl-style RCA evidence)
EVID_FILES = ["events.txt", "pods.txt", "ns_pods.txt", "pod_describe.txt",
              "deploy_describe.txt", "deploy_get.txt", "svc.txt", "svc_describe.txt",
              "endpoints.txt", "frontend_endpoints.txt", "nodes.txt",
              "quota_describe.txt", "serviceaccounts.txt", "chaos_describe.txt",
              "dns_hog_logs.txt", "loadgen.log"]

# cause keywords per fault_type (for the string-match fallback; gpt-5 judge is authoritative)
CAUSE_KWS = {
    "NamespaceCPUQuotaExceeded": ["cpu quota", "resourcequota", "exceeded", "forbidden"],
    "NamespaceMemoryQuotaExceeded": ["memory quota", "resourcequota", "exceeded", "forbidden"],
    "NamespacePodQuotaExceeded": ["pod quota", "resourcequota", "exceeded", "forbidden"],
    "MissingServiceAccount": ["serviceaccount", "missing-sa", "not found"],
    "NodeSelectorMismatch": ["nodeselector", "unschedulable", "pending", "no node"],
    "NodeAffinityMismatch": ["node affinity", "unschedulable", "pending"],
    "TaintTolerationMismatch": ["taint", "toleration", "noschedule", "pending"],
    "CPUCapacityMismatch": ["insufficient cpu", "unschedulable", "pending", "4500m"],
    "IncorrectImageReference": ["errimagepull", "imagepullbackoff", "not found", "image"],
    "MissingImagePullSecret": ["imagepullbackoff", "errimagepull", "pull secret", "unauthorized"],
    "MissingSecretBinding": ["secret", "not found", "createcontainerconfig", "missing-secret"],
    "OOMKilled": ["oomkilled", "oom", "memory", "137", "limit"],
    "LivenessProbeIncorrectPort": ["liveness", "probe", "restart", "port"],
    "ReadinessProbeIncorrectPort": ["readiness", "probe", "not ready", "port"],
    "DeploymentZeroReplicas": ["replicas", "scaled", "zero", "0"],
    "ServiceSelectorMismatch": ["selector", "endpoints", "no endpoints", "does-not-exist"],
    "ServicePortMappingMismatch": ["targetport", "port", "9999", "connection refused"],
    "ServiceDNSResolutionFailure": ["dns", "resolution", "nxdomain", "nosuchsvc"],
    "PodCPUOverload": ["cpu", "overload", "throttl", "high cpu"],
    "PodNetworkDelay": ["network", "delay", "latency", "300ms", "networkchaos"],
}


def load_incidents(dataset_dir=DATASET):
    """Return list of (incident_id, EvidenceBundle, gt, meta)."""
    rows = list(csv.DictReader(open(os.path.join(dataset_dir, "incidents.csv"))))
    out = []
    for r in rows:
        iid = r["incident_id"]
        edir = r["evidence_dir"]
        if not os.path.isdir(edir):
            edir = os.path.join(dataset_dir, iid)
        sources = {}
        for fn in EVID_FILES:
            p = os.path.join(edir, fn)
            if os.path.isfile(p):
                txt = open(p, errors="replace").read().strip()
                if txt:
                    sources[fn] = txt
        ft = r["fault_type"]
        target = r["target_name"]
        kws = CAUSE_KWS.get(ft, [])
        # augment kws with gold_label tokens
        kws = list(dict.fromkeys(kws + r["gold_label"].split("_")))
        sig = FaultSignature(
            cluster=r["cluster"], namespace=r["namespace"],
            fault_type=ft, fault_object=f"{r['target_kind']}/{target}",
            root_cause_hint=r["gold_label"],
            summary=f"[{r['cluster']}] {r['category']} incident: {r['gold_label']}")
        bundle = EvidenceBundle(fault=sig, sources=sources, incident_id=iid)
        culprit = CULPRIT.get(iid, target)
        gt = {"primary": [(culprit, kws[:6])],
              "distractors": [],
              "gold_label": r["gold_label"], "gold_text": r["gold_text"],
              "category": r["category"], "fault_type": ft}
        out.append((iid, bundle, gt, r))
    return out


if __name__ == "__main__":
    incs = load_incidents()
    print(f"loaded {len(incs)} incidents")
    for iid, b, gt, r in incs:
        toks = sum(len(v.split()) for v in b.sources.values())
        print(f"  {iid:45s} src={len(b.sources):2d} ~{toks:5d}w  culprit={gt['primary'][0][0]}")
