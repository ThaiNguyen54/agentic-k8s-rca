# eval/overflow_test.py
"""Overflow experiment: on a SMALL-window model (o4-mini), sweep evidence size.

Raw collect-all grows with the cluster; past the model's context window it is
rejected/truncated → the RCA can't be produced. The distilled block stays ~1k
tokens and always fits. This is the one condition where the pipeline changes the
OUTCOME (success/recall), not just efficiency.

For each evidence size we send raw and distilled to the target agent, detect
context-overflow failures, and judge recall (gpt-5 judge) on successes.

Env: RCA_AGENT_URL (small-window agent), JUDGE_AGENT_URL
Run:  RCA_AGENT_URL=http://127.0.0.1:8083/api/a2a/kagent/rca-o4mini/ python eval/overflow_test.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app.pipelines.evidence_first_pipeline import distill_evidence
from app.rerank.cross_encoder_reranker import Reranker
from benchmark_pipeline import build_raw_prompt
from hard_scenarios import hard_C
from e2e_ablation import rca_base_prompt, _post, est_tokens
import judge as judgemod

SIZES = [("~80k", 1000), ("~180k", 2400), ("~290k", 3900)]  # noise → raw token size


def is_overflow(err):
    e = err.lower()
    return any(k in e for k in ["context", "maximum", "length", "token", "500"])


def main():
    reranker = Reranker(use_model=False)
    agent = os.getenv("RCA_AGENT_URL")
    print(f"target agent = {agent}")
    results = []
    for label, noise in SIZES:
        name, bundle, gt = hard_C(noise=noise)
        prompts = {
            "raw": rca_base_prompt(bundle.fault, "## Kubernetes Evidence\n" + build_raw_prompt(bundle)),
            "distilled": rca_base_prompt(bundle.fault, distill_evidence(bundle, reranker=reranker).prompt_block),
        }
        for mode, prompt in prompts.items():
            toks = est_tokens(prompt)
            print(f"  → {label} · {mode} · {toks:,} tok ...", flush=True)
            row = {"size": label, "noise": noise, "mode": mode, "prompt_tokens": toks}
            try:
                text, lat, _ = _post(agent, prompt, timeout=300)
                # judge recall on the produced analysis
                jr = judgemod.judge(text, gt)
                row.update({"success": True, "latency_s": round(lat, 1),
                            "recall": jr["judge_recall"], "precision": jr["judge_precision"]})
                print(f"    OK · recall {jr['judge_recall']} · {lat:.1f}s")
            except Exception as e:
                msg = str(e)
                row.update({"success": False, "overflow": is_overflow(msg), "error": msg[:200],
                            "recall": 0.0})
                print(f"    FAIL ({'overflow' if is_overflow(msg) else 'error'}): {msg[:120]}")
            results.append(row)
    json.dump(results, open("eval/overflow_results.json", "w"), indent=2)
    print("\n" + "=" * 84)
    print("OVERFLOW EXPERIMENT — small-window model (o4-mini)")
    print("=" * 84)
    print(f"{'Evidence size':<14}{'Mode':<12}{'RCA succeeded':<16}{'Recall':<10}{'Prompt tok'}")
    print("-" * 84)
    for r in results:
        ok = "yes" if r.get("success") else ("OVERFLOW" if r.get("overflow") else "fail")
        rec = f"{r.get('recall',0)*100:.0f}%" if r.get("success") else "—"
        print(f"{r['size']:<14}{r['mode']:<12}{ok:<16}{rec:<10}{r['prompt_tokens']:>9,}")
    print("-" * 84)
    print("saved → eval/overflow_results.json")


if __name__ == "__main__":
    main()
