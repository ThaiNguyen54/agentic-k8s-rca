"""Transition tool for MCP server"""
import json
from typing import Optional, Any, Dict

import httpx

from core.server import mcp
from core.utils import get_tool_config

@mcp.tool()
def transition(
    clusterName: str,
    nodeName: Optional[str] = None,
    reason: str = "",
    triggerType: str = "mcp",
    dryRun: bool = False,
) -> str:
    """Call the transition-operator/transition API

    Args:
        clusterName: Required (e.g., "edge1")
        nodeName: Optional node FQDN; if provided, server triggers node-scoped migration
        reason: Optional human-readable reason
        triggerType: Optional string tag ("mcp" by default)
        dryRun: If true, request a dry-run (no migration)

    Returns:
        str: JSON response (pretty-printed) or an error message
    """
    config = get_tool_config("transition")

    # Recommended kmcp.yaml keys (with default)
    url = config.get("url", "http://192.168.28.102:8080/transition")
    timeout = float(config.get("timeoutSeconds", 30))
    verify_tls = bool(config.get("verifyTLS", True)) 

    headers: Dict[str, str] = {"Content-Type": "application/json"}
    # # Optional auth patterns (pick one in kmcp.yaml):
    # # - headers: {Authorization: "Bearer <token>"}
    # # - bearerToken: "<token>"
    # headers.update(config.get("headers", {}) or {})
    # bearer = config.get("bearerToken")
    # if bearer and "Authorization" not in headers:
    #     headers["Authorization"] = f"Bearer {bearer}"

    payload: Dict[str, Any] = {
        "clusterName": clusterName,
        "reason": reason,
        "triggerType": triggerType,
        "dryRun": dryRun
    }
    if nodeName:
        payload["nodeName"] = nodeName

    try:
        with httpx.Client(timeout=timeout, verify=verify_tls) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            # Return pretty JSON for the agent to read
            return json.dumps(resp.json(), indent=2, ensure_ascii=False)
    except httpx.HTTPStatusError as e:
        body = e.response.text if e.response is not None else ""
        return f"transition API error: {e}\nresponse body: {body}"
    except Exception as e:
        return f"transition tool failed: {e}"

