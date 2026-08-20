# eval/baselines/run_20_microrca.py
"""Degraded (mesh-free) MicroRCA on the 20 real incidents - the COVERAGE bench.

MicroRCA needs a service call graph + response-time anomalies. The 20 incidents
have neither a mesh nor (in most cases) a latency signal, so we run the degraded
resource-only variant: topology-free, ranking workloads by container CPU/mem/net
anomaly. This is explicitly NOT the published MicroRCA; it exists to quantify how
much of a real K8s fault spectrum a metric-based localiser can even address.

For 13 Deployment faults we inject live and capture real metrics. For 7 faults
the culprit provably emits NO resource signal (quota-rejected pods never start;
zero-replicas has no pods; service-routing/network-latency faults leave a
healthy-resource pod), so MicroRCA is structurally blind - recorded as misses
with the reason, not injected (injection would not change the outcome).

Run: python baselines/run_20_microrca.py
"""
import json
import os
import subprocess
import sys
import time

import yaml

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from baselines.prom import Prom, resource_series
from baselines import microrca
from baselines.dataset_loader import load_incidents

CTX = os.getenv("KCTX", "edge1")
DATASET = "/home/ubuntu/thai/dataset"
OUT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "results_20_microrca.json")

# structurally-blind incidents (no resource anomaly possible) + reason
STRUCTURAL_MISS = {
    "edge1-cpu-quota-001": "ResourceQuota rejects pod creation - culprit never runs, no container metrics",
    "edge1-memory-quota-001": "ResourceQuota rejects pod creation - culprit never runs, no container metrics",
    "edge1-pod-quota-001": "ResourceQuota rejects pod creation - culprit never runs, no container metrics",
    "edge1-deployment-zero-replicas-001": "workload scaled to 0 replicas - no pods, no metrics",
    "edge1-service-selector-mismatch-001": "Service-routing fault - endpoints empty, no resource anomaly on any pod",
    "edge1-service-port-mapping-mismatch-001": "Service-routing fault - wrong targetPort, no resource anomaly on any pod",
    "edge1-pod-network-delay-001": "network-latency fault - resource-only MicroRCA has no latency signal without a mesh",
}


def kubectl(*args, check=True, timeout=90, stdin=None):
    r = subprocess.run(["kubectl", "--context", CTX, *args],
                       input=stdin, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)}: {r.stderr[:200]}")
    return r.stdout


def workload_name(fault_yaml_path):
    """Deployment name from the incident's fault.yaml (the culprit workload)."""
    try:
        for doc in yaml.safe_load_all(open(fault_yaml_path)):
            if doc and doc.get("kind") == "Deployment":
                return doc["metadata"]["name"]
    except Exception:
        pass
    return None


def start_pf():
    pf = subprocess.Popen(["kubectl", "--context", CTX, "-n", "monitoring", "port-forward",
                           "svc/prometheus-kube-prometheus-prometheus", "9090:9090"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(6)
    return pf


def main():
    baseline, anomaly, recover = 60, 150, 35
    incs = {iid: (b, gt, meta) for iid, b, gt, meta in load_incidents()}
    pf = start_pf()
    prom = Prom("http://127.0.0.1:9090")
    if not prom.up():
        print("! Prometheus unreachable"); pf.terminate(); sys.exit(1)

    results = []
    try:
        for iid, (bundle, gt, meta) in incs.items():
            ns = meta["namespace"]
            culprit = gt["primary"][0][0]
            row = {"incident": iid, "culprit": culprit, "namespace": ns,
                   "category": gt["category"], "fault_type": gt["fault_type"]}

            if iid in STRUCTURAL_MISS:
                row.update({"live": False, "hit@1": False, "hit@3": False,
                            "signal": False, "reason": STRUCTURAL_MISS[iid], "ranking": []})
                print(f"{iid:44s} STRUCTURAL MISS - {STRUCTURAL_MISS[iid][:60]}", flush=True)
                results.append(row); json.dump(results, open(OUT, "w"), indent=2); continue

            fyaml = os.path.join(DATASET, iid, "fault.yaml")
            wl = workload_name(fyaml)
            print(f"\n=== {iid} (culprit workload={wl}, ns={ns}) ===", flush=True)
            try:
                base_start = int(time.time())
                print(f"  baseline {baseline}s ...", flush=True); time.sleep(baseline)
                kubectl("apply", "-n", ns, "-f", fyaml)
                print(f"  injected; anomaly {anomaly}s ...", flush=True); time.sleep(anomaly)
                end = int(time.time())
                res = resource_series(prom, ns, base_start, end)
                ranked = microrca.localize(None, None, res, mode="resource", topology={})
                top = [s for s, _ in ranked[:5]]
                match = wl or culprit
                hit1 = bool(ranked and match and match in ranked[0][0])
                hit3 = any(match and match in s for s in top[:3]) if match else False
                row.update({"live": True, "signal": bool(ranked),
                            "hit@1": hit1, "hit@3": hit3, "top5": top,
                            "ranking": [(s, round(v, 4)) for s, v in ranked[:6]]})
                print(f"  MicroRCA top-5: {top}  hit@1={hit1} hit@3={hit3} signal={bool(ranked)}", flush=True)
            except Exception as e:
                row.update({"live": True, "error": str(e)[:200], "hit@1": False, "hit@3": False, "signal": False})
                print(f"  ! FAILED: {str(e)[:150]}", flush=True)
            finally:
                kubectl("delete", "-n", ns, "-f", fyaml, "--ignore-not-found", check=False)
                time.sleep(recover)
            results.append(row); json.dump(results, open(OUT, "w"), indent=2)
    finally:
        pf.terminate()

    n = len(results)
    h1 = sum(r.get("hit@1") for r in results); h3 = sum(r.get("hit@3") for r in results)
    live = sum(1 for r in results if r.get("live"))
    print("\n" + "=" * 74)
    print(f"DEGRADED MICRORCA — 20 real incidents  ({live} injected live, {n-live} structural)")
    print(f"  top-1: {h1}/{n} = {100*h1/n:.0f}%   top-3: {h3}/{n} = {100*h3/n:.0f}%")
    print("  (coverage baseline: metric-only localisation vs a broad K8s fault spectrum)")
    print("=" * 74)


if __name__ == "__main__":
    main()
