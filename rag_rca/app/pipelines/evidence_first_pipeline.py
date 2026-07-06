# app/pipelines/evidence_first_pipeline.py
"""Evidence-First Retrieval Pipeline.

Replaces "collect-all context stuffing" with five stages:

  1. Top-k retrieval   — chunk each scoped source and keep candidate snippets
                         (SIGNAL snippets are always kept).
  2. Reranking         — a cross-encoder (or lexical fallback) reorders
                         candidates by relevance to the fault signature.
  3. Compression       — top snippets stay verbatim; routine lines collapse.
  4. Edge placement    — strongest first, second-strongest last.
  5. On-demand expand  — low-confidence incidents are flagged so the agent can
                         request the raw timeline (see service/expand hook).

The fast-changing evidence (logs, events, describe output) is passed in as an
ephemeral EvidenceBundle. Slow-changing manifests/runbooks can additionally be
retrieved from the persistent Qdrant index via `manifest_snippets`.
"""
import re
from typing import List, Optional

from app.config import (
    EVIDENCE_CANDIDATES_PER_SOURCE,
    EVIDENCE_FINAL_K,
    EDGE_PLACEMENT_ENABLED,
    LOW_CONFIDENCE_THRESHOLD,
)
from app.schemas import (
    DistilledEvidence,
    EvidenceBundle,
    EvidenceSnippet,
    FaultSignature,
    SIGNAL,
)
from app.chunkers.log_event_chunker import chunk_bundle
from app.rerank.cross_encoder_reranker import Reranker
from app.compress.evidence_compressor import compress
from app.ordering.edge_placement import edge_place
from app.prompts.evidence_block import render_evidence_block

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _prefilter_tokens(text: str) -> set:
    return set(_TOKEN_RE.findall(text.lower()))


def _candidate_retrieval(
    snippets: List[EvidenceSnippet],
    fault: FaultSignature,
    per_source: int,
) -> List[EvidenceSnippet]:
    """Stage 1 — cheap per-source top-k prefilter, scoped to the incident.

    Keeps every SIGNAL snippet unconditionally, plus the `per_source` best
    routine/context snippets per source by lexical overlap. This is the cheap
    recall step before the (more expensive) reranker.
    """
    query_tokens = _prefilter_tokens(fault.as_query())

    by_source: dict = {}
    for s in snippets:
        by_source.setdefault(s.source, []).append(s)

    kept: List[EvidenceSnippet] = []
    for _source, group in by_source.items():
        signals = [s for s in group if s.signal_class == SIGNAL]
        others = [s for s in group if s.signal_class != SIGNAL]

        def overlap(s: EvidenceSnippet) -> int:
            return len(query_tokens & _prefilter_tokens(s.text))

        others.sort(key=overlap, reverse=True)
        kept.extend(signals)
        kept.extend(others[:per_source])
    return kept


def distill_evidence(
    bundle: EvidenceBundle,
    *,
    reranker: Optional[Reranker] = None,
    manifest_snippets: Optional[List[EvidenceSnippet]] = None,
    per_source: int = EVIDENCE_CANDIDATES_PER_SOURCE,
    final_k: int = EVIDENCE_FINAL_K,
) -> DistilledEvidence:
    reranker = reranker or Reranker()
    fault = bundle.fault

    # Stage 1 — chunk + candidate retrieval
    all_snippets = chunk_bundle(bundle)
    if manifest_snippets:
        all_snippets.extend(manifest_snippets)
    candidates = _candidate_retrieval(all_snippets, fault, per_source)

    # Stage 2 — rerank against the fault signature
    reranker.score(fault, candidates)
    ranked = sorted(candidates, key=lambda s: s.score, reverse=True)

    # Stage 3 — compress (verbatim high-value + collapsed routine)
    verbatim, compressed, stats = compress(ranked, final_k=final_k)

    # Stage 4 — edge placement
    placed = edge_place(verbatim) if EDGE_PLACEMENT_ENABLED else verbatim

    # Stage 5 — confidence gate for on-demand expansion
    top_score = ranked[0].score if ranked else 0.0
    low_confidence = top_score < LOW_CONFIDENCE_THRESHOLD

    incident_id = bundle.key()
    block = render_evidence_block(
        placed, compressed, fault, low_confidence=low_confidence, incident_id=incident_id
    )

    stats.update(
        {
            "incident_id": incident_id,
            "total_snippets": len(all_snippets),
            "candidates": len(candidates),
            "top_score": round(top_score, 4),
            "raw_chars": sum(len(v) for v in bundle.sources.values()),
            "distilled_chars": len(block),
        }
    )
    raw = stats["raw_chars"] or 1
    stats["reduction_ratio"] = round(1.0 - stats["distilled_chars"] / raw, 4)

    return DistilledEvidence(
        verbatim=placed,
        compressed=compressed,
        prompt_block=block,
        low_confidence=low_confidence,
        stats=stats,
    )
