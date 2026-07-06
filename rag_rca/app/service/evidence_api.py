# app/service/evidence_api.py
"""HTTP surface for the Evidence-First Retrieval Pipeline.

The Go webhook-receiver collects scoped evidence at fault time and POSTs the
bundle here instead of stuffing it into the RCA prompt. This service returns
the distilled, reranked, compressed, edge-placed evidence block.

Run:
    pip install fastapi uvicorn
    uvicorn app.service.evidence_api:app --host 0.0.0.0 --port 8100

Endpoints:
    GET  /healthz                    liveness
    POST /evidence/distill           body = EvidenceBundle  ->  DistilledEvidence
                                     (also caches the bundle for later expansion)
    GET  /evidence/sources           ?incident_id=          ->  available source names
    GET  /evidence/timeline          ?incident_id=&source=  ->  full raw timeline
                                     (stage 5 — on-demand expansion for low-confidence RCA)
    POST /evidence/expand            body = ExpandRequest     ->  stateless expansion
"""
from collections import OrderedDict
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.schemas import DistilledEvidence, EvidenceBundle, EvidenceSnippet
from app.chunkers.log_event_chunker import chunk_source
from app.pipelines.evidence_first_pipeline import distill_evidence
from app.rerank.cross_encoder_reranker import Reranker

app = FastAPI(title="Evidence-First Retrieval Pipeline")

# Reranker (and any model) is loaded once at startup and reused.
_reranker = Reranker()

# Bounded in-memory cache of recent bundles, keyed by incident id, so the agent
# can expand into the full raw timeline after a low-confidence distillation.
_CACHE_MAX = 64
_BUNDLE_CACHE: "OrderedDict[str, EvidenceBundle]" = OrderedDict()


def _cache_put(bundle: EvidenceBundle) -> str:
    key = bundle.key()
    _BUNDLE_CACHE[key] = bundle
    _BUNDLE_CACHE.move_to_end(key)
    while len(_BUNDLE_CACHE) > _CACHE_MAX:
        _BUNDLE_CACHE.popitem(last=False)
    return key


def _cache_get(incident_id: str) -> EvidenceBundle:
    bundle = _BUNDLE_CACHE.get(incident_id)
    if bundle is None:
        raise HTTPException(
            status_code=404,
            detail=f"no cached evidence for incident_id={incident_id!r}",
        )
    return bundle


class ExpandRequest(BaseModel):
    bundle: EvidenceBundle
    source: str
    max_lines: Optional[int] = 400


class ExpandResponse(BaseModel):
    incident_id: str
    source: str
    snippets: List[EvidenceSnippet]
    truncated: bool


@app.get("/healthz")
def healthz():
    return {
        "ok": True,
        "reranker_model_loaded": _reranker._model is not None,
        "cached_incidents": len(_BUNDLE_CACHE),
    }


@app.post("/evidence/distill", response_model=DistilledEvidence)
def distill(bundle: EvidenceBundle) -> DistilledEvidence:
    _cache_put(bundle)
    return distill_evidence(bundle, reranker=_reranker)


@app.get("/evidence/sources")
def sources(incident_id: str):
    bundle = _cache_get(incident_id)
    return {"incident_id": incident_id, "sources": list(bundle.sources.keys())}


@app.get("/evidence/timeline", response_model=ExpandResponse)
def timeline(incident_id: str, source: str, max_lines: int = 400) -> ExpandResponse:
    """Stage 5 — full uncompressed timeline for one source of a cached incident."""
    bundle = _cache_get(incident_id)
    if source not in bundle.sources:
        raise HTTPException(
            status_code=404,
            detail=f"source {source!r} not in incident {incident_id!r}; "
            f"available: {list(bundle.sources.keys())}",
        )
    return _expand(bundle, source, max_lines)


@app.post("/evidence/expand", response_model=ExpandResponse)
def expand(req: ExpandRequest) -> ExpandResponse:
    """Stateless expansion — caller supplies the bundle directly."""
    return _expand(req.bundle, req.source, req.max_lines or 400)


def _expand(bundle: EvidenceBundle, source: str, max_lines: int) -> ExpandResponse:
    content = bundle.sources.get(source, "")
    snippets = chunk_source(source, content, bundle.fault)
    truncated = False
    if max_lines and len(snippets) > max_lines:
        snippets = snippets[:max_lines]
        truncated = True
    return ExpandResponse(
        incident_id=bundle.key(),
        source=source,
        snippets=snippets,
        truncated=truncated,
    )
