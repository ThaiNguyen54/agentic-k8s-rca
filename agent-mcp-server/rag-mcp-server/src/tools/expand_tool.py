"""Stage-5 on-demand expansion tool for the Evidence-First Retrieval Pipeline.

When the distilled RCA evidence is flagged low-confidence, the agent can pull
the full, uncompressed timeline for a specific evidence source (pod logs, events,
node describe, ...) of the incident instead of relying on the compressed block.

Backed by the Evidence-First service (app.service.evidence_api):
    GET /evidence/sources?incident_id=...
    GET /evidence/timeline?incident_id=...&source=...&max_lines=...
"""
import os

import httpx
from core.server import mcp
from core.utils import get_tool_config

EVIDENCE_API_BASE = os.getenv(
    "EVIDENCE_API_BASE",
    "http://evidence-pipeline.rca.svc.cluster.local:8100",
)


@mcp.tool()
async def get_raw_timeline(
    incident_id: str,
    source: str | None = None,
    max_lines: int = 400,
):
    """Fetch the full raw (uncompressed) evidence timeline for an incident.

    Use this only when the distilled evidence block was flagged LOW confidence
    and the compressed context is insufficient to confirm the root cause.

    Args:
        incident_id: The incident id shown in the distilled evidence block
                     (line "Incident: <id>").
        source: The evidence source to expand (e.g. "events.txt",
                "<pod>-logs.txt", "node-<name>.txt"). If omitted, returns the
                list of available sources for the incident.
        max_lines: Maximum number of lines to return (default 400).

    Returns:
        Either the available source names, or the full timeline for one source
        with per-line signal classification.
    """
    config = get_tool_config("get_raw_timeline")
    timeout = float(config.get("timeout", 30))
    base = config.get("evidence_api_base", EVIDENCE_API_BASE).rstrip("/")

    async with httpx.AsyncClient(timeout=timeout) as client:
        # No source → list what's available for this incident.
        if not source:
            resp = await client.get(
                f"{base}/evidence/sources",
                params={"incident_id": incident_id},
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "incident_id": incident_id,
                "available_sources": data.get("sources", []),
                "hint": "call get_raw_timeline again with one of these as `source`",
            }

        resp = await client.get(
            f"{base}/evidence/timeline",
            params={
                "incident_id": incident_id,
                "source": source,
                "max_lines": max_lines,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    snippets = data.get("snippets", [])
    return {
        "incident_id": data.get("incident_id", incident_id),
        "source": data.get("source", source),
        "truncated": data.get("truncated", False),
        "line_count": len(snippets),
        "lines": [
            {"text": s.get("text"), "signal_class": s.get("signal_class")}
            for s in snippets
        ],
    }
