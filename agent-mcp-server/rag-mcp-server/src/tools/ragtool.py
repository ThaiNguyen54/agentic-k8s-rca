import os
import httpx
from core.server import mcp
from core.utils import get_tool_config

RAG_API_BASE = os.getenv(
    "RAG_API_BASE",
    "http://192.168.24.21:8080",
)

@mcp.tool()
async def search_rca_chunks(
    question: str,
    namespace: str | None = None,
    resource_kind: str | None = None,
    source_type: str = "config_yaml",
    top_k: int = 5,
):
    config = get_tool_config("search_rca_chunks")
    timeout = float(config.get("timeout", 30))

    payload = {
        "question": question,
        "namespace": namespace,
        "resource_kind": resource_kind,
        "source_type": source_type,
        "top_k": top_k
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{RAG_API_BASE}/rag/search", json=payload)
        resp.raise_for_status()
        result = resp.json()

    return {
        "query": payload,
        "result_count": len(result.get("documents", [])),
        "documents": result.get("documents", []),
        "metadatas": result.get("metadatas", []),
        "prompt": result.get("prompt")
    }

@mcp.tool()
async def ask_rca_rag(
    question: str,
    namespace: str | None = None,
    resource_kind: str | None = None,
    source_type: str = "config_yaml"
):
    config = get_tool_config("ask_rca_rag")
    timeout= float(config.get("timeout", 60))

    payload = {
        "question": question,
        "namespace": namespace,
        "resource_kine": resource_kind,
        "source_type": source_type
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{RAG_API_BASE}/rag/ask", json=payload)
        resp.raise_for_status()
        result = resp.json()

        return {
            "query": payload,
            "answer": result.get("answer"),
            "documents": result.get("documents", []),
            "metadatas": result.get("metadatas", [])
        }