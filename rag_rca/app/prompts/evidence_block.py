# app/prompts/evidence_block.py
"""Renders the distilled evidence into the prompt block that replaces the raw
collect-all dump. Layout realises edge placement: the strongest snippet is at
the top, routine/compressed context sits in the middle, and the second-most
decisive snippet is physically last.
"""
from typing import List

from app.schemas import EvidenceSnippet, FaultSignature


def _fmt(tag: str, s: EvidenceSnippet) -> str:
    meta = f"source={s.source}, class={s.signal_class}, score={s.score:.2f}"
    return f"[{tag}] ({meta})\n{s.text}"


def render_evidence_block(
    placed: List[EvidenceSnippet],
    compressed: List[str],
    fault: FaultSignature,
    low_confidence: bool = False,
    incident_id: str | None = None,
) -> str:
    lines: List[str] = []
    lines.append("## Evidence (distilled, most decisive first)")
    lines.append(
        "The evidence below was retrieved, reranked against the fault signature, "
        "and compressed. Routine high-volume lines are collapsed under "
        "'Compressed Context'. The two most decisive snippets are placed at the "
        "top and the very bottom of this block."
    )
    q = fault.as_query().strip()
    if q:
        lines.append(f"\nFault signature: {q}")
    if incident_id:
        lines.append(f"Incident: {incident_id}")

    if not placed and not compressed:
        lines.append("\n(no evidence collected)")
        return "\n".join(lines)

    # strongest first
    head = placed[0] if placed else None
    tail = placed[-1] if len(placed) >= 2 else None
    middle = placed[1:-1] if len(placed) >= 2 else []

    if head is not None:
        lines.append("\n### Primary signal")
        lines.append(_fmt("E1", head))

    if middle:
        lines.append("\n### Supporting evidence")
        for i, s in enumerate(middle, start=2):
            lines.append(_fmt(f"E{i}", s))

    if compressed:
        lines.append("\n### Compressed Context (routine, collapsed)")
        for c in compressed:
            lines.append(f"- {c}")

    if tail is not None:
        lines.append("\n### Secondary signal (most critical remaining)")
        lines.append(_fmt(f"E{len(placed)}", tail))

    if low_confidence:
        expand_hint = (
            f'call get_raw_timeline(incident_id="{incident_id}", source=<source>)'
            if incident_id
            else "request the full raw timeline for the affected object"
        )
        lines.append(
            "\n> NOTE: retrieval confidence is LOW. If the evidence above is "
            f"insufficient, {expand_hint} before concluding."
        )

    return "\n".join(lines)
