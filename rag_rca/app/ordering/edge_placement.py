# app/ordering/edge_placement.py
"""Stage 4 — edge placement.

Mitigates the "lost in the middle" effect (Liu et al., 2024) by putting the
strongest evidence first and the second-strongest last, so the two most
decisive snippets sit at the high-attention edges of the prompt while lower-
value snippets occupy the middle.
"""
from typing import List

from app.schemas import EvidenceSnippet


def edge_place(ranked: List[EvidenceSnippet]) -> List[EvidenceSnippet]:
    """Reorder score-descending snippets to [1st, 3rd, 4th, ..., nth, 2nd].

    The returned list's FIRST element is the strongest and its LAST element is
    the second-strongest.
    """
    if len(ranked) <= 2:
        return list(ranked)
    strongest = ranked[0]
    second = ranked[1]
    middle = ranked[2:]
    return [strongest, *middle, second]
