# eval/baselines/run_20_llm.py
"""LLM-system comparison on the 20 real labeled incidents (dataset/).

Runs yours_raw, yours_distilled, RCACopilot-style, RCAgent-style on each
incident's captured-at-fault-time evidence, judged by the gpt-5 judge against the
gold root cause. No injection needed - the dataset evidence IS the fault-time
snapshot the thesis webhook would have collected.

Env: RCA_AGENT_URL, JUDGE_AGENT_URL   Run: python baselines/run_20_llm.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from baselines.dataset_loader import load_incidents
from baselines import llm_baselines as LB
import judge as judgemod

OUT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "results_20_llm.json")
SYSTEMS = ["yours_distilled", "yours_raw", "rcacopilot", "rcagent"]


def retry(fn, tries=3, label=""):
    import time
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
    incidents = load_incidents()
    corpus = [(iid, b, gt) for iid, b, gt, _ in incidents]
    results = []
    for iid, bundle, gt, meta in incidents:
        print(f"\n=== {iid} (culprit={gt['primary'][0][0]}, cat={gt['category']}) ===", flush=True)
        for sysname in SYSTEMS:
            print(f"  {sysname} ...", flush=True)
            row = {"incident": iid, "system": sysname, "category": gt["category"],
                   "fault_type": gt["fault_type"], "culprit": gt["primary"][0][0]}
            try:
                if sysname == "yours_raw":
                    r = retry(lambda: LB.run_yours_raw(bundle), label="raw")
                elif sysname == "yours_distilled":
                    r = retry(lambda: LB.run_yours_distilled(bundle), label="dist")
                elif sysname == "rcacopilot":
                    r = retry(lambda: LB.run_rcacopilot(iid, bundle, corpus, k=2), label="copilot")
                else:
                    r = retry(lambda: LB.run_rcagent(bundle, n=3), label="rcagent")
                jr = retry(lambda: judgemod.judge(r["text"], gt), label="judge")
                row.update({"latency_s": r["latency_s"], "prompt_tokens": r["prompt_tokens"],
                            "recall": jr["judge_recall"], "precision": jr["judge_precision"],
                            "tp": jr["judge_tp"], "misattr": jr["judge_misattributed"],
                            "hit": jr["judge_recall"] >= 0.999, "response": r["text"]})
                print(f"    hit={row['hit']} recall={jr['judge_recall']} prec={jr['judge_precision']} "
                      f"tok={r['prompt_tokens']} {r['latency_s']}s", flush=True)
            except Exception as e:
                row.update({"error": str(e)[:200], "recall": None})
                print(f"    ! FAILED: {str(e)[:140]}", flush=True)
            results.append(row)
            json.dump(results, open(OUT, "w"), indent=2)
    summarize(results)


def summarize(results):
    print("\n" + "=" * 82)
    print("20 REAL INCIDENTS — localisation accuracy (gpt-5 judge)")
    print("=" * 82)
    print(f"{'System':<18}{'Accuracy':<12}{'Precision':<12}{'Mean tok':<12}{'Mean lat':<10}{'fails'}")
    print("-" * 82)
    for s in SYSTEMS:
        rows = [r for r in results if r["system"] == s and r.get("recall") is not None]
        fails = sum(1 for r in results if r["system"] == s and r.get("recall") is None)
        if not rows:
            print(f"{s:<18}(no successful runs) fails={fails}"); continue
        acc = 100 * sum(r["hit"] for r in rows) / len(rows)
        prec = 100 * sum(r["precision"] for r in rows) / len(rows)
        tok = sum(r["prompt_tokens"] for r in rows) / len(rows)
        lat = sum(r["latency_s"] for r in rows) / len(rows)
        print(f"{s:<18}{acc:>6.1f}%     {prec:>6.1f}%     {tok:>8,.0f}    {lat:>6.1f}s    {fails}")
    print("-" * 82)


if __name__ == "__main__":
    main()
