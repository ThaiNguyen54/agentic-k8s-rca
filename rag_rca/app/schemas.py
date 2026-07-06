from pydantic import BaseModel, Field
from typing import Any , Dict, List, Optional

class ParsedDocument(BaseModel):
    source_type: str
    file_path: str
    doc_index: int
    content: Dict[str, Any]

class CanonicalRecord(BaseModel):
    record_id: str
    source_type: str
    file_path: str
    doc_index: int = 0
    resource_kind: Optional[str] = None
    resource_name: Optional[str] = None
    namespace: Optional[str] = None
    provider: Optional[str] = None
    section: Optional[str] = None
    path: Optional[str] = None
    text: str
    raw: Dict[str, Any] = Field(default_factory=dict)
    attributes: Dict[str, Any] = Field(default_factory=dict)
    links: Dict[str, Any] = Field(default_factory=dict)


# ── Evidence-First Retrieval Pipeline ────────────────────────────────────────
# These models describe an incident's collected evidence and the distilled,
# reranked, compressed output that replaces the "collect-all" prompt dump.

# Signal classes assigned to every snippet during chunking.
SIGNAL = "signal"        # decisive fault evidence (CrashLoopBackOff, probe failed, OOMKilled, ...)
CONTEXT = "context"      # relevant but not decisive (Scheduled, Started, Pulled, ...)
ROUTINE = "routine"      # high-volume, low-value lines that get collapsed


class FaultSignature(BaseModel):
    """The incident fingerprint used as the retrieval / rerank query.

    Mirrors the labels emitted by the multi-cluster-fault-controller and parsed
    by the Go webhook-receiver (cluster / namespace / fault_type / fault_object
    / root_cause_hint).
    """
    cluster: Optional[str] = None
    namespace: Optional[str] = None
    node: Optional[str] = None
    pod: Optional[str] = None
    fault_type: Optional[str] = None
    fault_object: Optional[str] = None
    root_cause_hint: Optional[str] = None
    summary: Optional[str] = None

    def as_query(self) -> str:
        parts = [
            self.summary,
            self.root_cause_hint,
            self.fault_type,
            self.fault_object,
            self.pod,
            self.node,
            self.namespace,
        ]
        return " ".join(p for p in parts if p)

    def target_terms(self) -> List[str]:
        """Concrete object names a decisive snippet is likely to mention."""
        terms: List[str] = []
        for raw in (self.fault_object, self.pod, self.node):
            if not raw:
                continue
            # strip "pod/", "node/", "service/" prefixes
            name = raw.split("/", 1)[-1].strip()
            if name:
                terms.append(name)
        return terms


class EvidenceSnippet(BaseModel):
    snippet_id: str
    source: str                     # e.g. "events.txt", "checkout-xxx-logs.txt"
    source_type: str                # "event" | "log" | "describe" | "kubectl" | "manifest"
    text: str
    signal_class: str = ROUTINE
    namespace: Optional[str] = None
    pod: Optional[str] = None
    node: Optional[str] = None
    line_no: Optional[int] = None
    score: float = 0.0              # rerank score (higher = more relevant)


class EvidenceBundle(BaseModel):
    """The raw, scoped evidence collected at fault time.

    `sources` maps a source name (the key the Go collector already uses, e.g.
    "events.txt", "pods-all.txt", "<pod>-logs.txt") to its raw text content.
    """
    fault: FaultSignature
    sources: Dict[str, str] = Field(default_factory=dict)
    incident_id: Optional[str] = None

    def key(self) -> str:
        """Stable identifier used to cache the bundle for on-demand expansion."""
        if self.incident_id:
            return self.incident_id
        f = self.fault
        return ":".join(x for x in (f.cluster, f.namespace, f.fault_object) if x) or "incident"


class DistilledEvidence(BaseModel):
    verbatim: List[EvidenceSnippet] = Field(default_factory=list)  # edge-placed order
    compressed: List[str] = Field(default_factory=list)            # collapsed routine lines
    prompt_block: str = ""
    low_confidence: bool = False
    stats: Dict[str, Any] = Field(default_factory=dict)