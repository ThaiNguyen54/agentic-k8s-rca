"""Get ClusterPolicy CRD schema tool for failover-mcp-server"""
import base64
import os
import subprocess
from pathlib import Path

from core.server import mcp
from core.utils import get_tool_config

KUBECONFIG_B64 = os.getenv("KUBECONFIG_B64", "")
KUBECONFIG_PATH = os.getenv("KUBECONFIG_PATH", "/tmp/multi-cluster.kubeconfig")


def _ensure_kubeconfig() -> None:
    """Decode KUBECONFIG_B64 to KUBECONFIG_PATH if the file doesn't exist yet."""
    if not KUBECONFIG_B64:
        return
    path = Path(KUBECONFIG_PATH)
    if not path.exists():
        try:
            decoded = base64.b64decode(KUBECONFIG_B64)
            path.write_bytes(decoded)
        except Exception as e:
            raise RuntimeError(f"Failed to decode KUBECONFIG_B64: {e}")


_ensure_kubeconfig()


@mcp.tool()
def get_clusterpolicy_schema() -> str:
    """Read the ClusterPolicy CRD schema from the management cluster.

    Use this tool to understand the full spec of the ClusterPolicy custom resource
    (clusterpolicies.transition.dcnlab.ssu.ac.kr) before generating a ClusterPolicy
    manifest for workload failover via the transition operator.

    Returns:
        The ClusterPolicy CRD specification as YAML, including all fields,
        their types, and descriptions. Use this to generate a valid manifest.
    """
    config = get_tool_config("get_clusterpolicy_schema")
    mgmt_context = config.get("managementContext", "")

    cmd = ["kubectl"]
    if KUBECONFIG_B64:
        cmd += ["--kubeconfig", KUBECONFIG_PATH]
    if mgmt_context:
        cmd += ["--context", mgmt_context]
    cmd += ["get", "crd", "clusterpolicies.transition.dcnlab.ssu.ac.kr", "-o", "yaml"]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            return f"# error: {result.stderr.strip()}"
        return result.stdout
    except subprocess.TimeoutExpired:
        return "# error: kubectl timed out"
    except Exception as e:
        return f"# error: {e}"
