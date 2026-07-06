# app/compress/evidence_compressor.py
"""Stage 3 — context compression.

Keeps high-value snippets verbatim and collapses low-value, high-volume routine
lines into compact "N× <template>" summaries. SIGNAL snippets are never
compressed away, even if they fall outside the verbatim budget.
"""
import re
from collections import OrderedDict
from typing import List, Tuple

from app.config import COMPRESSION_SAMPLES_PER_CLUSTER, EVIDENCE_FINAL_K
from app.schemas import EvidenceSnippet, SIGNAL, ROUTINE

# Masks for building a template key so near-identical lines cluster together.
_MASKS = [
    (re.compile(r"\d{4}-\d{2}-\d{2}[t ]\d{2}:\d{2}:\d{2}(?:\.\d+)?z?", re.I), "<ts>"),
    (re.compile(r"\b[0-9a-f]{7,64}\b", re.I), "<hex>"),
    (re.compile(r"\b\d+(?:\.\d+)?(?:ms|s|m|h|ki|mi|gi|kb|mb|gb)?\b", re.I), "<n>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b"), "<ip>"),
]


def _template(text: str) -> str:
    t = text.lower()
    for pat, repl in _MASKS:
        t = pat.sub(repl, t)
    return re.sub(r"\s+", " ", t).strip()


def compress(
    ranked: List[EvidenceSnippet],
    final_k: int = EVIDENCE_FINAL_K,
    samples_per_cluster: int = COMPRESSION_SAMPLES_PER_CLUSTER,
) -> Tuple[List[EvidenceSnippet], List[str], dict]:
    """Split ranked snippets into (verbatim, compressed_summaries, stats).

    `ranked` must already be sorted by score descending.
    """
    verbatim: List[EvidenceSnippet] = []
    leftovers: List[EvidenceSnippet] = []
    seen_templates: set = set()

    for s in ranked:
        tmpl = _template(s.text)
        # SIGNAL snippets are always kept verbatim so a decisive line is never
        # compressed away. ROUTINE snippets are always collapsed. CONTEXT (and
        # any other) snippets fill the remaining verbatim budget, skipping
        # near-duplicates (same template) so they don't eat slots.
        if s.signal_class == SIGNAL:
            verbatim.append(s)
            seen_templates.add(tmpl)
        elif (
            s.signal_class != ROUTINE
            and len(verbatim) < final_k
            and tmpl not in seen_templates
        ):
            verbatim.append(s)
            seen_templates.add(tmpl)
        else:
            leftovers.append(s)

    # Never return an empty verbatim set: if nothing qualified (e.g. an
    # all-routine bundle), promote the top distinct-template snippets.
    if not verbatim:
        for s in ranked:
            tmpl = _template(s.text)
            if tmpl in seen_templates:
                continue
            verbatim.append(s)
            seen_templates.add(tmpl)
            leftovers = [x for x in leftovers if x is not s]
            if len(verbatim) >= final_k:
                break

    compressed = _collapse(leftovers, samples_per_cluster)

    stats = {
        "input_snippets": len(ranked),
        "verbatim_snippets": len(verbatim),
        "compressed_lines_in": len(leftovers),
        "compressed_clusters_out": len(compressed),
        "signal_snippets": sum(1 for s in ranked if s.signal_class == SIGNAL),
    }
    return verbatim, compressed, stats


def _collapse(snippets: List[EvidenceSnippet], samples_per_cluster: int) -> List[str]:
    clusters: "OrderedDict[str, dict]" = OrderedDict()
    for s in snippets:
        key = f"{s.source}::{_template(s.text)}"
        c = clusters.get(key)
        if c is None:
            clusters[key] = {"count": 1, "source": s.source, "samples": [s.text]}
        else:
            c["count"] += 1
            if len(c["samples"]) < samples_per_cluster:
                c["samples"].append(s.text)

    out: List[str] = []
    # Emit the largest clusters first — they represent the dominant routine noise.
    for c in sorted(clusters.values(), key=lambda x: x["count"], reverse=True):
        sample = c["samples"][0].strip()
        if len(sample) > 200:
            sample = sample[:197] + "..."
        if c["count"] == 1:
            out.append(f"[{c['source']}] {sample}")
        else:
            out.append(f"[{c['source']}] {c['count']}× {sample}")
    return out
