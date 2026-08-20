# eval/baselines/efca_ablation.py
"""C6 — per-component EFCA ablation + positional-robustness study.

Builds RCA prompts for each cumulative EFCA configuration so the contribution of
each stage can be isolated:

  raw       full collect-all dump (no EFCA)
  topk      Stage 1 only: per-source candidate retrieval, plain listing
  rerank    + Stage 2: neural cross-encoder reranking (bge-reranker-v2-m3)
  compress  + Stage 3: verbatim high-value + collapsed routine
  edge      + Stage 4: salience-based edge placement (strongest first / 2nd last)
  expand    + Stage 5: confidence-gated on-demand expansion hint (= full EFCA)

Also emits a positional study: fix the compressed evidence set and force the
decisive SIGNAL snippet to the FIRST / MIDDLE / LAST position, to isolate the
lost-in-the-middle effect independently of retrieval quality.

Runs on the GPU node (needs the neural reranker). Outputs prompts JSON; the RCA
+ judge pass runs separately on the cluster host.

    RERANKER: neural (USE_RERANKER_MODEL forced True here)
    python efca_ablation.py --n 15 --out efca_ablation_prompts.json
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.schemas import EvidenceSnippet, FaultSignature, SIGNAL
from app.chunkers.log_event_chunker import chunk_bundle
from app.compress.evidence_compressor import compress
from app.ordering.edge_placement import edge_place
from app.prompts.evidence_block import render_evidence_block
from app.pipelines.evidence_first_pipeline import _candidate_retrieval
from app.rerank.cross_encoder_reranker import Reranker
from app.config import EVIDENCE_CANDIDATES_PER_SOURCE, EVIDENCE_FINAL_K, LOW_CONFIDENCE_THRESHOLD

from benchmark_suite import generate_incidents
from benchmark_pipeline import build_raw_prompt


def est_tokens(s):
    return round(len(s) / 4)


def rca_base_prompt(fault, evidence_section):
    return f"""You are a Kubernetes SRE agent performing autonomous root cause analysis.

Rules:
- Analyze the Kubernetes evidence sections first.
- Identify PRIMARY root causes — failures that have distinct, independent origins.
- Distinguish root causes from cascading effects. Do NOT list downstream symptoms as separate faults.
- Cite specific evidence to support every conclusion.
- Do not ask for approval. Proceed autonomously.

## Incident Report
Cluster: {fault.cluster}
Namespace: {fault.namespace}
Summary: {fault.summary}

## Expected Output
1. Primary Root Cause(s) — one entry per independent origin
2. Cascading Effects
3. Impacted Services
4. Remediation Steps
5. Confidence Level

{evidence_section}

## Response Format
Write your analysis in markdown. End with this exact section (nothing after it):

## Validator Packet
```json
{{"hypothesis": "<one sentence>", "confidence": 0.0, "evidenceSummary": "<2-3 sentences>", "safeToFailover": true}}
```
"""


def _plain_block(snippets, fault, header="## Evidence (selected)"):
    """Render a snippet list in the given order — NO edge placement, NO head/tail
    split — so configs before Stage 4 don't accidentally get edge benefits."""
    lines = [header,
             "The evidence below was selected for this incident. Fault signature: "
             + fault.as_query().strip()]
    for i, s in enumerate(snippets, start=1):
        lines.append(f"\n[E{i}] (source={s.source}, class={s.signal_class}, score={s.score:.2f})\n{s.text}")
    return "\n".join(lines)


def _positional_block(ordered, signal_idx_target, fault):
    """Fixed evidence set; move the decisive SIGNAL snippet to a target position."""
    sig_pos = next((i for i, s in enumerate(ordered) if s.signal_class == SIGNAL), 0)
    seq = list(ordered)
    sig = seq.pop(sig_pos)
    if signal_idx_target == "first":
        seq.insert(0, sig)
    elif signal_idx_target == "last":
        seq.append(sig)
    else:  # middle
        seq.insert(len(seq) // 2, sig)
    return _plain_block(seq, fault, header="## Evidence (fixed set)")


def build_configs(bundle, reranker):
    """Return {config_name: {prompt, tokens, top_score}} for one incident."""
    fault = bundle.fault
    out = {}

    # raw
    raw_block = build_raw_prompt(bundle)
    out["raw"] = raw_block

    # Stage 1 — candidate retrieval (retrieval order, no rerank)
    all_snips = chunk_bundle(bundle)
    candidates = _candidate_retrieval(all_snips, fault, EVIDENCE_CANDIDATES_PER_SOURCE)
    topk = candidates[:EVIDENCE_FINAL_K]
    out["topk"] = _plain_block(topk, fault)

    # Stage 2 — neural rerank
    reranker.score(fault, candidates)
    ranked = sorted(candidates, key=lambda s: s.score, reverse=True)
    top_score = ranked[0].score if ranked else 0.0
    out["rerank"] = _plain_block(ranked[:EVIDENCE_FINAL_K], fault)

    # Stage 3 — compress
    verbatim, compressed, _ = compress(ranked, final_k=EVIDENCE_FINAL_K)
    out["compress"] = render_evidence_block(verbatim, compressed, fault,
                                            low_confidence=False, incident_id=None)

    # Stage 4 — edge placement
    placed = edge_place(verbatim)
    out["edge"] = render_evidence_block(placed, compressed, fault,
                                        low_confidence=False, incident_id=None)

    # Stage 5 — + confidence-gated expansion hint (full EFCA)
    low_conf = top_score < LOW_CONFIDENCE_THRESHOLD
    out["expand"] = render_evidence_block(placed, compressed, fault,
                                          low_confidence=low_conf, incident_id=bundle.key())

    # positional study (fixed = verbatim set, in rank order)
    pos = {}
    for tgt in ("first", "middle", "last"):
        pos[tgt] = _positional_block(verbatim, tgt, fault)

    prompts = {k: rca_base_prompt(fault, v) for k, v in out.items()}
    pos_prompts = {k: rca_base_prompt(fault, v) for k, v in pos.items()}
    return prompts, pos_prompts, top_score


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=15)
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--noise", type=int, default=700)
    ap.add_argument("--out", default="efca_ablation_prompts.json")
    args = ap.parse_args()

    reranker = Reranker(use_model=True)   # NEURAL cross-encoder (bge-reranker-v2-m3)
    if reranker._model is None:
        print("!! neural reranker failed to load — aborting (would fall back to lexical)")
        sys.exit(2)
    print(f"neural reranker loaded: {reranker.model_name}")

    incidents = generate_incidents(n=args.n, seed=args.seed, noise=args.noise)
    data = {"reranker": reranker.model_name, "incidents": []}
    for name, bundle, gt in incidents:
        prompts, pos_prompts, top_score = build_configs(bundle, reranker)
        rec = {"incident": name, "faults": len(gt["primary"]),
               "gt": {"primary": gt["primary"], "distractors": gt.get("distractors", [])},
               "top_score": round(top_score, 4),
               "configs": {k: {"prompt": p, "tokens": est_tokens(p)} for k, p in prompts.items()},
               "positional": {k: {"prompt": p, "tokens": est_tokens(p)} for k, p in pos_prompts.items()}}
        data["incidents"].append(rec)
        toks = {k: rec["configs"][k]["tokens"] for k in rec["configs"]}
        print(f"  {name}: top_score={top_score:.3f} tokens={toks}", flush=True)

    json.dump(data, open(args.out, "w"), indent=2)
    print(f"\nsaved {len(data['incidents'])} incidents x {len(data['incidents'][0]['configs'])} configs -> {args.out}")


if __name__ == "__main__":
    main()
