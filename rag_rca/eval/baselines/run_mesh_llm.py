# eval/baselines/run_mesh_llm.py
"""Head-to-head on the mesh bench: run the LLM systems (yours raw/distilled,
RCACopilot-style, RCAgent-style) on the SAME 6 mesh incidents MicroRCA saw, and
judge localisation of the culprit service. Combined with mesh_bench.json
(MicroRCA), this gives the faithful MicroRCA-vs-your-system comparison.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.schemas import EvidenceBundle, FaultSignature
from baselines import llm_baselines as LB
import judge as judgemod

EVID_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "mesh_evidence")
OUT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "mesh_llm_results.json")

MECH_TEXT = {
    "network_latency": "A network-latency fault was injected into {c}, inflating its response time; "
                       "the elevated latency propagates upstream through the call graph. Root cause: {c}.",
    "cpu_stress": "A CPU-stress fault was injected into {c}, saturating its CPU and degrading it. Root cause: {c}.",
}
KWS = {"network_latency": ["latency", "network", "delay", "response time", "slow"],
       "cpu_stress": ["cpu", "saturation", "throttl", "overload", "high cpu"]}


def to_bundle(ev):
    sources = {
        "events.txt": ev.get("events", ""),
        "pods.txt": ev.get("pods", ""),
        "top_pods.txt": ev.get("top_pods", ""),
        "service_graph.txt": ev.get("service_graph", ""),
        "latency_shift.txt": "Per-edge p50 latency, baseline -> anomaly:\n" + ev.get("latency_shift", ""),
    }
    sig = FaultSignature(cluster="edge1", namespace=ev["namespace"],
                         fault_type=ev["mechanism"], fault_object=f"service/{ev['culprit']}",
                         summary=f"[edge1] performance incident in {ev['namespace']}: elevated latency")
    return EvidenceBundle(fault=sig, sources={k: v for k, v in sources.items() if v},
                          incident_id=ev["name"])


def main():
    files = sorted(f for f in os.listdir(EVID_DIR) if f.endswith(".json"))
    incidents = []
    for f in files:
        ev = json.load(open(os.path.join(EVID_DIR, f)))
        b = to_bundle(ev)
        gt = {"primary": [(ev["culprit"], KWS.get(ev["mechanism"], []))],
              "distractors": ["frontend", "loadgenerator"],
              "category": "Performance", "fault_type": ev["mechanism"],
              "gold_text": MECH_TEXT[ev["mechanism"]].format(c=ev["culprit"])}
        incidents.append((ev["name"], b, gt, ev["culprit"]))

    corpus = [(n, b, gt) for n, b, gt, _ in incidents]
    systems = ["yours_distilled", "yours_raw", "rcacopilot", "rcagent"]
    results = []
    for name, bundle, gt, culprit in incidents:
        print(f"\n=== {name} (culprit={culprit}) ===", flush=True)
        for sysname in systems:
            print(f"  {sysname} ...", flush=True)
            row = {"incident": name, "system": sysname, "culprit": culprit}
            try:
                if sysname == "yours_raw":
                    r = LB.run_yours_raw(bundle)
                elif sysname == "yours_distilled":
                    r = LB.run_yours_distilled(bundle)
                elif sysname == "rcacopilot":
                    r = LB.run_rcacopilot(name, bundle, corpus, k=2)
                else:
                    r = LB.run_rcagent(bundle, n=3)
                jr = judgemod.judge(r["text"], gt)
                row.update({"latency_s": r["latency_s"], "prompt_tokens": r["prompt_tokens"],
                            "recall": jr["judge_recall"], "precision": jr["judge_precision"],
                            "tp": jr["judge_tp"], "hit": jr["judge_recall"] >= 0.999,
                            "response": r["text"]})
                print(f"    hit={row['hit']} recall={jr['judge_recall']} tok={r['prompt_tokens']} {r['latency_s']}s")
            except Exception as e:
                row.update({"error": str(e)[:200], "recall": None})
                print(f"    ! FAILED: {str(e)[:140]}")
            results.append(row)
            json.dump(results, open(OUT, "w"), indent=2)

    print("\n" + "=" * 74)
    print("MESH BENCH — localisation accuracy (culprit service identified)")
    print("=" * 74)
    for sysname in systems:
        rows = [r for r in results if r["system"] == sysname and r.get("recall") is not None]
        if rows:
            acc = 100 * sum(r["hit"] for r in rows) / len(rows)
            tok = sum(r["prompt_tokens"] for r in rows) / len(rows)
            lat = sum(r["latency_s"] for r in rows) / len(rows)
            print(f"  {sysname:18s} acc={acc:5.0f}%  mean_tok={tok:8.0f}  mean_lat={lat:5.1f}s  (n={len(rows)})")
    print(f"  {'MicroRCA (top-1)':18s} acc=   33%   (see mesh_bench.json; top-3=83%)")
    print("=" * 74)


if __name__ == "__main__":
    main()
