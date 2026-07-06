# app/chunkers/log_event_chunker.py
"""Stage 1 chunking for fast-changing evidence (logs, events, describe output).

The schema_chunker handles slow-changing manifests. This module handles the
ephemeral per-incident bundle collected by the Go webhook-receiver, splitting
each raw source into line/block-level EvidenceSnippets and tagging every snippet
with a signal class so the decisive lines are never diluted or dropped.
"""
import re
from typing import List

from app.schemas import (
    EvidenceBundle,
    EvidenceSnippet,
    FaultSignature,
    SIGNAL,
    CONTEXT,
    ROUTINE,
)

# Decisive fault indicators — a snippet matching any of these is a SIGNAL and is
# protected from prefilter dropping and compression.
SIGNAL_PATTERNS = [
    r"crashloopbackoff",
    r"back-?off restarting",
    r"oomkilled",
    r"out of memory",
    r"imagepullbackoff",
    r"errimagepull",
    r"failedscheduling",
    r"failedmount",
    r"readiness probe failed",
    r"liveness probe failed",
    r"startup probe failed",
    r"\bunhealthy\b",
    r"\bevicted\b",
    r"\bnotready\b",
    r"node not ready",
    r"\bfailed\b",
    r"\berror\b",
    r"\bfatal\b",
    r"\bpanic\b",
    r"exit code [1-9]",
    r"non-zero exit",
    r"connection refused",
    r"connection reset",
    r"no route to host",
    r"i/o timeout",
    r"context deadline exceeded",
    r"502 bad gateway",
    r"503 service unavailable",
    r"\b5\d\d\b\s+(internal|error|bad gateway|service unavailable)",
    r"dial tcp .* refused",
    r"memorypressure",
    r"diskpressure",
]

# Not decisive on their own, but useful surrounding context.
CONTEXT_PATTERNS = [
    r"\bscheduled\b",
    r"\bpulling\b",
    r"\bpulled\b",
    r"\bcreated\b",
    r"\bstarted\b",
    r"\bkilling\b",
    r"successfully assigned",
    r"\brollout\b",
    r"scaledup|scaleddown|scalingreplicaset",
]

_SIGNAL_RE = re.compile("|".join(SIGNAL_PATTERNS), re.IGNORECASE)
_CONTEXT_RE = re.compile("|".join(CONTEXT_PATTERNS), re.IGNORECASE)


def classify_signal(text: str) -> str:
    if _SIGNAL_RE.search(text):
        return SIGNAL
    if _CONTEXT_RE.search(text):
        return CONTEXT
    return ROUTINE


def _source_type_for(name: str) -> str:
    low = name.lower()
    if "event" in low:
        return "event"
    if "-logs" in low or low.endswith("logs.txt"):
        return "log"
    if "describe" in low or low.startswith("node-"):
        return "describe"
    return "kubectl"


def _looks_like_header(line: str) -> bool:
    # kubectl table headers, e.g. "NAME READY STATUS ..." / "NAMESPACE NAME ..."
    stripped = line.strip()
    return stripped[:9].isupper() and (
        stripped.startswith("NAME") or stripped.startswith("NAMESPACE")
    )


def _describe_blocks(content: str) -> List[str]:
    """Split `kubectl describe` output into blank-line-separated blocks so the
    Events: section and Conditions stay coherent instead of exploding per line."""
    blocks, buf = [], []
    for line in content.splitlines():
        if line.strip() == "":
            if buf:
                blocks.append("\n".join(buf))
                buf = []
            continue
        buf.append(line)
    if buf:
        blocks.append("\n".join(buf))
    return blocks


def chunk_source(
    source_name: str,
    content: str,
    fault: FaultSignature,
) -> List[EvidenceSnippet]:
    if not content or not content.strip():
        return []

    source_type = _source_type_for(source_name)
    snippets: List[EvidenceSnippet] = []

    units: List[str]
    if source_type == "describe":
        units = _describe_blocks(content)
    else:
        units = [ln for ln in content.splitlines() if ln.strip()]

    for idx, unit in enumerate(units):
        if source_type in ("kubectl",) and _looks_like_header(unit):
            continue
        text = unit.rstrip()
        if not text:
            continue
        snippets.append(
            EvidenceSnippet(
                snippet_id=f"{source_name}:{idx}",
                source=source_name,
                source_type=source_type,
                text=text,
                signal_class=classify_signal(text),
                namespace=fault.namespace,
                pod=_infer_pod(text, fault),
                node=fault.node,
                line_no=idx,
            )
        )
    return snippets


def _infer_pod(text: str, fault: FaultSignature) -> str | None:
    for term in fault.target_terms():
        if term and term in text:
            return term
    return None


def chunk_bundle(bundle: EvidenceBundle) -> List[EvidenceSnippet]:
    out: List[EvidenceSnippet] = []
    for name, content in bundle.sources.items():
        out.extend(chunk_source(name, content, bundle.fault))
    return out
