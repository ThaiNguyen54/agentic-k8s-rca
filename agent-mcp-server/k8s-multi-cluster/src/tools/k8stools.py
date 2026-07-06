"""
Custom multi-cluster Kubernetes tools for k8s-agent/validator-agent via MCP

Goals:
- Context-safe (no global kubeconfig mutation)
- Read-only
- Deterministic + compact JSON outputs
"""

import os
import time
import json
from functools import lru_cache
import base64
from pathlib import Path

from core.server import mcp  # FIX: was mcggp
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from kubernetes.config.config_exception import ConfigException
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
load_dotenv('.env.production')

KUBECONFIG_B64 = os.getenv("KUBECONFIG_B64", "")
KUBECONFIG_PATH = os.getenv("KUBECONFIG_PATH", "/tmp/multi-cluster.kubeconfig")
DEFAULT_NAMESPACE = os.getenv("DEFAULT_NAMESPACE", "default")  # FIX: was DEFAULT_NAESPACE/DEFAULT_NAESPACE typo

# -------------------------------------------------------------------
# Client helpers
# -------------------------------------------------------------------

def _events_to_lines(events: List[Any], limit: int = 50) -> List[str]:
    def event_ts(ev: Any) -> str:
        return str(
            ev.last_timestamp
            or ev.event_time
            or ev.first_timestamp
            or ev.metadata.creation_timestamp
            or ""
        )

    sorted_events = sorted(events, key=event_ts)
    lines = []
    for ev in sorted_events[-limit:]:
        lines.append(
            f"{event_ts(ev)} | {ev.type} | {ev.involved_object.kind}/{ev.involved_object.name} | {ev.reason} | {ev.message}"
        )
    return lines

def _new_api_client(context_name: str):
    """Create an isolated ApiClient for a specific kubeconfig context."""
    return config.new_client_from_config(
        config_file=KUBECONFIG_PATH,
        context=context_name,
    )


def _get_api(context_name: str, api_cls):
    """Load a Kubernetes API client instance for a specific context."""
    try:
        api_client = _new_api_client(context_name)
        return api_cls(api_client)
    except ConfigException as e:
        return f"Configuration Error: {str(e)}. Check if context '{context_name}' exists in kubeconfig."
    except Exception as e:
        return f"Error loading context '{context_name}': {str(e)}"


def get_core_v1_client(context_name: str):
    return _get_api(context_name, client.CoreV1Api)


def get_networking_v1_client(context_name: str):
    return _get_api(context_name, client.NetworkingV1Api)


def get_discovery_v1_client(context_name: str):
    return _get_api(context_name, client.DiscoveryV1Api)


def _api_error(prefix: str, e: Exception) -> str:
    if isinstance(e, client.exceptions.ApiException):
        if e.status == 404:
            return f"{prefix}: resource not found."
        return f"{prefix}: Kubernetes API Error {e.status} - {e.reason} ({e.body})"
    return f"{prefix}: {str(e)}"


def _to_pretty_json(obj: Any) -> str:
    if hasattr(obj, "to_dict"):
        obj = obj.to_dict()
    return json.dumps(obj, indent=2, default=str, ensure_ascii=False)


def _safe_name(value: Optional[str], fallback: str = "-") -> str:
    return value if value else fallback


def _pod_ready_counts(pod) -> Tuple[int, int]:
    ready_count = 0
    total = 0
    if pod.status.container_statuses:
        total = len(pod.status.container_statuses)
        ready_count = sum(1 for c in pod.status.container_statuses if c.ready)
    return ready_count, total


def _get_pod_declared_ports(pod) -> List[Dict[str, Any]]:
    ports = []
    for c in pod.spec.containers or []:
        for p in c.ports or []:
            ports.append({
                "container": c.name,
                "name": p.name,
                "containerPort": p.container_port,
                "protocol": p.protocol,
            })
    return ports


def _collect_service_backends(ingress_obj) -> List[Dict[str, str]]:
    backends = []

    if ingress_obj.spec.default_backend and ingress_obj.spec.default_backend.service:
        svc = ingress_obj.spec.default_backend.service
        backends.append({
            "host": "*",
            "path": "/",
            "service": svc.name,
            "service_port": str(svc.port.number or svc.port.name),
        })

    for rule in ingress_obj.spec.rules or []:
        host = rule.host or "*"
        if not rule.http:
            continue
        for p in rule.http.paths or []:
            svc = p.backend.service if p.backend else None
            if svc:
                backends.append({
                    "host": host,
                    "path": p.path or "/",
                    "service": svc.name,
                    "service_port": str(svc.port.number or svc.port.name),
                })

    return backends


def _resolve_selector(selector: Optional[Dict[str, str]]) -> str:
    if not selector:
        return ""
    return ",".join(f"{k}={v}" for k, v in selector.items())


def _extract_endpoint_ports(endpoints_obj) -> List[int]:
    ports = []
    for subset in endpoints_obj.subsets or []:
        for p in subset.ports or []:
            if p.port is not None:
                ports.append(int(p.port))
    return ports


def _extract_endpoint_addresses(endpoints_obj) -> List[str]:
    addresses = []
    for subset in endpoints_obj.subsets or []:
        for a in subset.addresses or []:
            addresses.append(a.ip)
    return addresses


def _resolve_service_target_ports(service_obj, pods: List[Any]) -> List[Any]:
    resolved = []
    pod_named_ports = {}
    for pod in pods:
        for c in pod.spec.containers or []:
            for p in c.ports or []:
                if p.name:
                    pod_named_ports.setdefault(p.name, set()).add(p.container_port)

    for sp in service_obj.spec.ports or []:
        target = sp.target_port if sp.target_port is not None else sp.port
        if isinstance(target, int):
            resolved.append(target)
        elif isinstance(target, str):
            named = sorted(pod_named_ports.get(target, []))
            resolved.append({"name": target, "resolved_ports": named})
        else:
            resolved.append(sp.port)
    return resolved

def ensure_kubeconfig_file():
    """Decode KUBECONFIG_B64 to KUBECONFIG_PATH if needed."""
    if KUBECONFIG_B64:
        path = Path(KUBECONFIG_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            try:
                decoded = base64.b64decode(KUBECONFIG_B64)
                path.write_bytes(decoded)
            except Exception as e:
                raise RuntimeError(f"Failed to decode KUBECONFIG_B64: {e}")

ensure_kubeconfig_file()  # Run once at module load

def _now_ms() -> int:
    return int(time.time() * 1000)

def _json(obj) -> str:
    # Always return valid JSON text (helps agents + debugging)
    return json.dumps(obj, default=str, ensure_ascii=False)

def _ok(data, **meta):
    return {"ok": True, "tsMs": _now_ms(), **meta, "data": data}

def _err(message, **meta):
    return {"ok": False, "tsMs": _now_ms(), **meta, "error": {"message": message}}

@lru_cache(maxsize=32)
def _api_client_for_context(context_name: str) -> client.ApiClient:
    # Context-safe: does not mutate global default configuration
    return config.new_client_from_config(
        config_file=KUBECONFIG_PATH,
        context=context_name,
        persist_config=False,
    )

def _core_v1(context_name: str) -> client.CoreV1Api:
    return client.CoreV1Api(_api_client_for_context(context_name))

def _events_v1(context_name: str) -> client.EventsV1Api:
    return client.EventsV1Api(_api_client_for_context(context_name))  # FIX: name was _eveents_v1

def _iso(dt):
    return dt.isoformat() if dt else None

@mcp.tool()
def list_contexts() -> str:
    try:
        contexts, active = config.list_kube_config_contexts(config_file=KUBECONFIG_PATH)
        names = [c["name"] for c in (contexts or [])]
        return _json(_ok({"contexts": names, "active": (active or {}).get("name")}, kubeconfig=KUBECONFIG_PATH))
    except Exception as e:
        return _json(_err(f"Error reading contexts: {e}", kubeconfig=KUBECONFIG_PATH))

@mcp.tool()
def get_node_ready_condition(context: str, nodeName: str) -> str:
    try:
        v1 = _core_v1(context)
        node = v1.read_node(nodeName)

        conds = node.status.conditions or []
        ready = next((c for c in conds if c.type == "Ready"), None)

        data = {
            "nodeName": nodeName,
            "readyStatus": (ready.status if ready else "Unknown"),
            "reason": (ready.reason if ready else None),
            "message": (ready.message if ready else None),
            "lastHeartbeatTime": _iso(getattr(ready, "last_heartbeat_time", None)),   # FIX: was last_heatbeat_time
            "lastTransitionTime": _iso(getattr(ready, "last_transition_time", None)),
        }
        return _json(_ok(data, context=context, tool="get_node_ready_condition"))
    except ApiException as e:
        return _json(_err(f"Kubernetes API error: {e.status} {e.reason}", context=context, nodeName=nodeName, body=getattr(e, "body", None)))
    except Exception as e:
        return _json(_err(f"Unexpected error: {e}", context=context, nodeName=nodeName))

@mcp.tool()
def describe_node_summary(context: str, nodeName: str) -> str:
    try:
        v1 = _core_v1(context)
        node = v1.read_node(nodeName)

        labels = node.metadata.labels or {}

        # Better than hardcoding 'worker': infer roles from label keys
        roles = []
        prefix = "node-role.kubernetes.io/"
        for k in labels.keys():
            if k.startswith(prefix):
                r = k[len(prefix):]
                if r:
                    roles.append(r)
        roles = sorted(set(roles))

        label_summary = {
            "kubernetes.io/hostname": labels.get("kubernetes.io/hostname"),
            "roles": roles,  # [] means "<none>" in kubectl get nodes ROLES
        }

        taints = []
        for t in (node.spec.taints or []):
            taints.append({"key": t.key, "effect": t.effect, "value": t.value})

        addresses = []
        for a in (node.status.addresses or []):
            addresses.append({"type": a.type, "address": a.address})

        node_info = node.status.node_info
        capacity = node.status.capacity or {}
        alloc = node.status.allocatable or {}

        conds = node.status.conditions or []
        ready = next((c for c in conds if c.type == "Ready"), None)

        data = {
            "nodeName": nodeName,
            "readyStatus": (ready.status if ready else "Unknown"),
            "readyReason": (ready.reason if ready else None),
            "labels": label_summary,
            "taints": taints[:5],
            "addresses": addresses[:5],
            "capacity": {k: capacity.get(k) for k in ["cpu", "memory", "pods"] if k in capacity},
            "allocatable": {k: alloc.get(k) for k in ["cpu", "memory", "pods"] if k in alloc},
            "nodeInfo": {
                "kubeletVersion": getattr(node_info, "kubelet_version", None),
                "containerRuntimeVersion": getattr(node_info, "container_runtime_version", None),
                "kernelVersion": getattr(node_info, "kernel_version", None),
                "osImage": getattr(node_info, "os_image", None),
            } if node_info else None,
        }
        return _json(_ok(data, context=context, tool="describe_node_summary"))
    except ApiException as e:
        return _json(_err(f"Kubernetes API error: {e.status} {e.reason}", context=context, nodeName=nodeName, body=getattr(e, "body", None)))
    except Exception as e:
        return _json(_err(f"Unexpected error: {e}", context=context, nodeName=nodeName))

def _sort_events(events: list) -> list:
    def ts(ev):
        for attr in ["event_time", "last_timestamp", "first_timestamp"]:
            v = getattr(ev, attr, None)
            if v:
                return v
        meta = getattr(ev, "metadata", None)
        return getattr(meta, "creation_timestamp", None) if meta else None
    return sorted(events, key=ts, reverse=True)

@mcp.tool()
def get_node_events(context: str, nodeName: str, limit: int = 10) -> str:
    limit = max(1, min(int(limit), 10))
    try:
        ev_api = _events_v1(context)
        events = ev_api.list_event_for_all_namespaces().items or []
        filtered = []
        for e in events:
            regarding = getattr(e, "regarding", None)
            if regarding and regarding.kind == "Node" and regarding.name == nodeName:
                filtered.append(e)

        filtered = _sort_events(filtered)[:limit]
        data = [{
            "type": getattr(e, "type", None),
            "reason": getattr(e, "reason", None),
            "note": getattr(e, "note", None),
            "reportingController": getattr(e, "reporting_controller", None),
            "eventTime": str(getattr(e, "event_time", None)),
        } for e in filtered]

        return _json(_ok({"nodeName": nodeName, "events": data}, context=context, tool="get_node_events"))
    except Exception:
        # Fallback CoreV1 Events
        try:
            v1 = _core_v1(context)
            events = v1.list_event_for_all_namespaces(
                field_selector=f"involvedObject.kind=Node,involvedObject.name={nodeName}"
            ).items or []
            events = _sort_events(events)[:limit]
            data = [{
                "type": e.type,
                "reason": e.reason,
                "message": e.message,
                "count": e.count,
                "lastTimestamp": str(e.last_timestamp),
            } for e in events]
            return _json(_ok({"nodeName": nodeName, "events": data}, context=context, tool="get_node_events(corev1_fallback)"))
        except ApiException as e:
            return _json(_err(f"Kubernetes API error: {e.status} {e.reason}", context=context, nodeName=nodeName, body=getattr(e, "body", None)))
        except Exception as e:
            return _json(_err(f"Unexpected error: {e}", context=context, nodeName=nodeName))

@mcp.tool()
def get_recent_pod_events(context: str, nodeName: str, namespace: str = DEFAULT_NAMESPACE, limit: int = 10) -> str:
    limit = max(1, min(int(limit), 10))
    try:
        v1 = _core_v1(context)

        pods = v1.list_namespaced_pod(
            namespace=namespace,
            field_selector=f"spec.nodeName={nodeName}"
        ).items or []

        pods = pods[:5]
        results = []

        for p in pods:
            pname = p.metadata.name
            evs = v1.list_namespaced_event(
                namespace=namespace,
                field_selector=f"involvedObject.kind=Pod,involvedObject.name={pname}"
            ).items or []
            evs = _sort_events(evs)[:limit]
            results.append({
                "pod": pname,
                "phase": p.status.phase,
                "events": [{
                    "type": e.type,
                    "reason": e.reason,
                    "message": e.message,
                    "lastTimestamp": str(e.last_timestamp)
                } for e in evs]
            })

        return _json(_ok({"nodeName": nodeName, "namespace": namespace, "pods": results}, context=context, tool="get_recent_pod_events"))
    except ApiException as e:
        return _json(_err(f"Kubernetes API error: {e.status} {e.reason}", context=context, nodeName=nodeName, namespace=namespace, body=getattr(e, "body", None)))
    except Exception as e:
        return _json(_err(f"Unexpected error: {e}", context=context, nodeName=nodeName, namespace=namespace))

@mcp.tool()
def get_pod_logs(context: str, namespace: str, pod: str, container: str = None, tail: int = 20) -> str:
    tail = max(1, min(int(tail), 200))
    try:
        v1 = _core_v1(context)
        log = v1.read_namespaced_pod_log(
            name=pod,
            namespace=namespace,
            container=container,
            tail_lines=tail,
            timestamps=True,
        )
        lines = log.splitlines()[-tail:]
        return _json(_ok({"namespace": namespace, "pod": pod, "container": container, "tail": tail, "lines": lines},
                         context=context, tool="get_pod_logs"))
    except ApiException as e:
        return _json(_err(f"Kubernetes API error: {e.status} {e.reason}", context=context, namespace=namespace, pod=pod, container=container, body=getattr(e, "body", None)))
    except Exception as e:
        return _json(_err(f"Unexpected error: {e}", context=context, namespace=namespace, pod=pod, container=container))





# =========== VALIDATOR'S TOLLS ==========================
@mcp.tool()
def verify_node_state(context: str, nodeName: str) -> str:
    """Verify current node Ready state + timestamps."""
    try:
        v1 = _core_v1(context)
        node = v1.read_node(nodeName)

        conds = node.status.conditions or []
        ready = next((c for c in conds if c.type == "Ready"), None)
        not_ready = next((c for c in conds if c.type == "NetworkUnavailable"), None)

        data = {
            "nodeName": nodeName,
            "phase": node.status.phase,
            "ready": {
                "status": (ready.status if ready else "Unknown"),
                "lastTransitionTime": _iso(getattr(ready, "last_transition_time", None)),
                "reason": getattr(ready, "reason", None),
            },
            "network": {
                "status": (not_ready.status if not_ready else "Unknown"),
                "lastTransitionTime": _iso(getattr(not_ready, "last_transition_time", None)),
            },
            "conditions": [
                {"type": c.type, "status": c.status, "reason": c.reason, "lastTransition": _iso(c.last_transition_time)}
                for c in conds[:5]  # top 5 conditions
            ]
        }
        return _json(_ok(data, context=context, tool="verify_node_state"))
    except Exception as e:
        return _json(_err(f"Node state verification failed: {e}", context=context, nodeName=nodeName))

@mcp.tool()
def get_node_condition_history(context: str, nodeName: str, condition_type: str = "Ready", limit: int = 3) -> str:
    """Get recent condition changes for a node (via events)."""
    limit = max(1, min(int(limit), 5))
    try:
        v1 = _core_v1(context)
        events = v1.list_event_for_all_namespaces(
            field_selector=f"involvedObject.kind=Node,involvedObject.name={nodeName},reason=~StatusUpdate"
        ).items or []
        events = _sort_events(events)[:limit]
        data = [{
            "reason": e.reason,
            "message": e.message,
            "count": e.count,
            "lastTimestamp": str(e.last_timestamp),
            "type": e.type
        } for e in events if condition_type.lower() in e.message.lower()]
        return _json(_ok({"nodeName": nodeName, "condition": condition_type, "changes": data}, context=context, tool="get_node_condition_history"))
    except Exception as e:
        return _json(_err(f"Condition history failed: {e}", context=context, nodeName=nodeName))

@mcp.tool()
def get_related_events(context: str, nodeName: str, limit: int = 5) -> str:
    """Lightweight: last N events mentioning this node name (any object)."""
    limit = max(1, min(int(limit), 10))
    try:
        v1 = _core_v1(context)
        events = v1.list_event_for_all_namespaces(
            field_selector=f"involvedObject.name={nodeName}"
        ).items or []
        events = _sort_events(events)[:limit]
        data = [{
            "type": e.type,
            "reason": e.reason,
            "message": e.message[:100],  # truncate
            "count": e.count,
            "lastTimestamp": str(e.last_timestamp)
        } for e in events]
        return _json(_ok({"nodeName": nodeName, "events": data}, context=context, tool="get_related_events"))
    except Exception as e:
        return _json(_err(f"Related events failed: {e}", context=context, nodeName=nodeName))


# -------------------------------------------------------------------
# New RCA-friendly tools
# -------------------------------------------------------------------
@mcp.tool()
def get_pods_by_label(context: str, namespace: str = "default", label_selector: str = "") -> str:
    """
    List pods using a label selector.
    """
    v1 = get_core_v1_client(context)
    if isinstance(v1, str):
        return v1

    try:
        pods = v1.list_namespaced_pod(namespace=namespace, label_selector=label_selector)

        if not pods.items:
            return f"No pods found in namespace '{namespace}' on context '{context}' for selector '{label_selector}'."

        rows = []
        for p in pods.items:
            ready_count, total = _pod_ready_counts(p)
            rows.append({
                "name": p.metadata.name,
                "phase": p.status.phase,
                "ready": f"{ready_count}/{total}",
                "pod_ip": p.status.pod_ip,
                "node": p.spec.node_name,
                "declared_ports": _get_pod_declared_ports(p),
            })

        return _to_pretty_json({
            "context": context,
            "namespace": namespace,
            "label_selector": label_selector,
            "pods": rows,
        })
    except Exception as e:
        return _api_error(f"Failed to get labeled pods on {context}", e)


@mcp.tool()
def get_services(context: str, namespace: str = "default") -> str:
    """
    List services in a namespace.
    """
    v1 = get_core_v1_client(context)
    if isinstance(v1, str):
        return v1

    try:
        services = v1.list_namespaced_service(namespace=namespace)
        items = []
        for s in services.items:
            items.append({
                "name": s.metadata.name,
                "type": s.spec.type,
                "cluster_ip": s.spec.cluster_ip,
                "selector": s.spec.selector,
                "ports": [
                    {
                        "name": p.name,
                        "port": p.port,
                        "targetPort": p.target_port,
                        "nodePort": getattr(p, "node_port", None),
                        "protocol": p.protocol,
                    }
                    for p in s.spec.ports or []
                ],
            })
        return _to_pretty_json({
            "context": context,
            "namespace": namespace,
            "services": items,
        })
    except Exception as e:
        return _api_error(f"Failed to list services on {context}", e)


@mcp.tool()
def get_service_details(context: str, service_name: str, namespace: str = "default") -> str:
    """
    Get a service as JSON.
    """
    v1 = get_core_v1_client(context)
    if isinstance(v1, str):
        return v1

    try:
        svc = v1.read_namespaced_service(name=service_name, namespace=namespace)
        return _to_pretty_json(svc)
    except Exception as e:
        return _api_error(f"Failed to read service '{service_name}' on {context}", e)


@mcp.tool()
def get_endpoints(context: str, service_name: str, namespace: str = "default") -> str:
    """
    Get Endpoints for a service.
    """
    v1 = get_core_v1_client(context)
    if isinstance(v1, str):
        return v1

    try:
        eps = v1.read_namespaced_endpoints(name=service_name, namespace=namespace)
        return _to_pretty_json(eps)
    except Exception as e:
        return _api_error(f"Failed to read endpoints for '{service_name}' on {context}", e)


@mcp.tool()
def get_endpointslices(context: str, service_name: str, namespace: str = "default") -> str:
    """
    Get EndpointSlices for a service.
    """
    discovery = get_discovery_v1_client(context)
    if isinstance(discovery, str):
        return discovery

    try:
        eps = discovery.list_namespaced_endpoint_slice(
            namespace=namespace,
            label_selector=f"kubernetes.io/service-name={service_name}",
        )
        return _to_pretty_json(eps)
    except Exception as e:
        return _api_error(f"Failed to read endpoint slices for '{service_name}' on {context}", e)


@mcp.tool()
def get_ingresses(context: str, namespace: str = "default") -> str:
    """
    List ingresses in a namespace.
    """
    net = get_networking_v1_client(context)
    if isinstance(net, str):
        return net

    try:
        ingresses = net.list_namespaced_ingress(namespace=namespace)
        rows = []
        for ing in ingresses.items:
            rows.append({
                "name": ing.metadata.name,
                "ingressClassName": ing.spec.ingress_class_name,
                "rules": _collect_service_backends(ing),
                "address": getattr(ing.status.load_balancer, "ingress", None),
            })
        return _to_pretty_json({
            "context": context,
            "namespace": namespace,
            "ingresses": rows,
        })
    except Exception as e:
        return _api_error(f"Failed to list ingresses on {context}", e)


@mcp.tool()
def describe_ingress(context: str, ingress_name: str, namespace: str = "default") -> str:
    """
    Get a specific ingress as JSON.
    """
    net = get_networking_v1_client(context)
    if isinstance(net, str):
        return net

    try:
        ing = net.read_namespaced_ingress(name=ingress_name, namespace=namespace)
        return _to_pretty_json(ing)
    except Exception as e:
        return _api_error(f"Failed to read ingress '{ingress_name}' on {context}", e)


@mcp.tool()
def get_events(context: str, namespace: str = "default", limit: int = 50) -> str:
    """
    Get recent namespace events, sorted by time.
    """
    v1 = get_core_v1_client(context)
    if isinstance(v1, str):
        return v1

    try:
        events = v1.list_namespaced_event(namespace=namespace)
        lines = _events_to_lines(events.items, limit=limit)
        if not lines:
            return f"No events found in namespace '{namespace}' on context '{context}'."
        return "\n".join(lines)
    except Exception as e:
        return _api_error(f"Failed to get events on {context}", e)


@mcp.tool()
def get_pod_logs(
    context: str,
    pod_name: str,
    namespace: str = "default",
    container: Optional[str] = None,
    tail_lines: int = 200,
) -> str:
    """
    Get logs from a specific pod.
    """
    v1 = get_core_v1_client(context)
    if isinstance(v1, str):
        return v1

    try:
        return v1.read_namespaced_pod_log(
            name=pod_name,
            namespace=namespace,
            container=container,
            tail_lines=tail_lines,
            timestamps=True,
        )
    except Exception as e:
        return _api_error(f"Failed to get logs for pod '{pod_name}' on {context}", e)


@mcp.tool()
def get_logs_by_selector(
    context: str,
    namespace: str,
    label_selector: str,
    tail_lines: int = 200,
    container: Optional[str] = None,
) -> str:
    """
    Get logs from all pods matching a label selector.
    """
    v1 = get_core_v1_client(context)
    if isinstance(v1, str):
        return v1

    try:
        pods = v1.list_namespaced_pod(namespace=namespace, label_selector=label_selector)
        if not pods.items:
            return f"No pods found for selector '{label_selector}' in namespace '{namespace}' on context '{context}'."

        chunks = []
        for pod in pods.items:
            try:
                text = v1.read_namespaced_pod_log(
                    name=pod.metadata.name,
                    namespace=namespace,
                    container=container,
                    tail_lines=tail_lines,
                    timestamps=True,
                )
                chunks.append(f"===== {pod.metadata.name} =====\n{text}")
            except Exception as e:
                chunks.append(f"===== {pod.metadata.name} =====\nERROR: {str(e)}")

        return "\n\n".join(chunks)
    except Exception as e:
        return _api_error(f"Failed to get logs by selector on {context}", e)


@mcp.tool()
def analyze_service_port_mismatch(
    context: str,
    service_name: str,
    namespace: str = "default",
) -> str:
    """
    Analyze whether a Service may be misconfigured to forward traffic to the wrong backend port.
    """
    v1 = get_core_v1_client(context)
    if isinstance(v1, str):
        return v1

    try:
        svc = v1.read_namespaced_service(name=service_name, namespace=namespace)
        eps = v1.read_namespaced_endpoints(name=service_name, namespace=namespace)

        selector = svc.spec.selector or {}
        selector_str = _resolve_selector(selector)
        pods = []
        if selector_str:
            pod_list = v1.list_namespaced_pod(namespace=namespace, label_selector=selector_str)
            pods = pod_list.items

        endpoint_ports = _extract_endpoint_ports(eps)
        endpoint_ips = _extract_endpoint_addresses(eps)
        resolved_targets = _resolve_service_target_ports(svc, pods)

        pod_summaries = []
        declared_container_ports = set()
        named_container_ports = {}
        for pod in pods:
            declared = _get_pod_declared_ports(pod)
            pod_summaries.append({
                "name": pod.metadata.name,
                "pod_ip": pod.status.pod_ip,
                "phase": pod.status.phase,
                "declared_ports": declared,
            })
            for dp in declared:
                if dp["containerPort"] is not None:
                    declared_container_ports.add(dp["containerPort"])
                if dp["name"]:
                    named_container_ports.setdefault(dp["name"], set()).add(dp["containerPort"])

        findings = []
        recommendations = []

        for sp in svc.spec.ports or []:
            target = sp.target_port if sp.target_port is not None else sp.port
            if isinstance(target, int):
                if declared_container_ports and target not in declared_container_ports:
                    findings.append(
                        f"Service targetPort {target} is not declared in selected pod containerPorts "
                        f"{sorted(declared_container_ports)}."
                    )
            elif isinstance(target, str):
                if target not in named_container_ports:
                    findings.append(
                        f"Service named targetPort '{target}' does not match any declared pod port names."
                    )

        if endpoint_ports and declared_container_ports:
            bad_ep_ports = [p for p in endpoint_ports if p not in declared_container_ports]
            if bad_ep_ports:
                findings.append(
                    f"Endpoints expose ports {sorted(set(bad_ep_ports))}, but selected pods only declare "
                    f"{sorted(declared_container_ports)}."
                )

        if not endpoint_ips:
            findings.append("Service has no endpoint addresses.")
            recommendations.append("Check pod labels, selector match, and readiness.")
        else:
            recommendations.append("Compare endpoint ports with the application's actual listen port.")
            recommendations.append("If ingress returns 502, inspect ingress controller logs for upstream connection errors.")

        if not findings:
            findings.append("No obvious Service-to-pod port mismatch detected from Kubernetes declarations alone.")

        return _to_pretty_json({
            "context": context,
            "namespace": namespace,
            "service": service_name,
            "service_ports": [
                {
                    "name": p.name,
                    "port": p.port,
                    "targetPort": p.target_port,
                    "protocol": p.protocol,
                }
                for p in svc.spec.ports or []
            ],
            "selector": selector,
            "endpoint_ips": endpoint_ips,
            "endpoint_ports": endpoint_ports,
            "resolved_target_ports": resolved_targets,
            "selected_pods": pod_summaries,
            "findings": findings,
            "recommendations": recommendations,
        })
    except Exception as e:
        return _api_error(f"Failed to analyze service '{service_name}' on {context}", e)


@mcp.tool()
def analyze_ingress_502(
    context: str,
    ingress_name: str,
    namespace: str = "default",
    ingress_controller_namespace: str = "ingress-nginx",
    controller_label_selector: str = "app.kubernetes.io/component=controller",
) -> str:
    """
    Analyze a likely 502 Bad Gateway issue for an ingress by correlating:
    - ingress backend mapping
    - service targetPort
    - endpoints
    - selected pods
    - ingress controller logs
    """
    v1 = get_core_v1_client(context)
    if isinstance(v1, str):
        return v1

    net = get_networking_v1_client(context)
    if isinstance(net, str):
        return net

    try:
        ing = net.read_namespaced_ingress(name=ingress_name, namespace=namespace)
        backends = _collect_service_backends(ing)

        controller_pods = v1.list_namespaced_pod(
            namespace=ingress_controller_namespace,
            label_selector=controller_label_selector,
        )

        controller_logs = []
        for pod in controller_pods.items:
            try:
                text = v1.read_namespaced_pod_log(
                    name=pod.metadata.name,
                    namespace=ingress_controller_namespace,
                    tail_lines=300,
                    timestamps=True,
                )
                controller_logs.extend(text.splitlines())
            except Exception as e:
                controller_logs.append(f"ERROR reading controller logs from {pod.metadata.name}: {str(e)}")

        relevant_log_lines = []
        ingress_hosts = {b["host"] for b in backends}
        for line in controller_logs:
            if (
                "502" in line
                or "connect() failed" in line
                or "upstream" in line
                or any(host != "*" and host in line for host in ingress_hosts)
            ):
                relevant_log_lines.append(line)

        services_report = []
        findings = []
        recommendations = []

        for backend in backends:
            svc_name = backend["service"]
            try:
                svc = v1.read_namespaced_service(name=svc_name, namespace=namespace)
                eps = v1.read_namespaced_endpoints(name=svc_name, namespace=namespace)

                selector = svc.spec.selector or {}
                selector_str = _resolve_selector(selector)
                pods = []
                if selector_str:
                    pod_list = v1.list_namespaced_pod(namespace=namespace, label_selector=selector_str)
                    pods = pod_list.items

                declared_ports = []
                declared_port_set = set()
                for pod in pods:
                    for dp in _get_pod_declared_ports(pod):
                        declared_ports.append({
                            "pod": pod.metadata.name,
                            **dp,
                        })
                        if dp["containerPort"] is not None:
                            declared_port_set.add(dp["containerPort"])

                endpoint_ports = _extract_endpoint_ports(eps)
                endpoint_ips = _extract_endpoint_addresses(eps)

                services_report.append({
                    "backend": backend,
                    "service_ports": [
                        {
                            "name": p.name,
                            "port": p.port,
                            "targetPort": p.target_port,
                            "protocol": p.protocol,
                        }
                        for p in svc.spec.ports or []
                    ],
                    "selector": selector,
                    "endpoint_ips": endpoint_ips,
                    "endpoint_ports": endpoint_ports,
                    "selected_pods_declared_ports": declared_ports,
                })

                if endpoint_ports and declared_port_set:
                    mismatched = [p for p in endpoint_ports if p not in declared_port_set]
                    if mismatched:
                        findings.append(
                            f"Backend service '{svc_name}' resolves to endpoint port(s) {sorted(set(mismatched))}, "
                            f"while selected pods only declare container port(s) {sorted(declared_port_set)}."
                        )

            except Exception as e:
                services_report.append({
                    "backend": backend,
                    "error": str(e),
                })

        if any("connect() failed" in line for line in relevant_log_lines):
            findings.append("Ingress controller logs show upstream TCP connection failures.")
        if any("Connection refused" in line or "(111:" in line for line in relevant_log_lines):
            findings.append("Ingress controller logs show upstream connection refused, which strongly suggests the backend port is wrong or nothing is listening on that port.")
        if any(" 502 " in line or '" 502 ' in line for line in relevant_log_lines):
            findings.append("Ingress controller access logs confirm client-facing 502 responses.")

        if not findings:
            findings.append("No explicit 502 signature found in current controller logs; inspect a wider time window or verify the ingress controller namespace and label selector.")

        recommendations.extend([
            "Compare Service targetPort with the application's real listening port.",
            "Check Endpoints and EndpointSlices immediately after any Service patch.",
            "If logs show upstream connection refused, fix the backend port mapping before changing ingress rules.",
            "Re-test through ingress with the correct Host header after restoring the Service targetPort.",
        ])

        return _to_pretty_json({
            "context": context,
            "namespace": namespace,
            "ingress": {
                "name": ing.metadata.name,
                "ingressClassName": ing.spec.ingress_class_name,
                "backends": backends,
            },
            "services_report": services_report,
            "controller_log_matches": relevant_log_lines[-50:],
            "findings": findings,
            "recommendations": recommendations,
        })
    except Exception as e:
        return _api_error(f"Failed to analyze ingress '{ingress_name}' on {context}", e)