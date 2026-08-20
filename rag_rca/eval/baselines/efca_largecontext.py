# eval/baselines/efca_largecontext.py
"""Large-context stress + positional test (builds prompts; GPU for neural reranker).

Tests the paper's thesis directly: as raw context grows, does accuracy degrade
(lost-in-the-middle / window pressure) while EFCA stays small and accurate?

  Size sweep: SAME incidents (fixed seed/faults), growing noise -> ~85k/160k/250k
              token raw context. raw vs EFCA(full) at each size.
  Positional: at the largest size, move the decisive SIGNAL line to first/middle/
              last of the RAW dump -> isolates the positional effect.

Outputs prompts JSON; RCA+judge runs on the cluster host.
    python efca_largecontext.py --n 8 --out efca_largectx_prompts.json
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.schemas import SIGNAL
from app.chunkers.log_event_chunker import classify_signal
from app.pipelines.evidence_first_pipeline import distill_evidence
from app.rerank.cross_encoder_reranker import Reranker
from benchmark_suite import generate_incidents
from benchmark_pipeline import build_raw_prompt
from efca_ablation import rca_base_prompt, est_tokens

SIZES = [("~85k", 1500), ("~160k", 2800), ("~250k", 4500)]


def reposition_signal(bundle, where):
    """Move all SIGNAL lines in events.txt to first / middle / last of the stream."""
    ev = bundle.sources.get("events.txt", "")
    lines = ev.split("\n")
    sig = [l for l in lines if l.strip() and classify_signal(l) == SIGNAL]
    non = [l for l in lines if not (l.strip() and classify_signal(l) == SIGNAL)]
    if where == "first":
        new = sig + non
    elif where == "last":
        new = non + sig
    else:
        m = len(non) // 2
        new = non[:m] + sig + non[m:]
    b2 = bundle.model_copy(deep=True)
    b2.sources["events.txt"] = "\n".join(new)
    return b2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=8)
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--out", default="efca_largectx_prompts.json")
    args = ap.parse_args()

    reranker = Reranker(use_model=True)
    if reranker._model is None:
        print("!! neural reranker not loaded"); sys.exit(2)
    print(f"neural reranker: {reranker.model_name}")

    data = {"reranker": reranker.model_name, "sizes": [s for s, _ in SIZES], "records": []}
    largest = SIZES[-1][0]
    for label, noise in SIZES:
        incs = generate_incidents(n=args.n, seed=args.seed, noise=noise)
        for name, bundle, gt in incs:
            raw_p = rca_base_prompt(bundle.fault, build_raw_prompt(bundle))
            efca_p = rca_base_prompt(bundle.fault, distill_evidence(bundle, reranker=reranker).prompt_block)
            rec = {"size": label, "noise": noise, "incident": name, "faults": len(gt["primary"]),
                   "gt": {"primary": gt["primary"], "distractors": gt.get("distractors", [])},
                   "configs": {"raw": {"prompt": raw_p, "tokens": est_tokens(raw_p)},
                               "efca": {"prompt": efca_p, "tokens": est_tokens(efca_p)}}}
            if label == largest:
                rec["positional"] = {}
                for pos in ("first", "middle", "last"):
                    b2 = reposition_signal(bundle, pos)
                    pr = rca_base_prompt(b2.fault, build_raw_prompt(b2))
                    rec["positional"][pos] = {"prompt": pr, "tokens": est_tokens(pr)}
            data["records"].append(rec)
            print(f"  {label} {name}: raw={rec['configs']['raw']['tokens']:,} "
                  f"efca={rec['configs']['efca']['tokens']:,}", flush=True)

    json.dump(data, open(args.out, "w"), indent=2)
    print(f"\nsaved {len(data['records'])} records -> {args.out}")


if __name__ == "__main__":
    main()
