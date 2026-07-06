"""K8smulticluster tool for MCP server.
"""

from core.server import mcp
from core.utils import get_tool_config
# from fastmcp import FastMCP
from kubernetes import client, config
import os

# Initialize MCP Server
# mcp = FastMCP("Failover-Controller")

# Load Kubeconfig from
KUBECONFIG_PATH = os.getenv("KUBECONFIG", "/home/ubuntu/multi-cluster.kubeconfig")

def get_k8s_client(context_name):
    """Helper to load kubernetes client for a specific context."""
    try:
        # Load the sepcific context from the merged kubeconfig file
        config.load_kube_config(config_file=KUBECONFIG_PATH, context=context_name)
        return client.CoreV1Api()
    except config.config_exception.ConfigException as e:
        return f"Configuration Error: {str(e)}. Check if context '{context_name} existes in kubeconfig'"
    except Exception as e:
        return f"Error loading context '{context_name}': {str(e)}"

@mcp.tool()
def get_pods(context: str, namespace: str="default") -> str:
    """
    List pods in a specific cluster context.

    Args:
        context: The name of the cluster context (e.g., 'edge1', '
        edge2', 'kubernetes')
        namespace: the namespace to lsit pods from (default: 'default').
    """
    v1 = get_k8s_client(context)

    # If get_k8s_client returned an error string, return it immediately
    if isinstance(v1, str):
        return v1
    try:
        pods = v1.list_namespaced_pod(namespace)

        if not pods.items:
            return f"No pods found in namespace '{namespace}' on context '{context}'."

        # Format the output for LLM agent
        output = [f"Pods in {context} ({namespace}):"]
        for p in pods.items:
            status = p.status.phase
            # Add readiness info if available
            ready_count = 0
            total_containers = 0
            if p.status.container_statuses:
                total_containers = len(p.status.container_statuses)
                ready_count =  sum(1 for c in p.status.container_statuses if c.ready)
            
            output.append(f"- {p.metadata.name} {status} ({ready_count}/{total_containers} ready) - IP: {p.status.pod_ip}")
        return "\n".join(output)
    except client.exceptions.ApiException as e:
        if e.status == 404:
            return f"Namespace '{namespace}' not found in context '{context}'."
        return f"Kubernetes API Error on {context}: {e.reason} ({e.body})"
    except Exception as e:
        return f"Unexpected error listing pods on {context}: {str(e)}"

@mcp.tool()
def list_contexts() -> str:
    """
    List all available cluster contexts in the kubeconfig.
    """
    try:
        contexts, active_context = config.list_kube_config_contexts(config_file=KUBECONFIG_PATH)
        if not contexts:
            return "No contexts found in kubeconfig."
        
        names = [c['name'] for c in contexts]
        return f"Available Contexts: {', '.join(names)}"
    except Exception as e:
        return f"Error reading contexts: {str(e)}"