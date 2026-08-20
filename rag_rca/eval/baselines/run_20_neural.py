# eval/baselines/run_20_neural.py
"""C2 system improvement — re-run yours_distilled on the 20 real incidents with the
NEURAL cross-encoder reranker (bge-reranker-v2-m3) instead of the lexical fallback.

The original 20-incident distilled runs used Reranker(use_model=False) (lexical), which
let distractor lines (e.g. an OCI StartError, chaos apply-failures) outrank the decisive
root-cause line (OOMKilled/137, injected 300ms delay) — costing 2 incidents vs raw. The
neural reranker (validated in C6: compression recovered 93.5% vs raw 87%) ranks decisive
lines higher. This measures the upgrade honestly on ALL 20 (not just the 2 failures).

Env: RCA_AGENT_URL, JUDGE_AGENT_URL
    python baselines/run_20_neural.py --out results_20_neural.json
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from baselines.dataset_loader import load_incidents
from baselines import llm_baselines as LB
from app.rerank.cross_encoder_reranker import Reranker
import judge as judgemod


def retry(fn, tries=3, label=""):
    last = None
    for t in range(tries):
        try:
            return fn()
        except Exception as e:
            last = e
            print(f"      retry {label} ({t+1}/{tries}): {str(e)[:90]}", flush=True)
            time.sleep(3 * (t + 1))
    raise last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                                  "results_20_neural.json"))
    args = ap.parse_args()

    print("loading NEURAL reranker (bge-reranker-v2-m3)...", flush=True)
    neural = Reranker(use_model=True)
    if neural._model is None:
        print("!! neural reranker failed to load"); sys.exit(2)
    LB._RERANKER = neural   # inject: run_yours_distilled uses module _RERANKER
    print(f"neural reranker ready: {neural.model_name}\n", flush=True)

    incidents = load_incidents()
    results = []
    for iid, bundle, gt, meta in incidents:
        print(f"=== {iid} (culprit={gt['primary'][0][0]}) ===", flush=True)
        row = {"incident": iid, "system": "yours_distilled_neural", "category": gt["category"],
               "fault_type": gt["fault_type"], "culprit": gt["primary"][0][0]}
        try:
            r = retry(lambda: LB.run_yours_distilled(bundle), label="dist-neural")
            jr = retry(lambda: judgemod.judge(r["text"], gt), label="judge")
            row.update({"latency_s": r["latency_s"], "prompt_tokens": r["prompt_tokens"],
                        "recall": jr["judge_recall"], "precision": jr["judge_precision"],
                        "tp": jr["judge_tp"], "misattr": jr["judge_misattributed"],
                        "hit": jr["judge_recall"] >= 0.999, "response": r["text"]})
            print(f"  hit={row['hit']} recall={jr['judge_recall']} tok={r['prompt_tokens']} "
                  f"{r['latency_s']}s", flush=True)
        except Exception as e:
            row.update({"error": str(e)[:200], "recall": None})
            print(f"  ! FAILED: {str(e)[:140]}", flush=True)
        results.append(row)
        json.dump(results, open(args.out, "w"), indent=2)

    ok = [r for r in results if r.get("recall") is not None]
    hits = sum(1 for r in ok if r.get("hit"))
    micro = 100.0 * sum(r["tp"] for r in ok) / max(1, sum(1 for r in ok))  # single-fault -> tp∈{0,1}
    print("\n" + "=" * 66)
    print(f"NEURAL distilled — {hits}/{len(ok)} strict hits, micro-recall {micro:.1f}%")
    print("  (compare: old lexical distilled 85%, raw 90%)")
    # show which of the 2 known failures flipped
    for r in ok:
        if r["incident"] in ("edge1-oomkilled-memory-001", "edge1-pod-network-delay-001"):
            print(f"  {r['incident']:34s} hit={r['hit']} (was MISS with lexical)")
    print("=" * 66)


if __name__ == "__main__":
    main()
