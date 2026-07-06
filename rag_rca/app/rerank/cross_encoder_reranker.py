# app/rerank/cross_encoder_reranker.py
"""Stage 2 — task-aware reranking.

Reorders candidate snippets by relevance to the *fault signature*, not lexical
overlap alone. Uses a cross-encoder (bge-reranker-v2-m3, the pair to the bge-m3
embedder) when available, and otherwise a deterministic lexical + signal scorer
so the pipeline runs with no GPU and no model download.
"""
import math
import re
from typing import List

from app.config import RERANKER_MODEL, USE_RERANKER_MODEL
from app.schemas import EvidenceSnippet, FaultSignature, SIGNAL, CONTEXT

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> List[str]:
    return _TOKEN_RE.findall(text.lower())


class Reranker:
    """Scores snippets in [0, 1]. Higher = more relevant to the fault."""

    def __init__(self, model_name: str = RERANKER_MODEL, use_model: bool = USE_RERANKER_MODEL):
        self.model_name = model_name
        self.use_model = use_model
        self._model = None
        if use_model:
            self._try_load_model()

    def _try_load_model(self) -> None:
        try:
            from sentence_transformers import CrossEncoder  # lazy, optional
            self._model = CrossEncoder(self.model_name)
        except Exception:
            # No model / no GPU / not installed → deterministic fallback.
            self._model = None
            self.use_model = False

    # ── public API ───────────────────────────────────────────────────────────
    def score(self, fault: FaultSignature, snippets: List[EvidenceSnippet]) -> List[EvidenceSnippet]:
        if not snippets:
            return snippets
        if self._model is not None:
            self._score_with_model(fault, snippets)
        else:
            self._score_lexical(fault, snippets)
        return snippets

    # ── cross-encoder path ─────────────────────────────────────────────────────
    def _score_with_model(self, fault: FaultSignature, snippets: List[EvidenceSnippet]) -> None:
        query = fault.as_query()
        pairs = [(query, s.text) for s in snippets]
        raw = self._model.predict(pairs)
        for s, r in zip(snippets, raw):
            base = _sigmoid(float(r))
            # Even with the model, keep a mild signal-class prior so decisive
            # lines are not out-ranked by verbose-but-relevant routine chatter.
            s.score = min(1.0, base + _signal_bonus(s) * 0.15)

    # ── lexical + signal fallback ──────────────────────────────────────────────
    def _score_lexical(self, fault: FaultSignature, snippets: List[EvidenceSnippet]) -> None:
        query_tokens = set(_tokens(fault.as_query()))
        target_terms = [t.lower() for t in fault.target_terms()]
        hint_tokens = set(_tokens((fault.root_cause_hint or "") + " " + (fault.fault_type or "")))

        for s in snippets:
            toks = _tokens(s.text)
            if not toks:
                s.score = 0.0
                continue
            tokset = set(toks)

            # 1) lexical overlap with the fault query (Jaccard-ish, length-normalised)
            overlap = len(query_tokens & tokset)
            lexical = overlap / (1.0 + math.log1p(len(tokset)))

            # 2) does the snippet name the actual faulting object?
            target_hit = any(term in s.text.lower() for term in target_terms)

            # 3) does it echo the hypothesised cause (crashloop, oom, probe, ...)?
            hint_hit = len(hint_tokens & tokset)

            raw = (
                0.35 * _clip01(lexical)
                + 0.25 * (1.0 if target_hit else 0.0)
                + 0.15 * _clip01(hint_hit / 3.0)
                + _signal_bonus(s)
            )
            s.score = _clip01(raw)


def _signal_bonus(s: EvidenceSnippet) -> float:
    if s.signal_class == SIGNAL:
        return 0.45
    if s.signal_class == CONTEXT:
        return 0.10
    return 0.0


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))
