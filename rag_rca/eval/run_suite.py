# eval/run_suite.py
"""Run the diverse incident suite through the RCA agent (raw vs distilled),
judge every response with the gpt-5 judge, and aggregate realistic accuracy.

Robust for a long run: per-call retries with backoff, incremental JSON save
(resumable-ish), and failed-status detection (via e2e_ablation._post).

Env: RCA_AGENT_URL, JUDGE_AGENT_URL
Run:  python eval/run_suite.py --n 15 --reps 3 --out suite_results.json
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app.pipelines.evidence_first_pipeline import distill_evidence
from app.rerank.cross_encoder_reranker import Reranker
from benchmark_suite import generate_incidents
from benchmark_pipeline import build_raw_prompt
from e2e_ablation import rca_base_prompt, call_rca, est_tokens
import judge as judgemod


def with_retry(fn, tries=4, base=3.0, label=""):
    last = None
    for t in range(tries):
        try:
            return fn()
        except Exception as e:
            last = e
            wait = base * (t + 1)
            print(f"      retry {label} ({t+1}/{tries}) after error: {str(e)[:100]} — waiting {wait:.0f}s", flush=True)
            time.sleep(wait)
    raise last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=15)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--noise", type=int, default=700)
    ap.add_argument("--out", default="eval/suite_results.json")
    args = ap.parse_args()

    reranker = Reranker(use_model=False)
    incidents = generate_incidents(n=args.n, seed=args.seed, noise=args.noise)
    print(f"generated {len(incidents)} incidents · reps={args.reps} · "
          f"RCA={os.getenv('RCA_AGENT_URL')} · judge={os.getenv('JUDGE_AGENT_URL')}\n")

    results = []
    for name, bundle, gt in incidents:
        raw_prompt = rca_base_prompt(bundle.fault, "## Kubernetes Evidence\n" + build_raw_prompt(bundle))
        dist_prompt = rca_base_prompt(bundle.fault, distill_evidence(bundle, reranker=reranker).prompt_block)
        prompts = {"raw": raw_prompt, "distilled": dist_prompt}
        for mode, prompt in prompts.items():
            for rep in range(args.reps):
                tag = f"{name}·{mode}·r{rep+1}"
                print(f"  → {tag} · {est_tokens(prompt):,} tok", flush=True)
                row = {"incident": name, "mode": mode, "rep": rep,
                       "faults": len(gt["primary"]), "prompt_tokens": est_tokens(prompt)}
                try:
                    text, lat, _ = with_retry(lambda: call_rca(prompt, timeout=300), label="rca")
                    jr = with_retry(lambda: judgemod.judge(text, gt), label="judge")
                    row.update({"latency_s": round(lat, 1),
                                "recall": jr["judge_recall"], "precision": jr["judge_precision"],
                                "tp": jr["judge_tp"], "misattr": jr["judge_misattributed"],
                                "response": text})
                    print(f"    recall {jr['judge_recall']} prec {jr['judge_precision']} · {lat:.1f}s")
                except Exception as e:
                    row.update({"error": str(e)[:200], "recall": None})
                    print(f"    ! FAILED: {str(e)[:120]}")
                results.append(row)
                json.dump(results, open(args.out, "w"), indent=2)   # incremental save
    aggregate(results)


def _micro(results, mode, key):
    """Micro-average key over successful rows of a mode (weight by faults)."""
    tp = sum(r["tp"] for r in results if r["mode"] == mode and r.get("recall") is not None and key == "recall")
    faults = sum(r["faults"] for r in results if r["mode"] == mode and r.get("recall") is not None)
    return (tp / faults) if faults else float("nan")


def aggregate(results):
    print("\n" + "=" * 92)
    print("SUITE ABLATION — RCA agent, raw vs distilled (gpt-5 judge, micro-averaged over all faults)")
    print("=" * 92)
    print(f"{'Mode':<12}{'Localization acc':<20}{'Mean precision':<18}{'Mean latency':<16}{'Mean tok':<12}{'fails'}")
    print("-" * 92)
    for mode in ("raw", "distilled"):
        rows = [r for r in results if r["mode"] == mode and r.get("recall") is not None]
        fails = sum(1 for r in results if r["mode"] == mode and r.get("recall") is None)
        if not rows:
            print(f"{mode:<12}(no successful runs) · fails={fails}")
            continue
        acc = _micro(results, mode, "recall") * 100
        prec = sum(r["precision"] for r in rows) / len(rows) * 100
        lat = sum(r["latency_s"] for r in rows) / len(rows)
        tok = sum(r["prompt_tokens"] for r in rows) / len(rows)
        print(f"{mode:<12}{acc:>6.1f}%            {prec:>6.1f}%           {lat:>6.1f} s        {tok:>8,.0f}   {fails}")
    print("-" * 92)
    print("Localization acc = micro-average (total root causes identified / total across all incidents x reps).")


if __name__ == "__main__":
    main()
