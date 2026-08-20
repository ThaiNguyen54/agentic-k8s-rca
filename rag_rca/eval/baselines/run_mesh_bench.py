# eval/baselines/run_mesh_bench.py
"""Mesh bench: inject each MicroRCA-appropriate fault into Online Boutique, run
MicroRCA on the live Istio+Prometheus telemetry, and capture a matching evidence
bundle (k8s state + the same telemetry rendered as text) for the LLM systems.

Per fault:
  baseline window -> inject chaos -> anomaly window -> snapshot metrics+evidence
  -> run MicroRCA -> delete chaos -> recover.

Saves eval/mesh_bench.json (MicroRCA rankings + ground truth) and one evidence
bundle per incident under eval/mesh_evidence/<name>.json for the LLM stage.

Requires: KUBECONFIG set, edge1 context, this host able to port-forward.
Run:  python baselines/run_mesh_bench.py --baseline 120 --anomaly 210 --recover 75
"""
import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))  # eval/
from baselines import mesh_faults as mf
from baselines.prom import Prom, call_graph, edge_latency_series, resource_series
from baselines import microrca

CTX = os.getenv("KCTX", "edge1")
NS = mf.NS
EVID_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "mesh_evidence")


def kubectl(*args, check=True, timeout=60):
    cmd = ["kubectl", "--context", CTX, *args]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)}: {r.stderr[:300]}")
    return r.stdout


def apply_chaos(fault):
    man = json.dumps(mf.manifest(fault))
    p = subprocess.run(["kubectl", "--context", CTX, "apply", "-f", "-"],
                       input=man, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"apply chaos: {p.stderr[:300]}")


def delete_chaos(fault):
    subprocess.run(["kubectl", "--context", CTX, "delete", fault["kind"].lower(),
                    fault["name"], "-n", NS, "--ignore-not-found"],
                   capture_output=True, text=True)


def start_port_forward():
    pf = subprocess.Popen(
        ["kubectl", "--context", CTX, "-n", "monitoring", "port-forward",
         "svc/prometheus-kube-prometheus-prometheus", "9090:9090"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(6)
    return pf


def capture_evidence(prom, name, culprit, mechanism, start, end):
    """k8s state + the mesh telemetry rendered as text (fair signal for the LLM)."""
    os.makedirs(EVID_DIR, exist_ok=True)
    ev = {"name": name, "culprit": culprit, "mechanism": mechanism,
          "namespace": NS, "window": [start, end]}
    # k8s state
    ev["pods"] = kubectl("get", "pods", "-n", NS, "-o", "wide", check=False)
    ev["events"] = kubectl("get", "events", "-n", NS, "--sort-by=.lastTimestamp", check=False)
    ev["top_pods"] = kubectl("top", "pods", "-n", NS, check=False)
    # telemetry as text: service graph + per-edge latency delta
    edges = call_graph(prom, NS, start, end)
    lat = edge_latency_series(prom, NS, start, end)
    graph_lines, lat_lines = [], []
    for (s, d), rate in sorted(edges.items(), key=lambda kv: -kv[1]):
        graph_lines.append(f"{s} -> {d}   ({rate:.2f} req/s)")
    for (s, d), series in lat.items():
        vals = [v for _, v in series]
        if len(vals) >= 5:
            n0 = max(2, int(len(vals) * 0.4))
            base = sum(vals[:n0]) / n0
            anom = sum(vals[n0:]) / max(1, len(vals[n0:]))
            lat_lines.append((anom - base, f"{s} -> {d}: p50 {base:.0f}ms -> {anom:.0f}ms"))
    lat_lines = [x[1] for x in sorted(lat_lines, reverse=True)]
    ev["service_graph"] = "\n".join(graph_lines)
    ev["latency_shift"] = "\n".join(lat_lines)
    json.dump(ev, open(os.path.join(EVID_DIR, f"{name}.json"), "w"), indent=2)
    return ev, edges, lat


def run_microrca(prom, edges, lat, start, end):
    res = resource_series(prom, NS, start, end)
    ranked = microrca.localize(edges, lat, res, mode="mesh")
    return ranked


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", type=int, default=120)
    ap.add_argument("--anomaly", type=int, default=210)
    ap.add_argument("--recover", type=int, default=75)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.dirname(__file__)), "mesh_bench.json"))
    args = ap.parse_args()

    pf = start_port_forward()
    prom = Prom("http://127.0.0.1:9090")
    if not prom.up():
        print("! Prometheus not reachable via port-forward", flush=True)
        pf.terminate(); sys.exit(1)

    results = []
    try:
        for fault in mf.FAULTS:
            name, culprit = fault["name"], fault["culprit"]
            print(f"\n=== {name}  (culprit={culprit}, {fault['mechanism']}) ===", flush=True)
            delete_chaos(fault)  # clean any stale CR
            base_start = int(time.time())
            print(f"  baseline {args.baseline}s ...", flush=True); time.sleep(args.baseline)
            print("  injecting chaos", flush=True); apply_chaos(fault)
            inject_t = int(time.time())
            print(f"  anomaly {args.anomaly}s ...", flush=True); time.sleep(args.anomaly)
            end = int(time.time())
            try:
                ev, edges, lat = capture_evidence(prom, name, culprit, fault["mechanism"], base_start, end)
                ranked = run_microrca(prom, edges, lat, base_start, end)
            except Exception as e:
                print(f"  ! capture/microrca failed: {e}", flush=True)
                ranked = []
            top = [s for s, _ in ranked[:5]]
            hit1 = bool(ranked and ranked[0][0] == culprit)
            hit3 = culprit in top[:3]
            print(f"  MicroRCA top-5: {top}   hit@1={hit1} hit@3={hit3}", flush=True)
            results.append({"name": name, "culprit": culprit, "mechanism": fault["mechanism"],
                            "ranking": [(s, round(v, 5)) for s, v in ranked[:8]],
                            "top5": top, "hit@1": hit1, "hit@3": hit3,
                            "window": [base_start, end], "inject_t": inject_t})
            json.dump(results, open(args.out, "w"), indent=2)
            print("  deleting chaos + recovering", flush=True)
            delete_chaos(fault); time.sleep(args.recover)
    finally:
        for f in mf.FAULTS:
            delete_chaos(f)
        pf.terminate()

    n = len(results)
    h1 = sum(r["hit@1"] for r in results); h3 = sum(r["hit@3"] for r in results)
    print("\n" + "=" * 70)
    print(f"MICRORCA MESH BENCH — {n} faults")
    print(f"  top-1 accuracy: {h1}/{n} = {100*h1/n:.0f}%")
    print(f"  top-3 accuracy: {h3}/{n} = {100*h3/n:.0f}%")
    print("=" * 70)


if __name__ == "__main__":
    main()
