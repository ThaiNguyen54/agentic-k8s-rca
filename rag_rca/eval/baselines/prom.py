# eval/baselines/prom.py
"""Thin Prometheus HTTP client for the MicroRCA baseline.

Reads the service call graph, per-edge response times, and per-service resource
utilisation that MicroRCA consumes. Only stdlib + requests; base URL from
$PROM_URL (default a local port-forward to the kube-prometheus-stack).

    kubectl -n monitoring port-forward svc/prometheus-kube-prometheus-prometheus 9090:9090
"""
import os
import time
import urllib.parse
import urllib.request
import json


class Prom:
    def __init__(self, url=None, timeout=20):
        self.url = (url or os.getenv("PROM_URL", "http://127.0.0.1:9090")).rstrip("/")
        self.timeout = timeout

    def _get(self, path, params):
        q = urllib.parse.urlencode(params)
        with urllib.request.urlopen(f"{self.url}{path}?{q}", timeout=self.timeout) as r:
            d = json.loads(r.read())
        if d.get("status") != "success":
            raise RuntimeError(f"prom error: {d}")
        return d["data"]

    def query(self, expr, t=None):
        """Instant query -> list of (labels dict, float value)."""
        params = {"query": expr}
        if t is not None:
            params["time"] = t
        res = self._get("/api/v1/query", params)["result"]
        return [(r["metric"], float(r["value"][1])) for r in res]

    def query_range(self, expr, start, end, step=15):
        """Range query -> list of (labels dict, [(ts, float), ...])."""
        data = self._get("/api/v1/query_range",
                         {"query": expr, "start": start, "end": end, "step": step})
        out = []
        for r in data["result"]:
            series = [(float(ts), float(v)) for ts, v in r["values"]]
            out.append((r["metric"], series))
        return out

    def up(self):
        try:
            return bool(self.query("vector(1)"))
        except Exception:
            return False


# --- MicroRCA-specific extractors (Istio + cAdvisor) -----------------------

def call_graph(prom, ns, start, end, rate_window="1m"):
    """Directed service call graph: {(src, dst): request_rate} over the window.

    Uses istio_requests_total. Restricts to workloads whose destination is in `ns`.
    """
    expr = (f'sum by (source_workload, destination_workload) '
            f'(rate(istio_requests_total{{destination_workload_namespace="{ns}"}}[{rate_window}]))')
    edges = {}
    for m, v in prom.query(expr, t=end):
        s, d = m.get("source_workload", "unknown"), m.get("destination_workload", "unknown")
        if s in ("unknown", "") or d in ("unknown", ""):
            continue
        edges[(s, d)] = v
    return edges


def edge_latency_series(prom, ns, start, end, step=15, quantile=0.5):
    """Per-edge response-time series (ms): {(src,dst): [(ts, p50_ms), ...]}.

    histogram_quantile over istio_request_duration_milliseconds_bucket — this is
    the 'response time' signal MicroRCA detects anomalies on.
    """
    expr = (f'histogram_quantile({quantile}, sum by (source_workload, destination_workload, le) '
            f'(rate(istio_request_duration_milliseconds_bucket{{destination_workload_namespace="{ns}"}}[1m])))')
    out = {}
    for m, series in prom.query_range(expr, start, end, step):
        s, d = m.get("source_workload", "unknown"), m.get("destination_workload", "unknown")
        if s in ("unknown", "") or d in ("unknown", ""):
            continue
        out[(s, d)] = [(ts, v) for ts, v in series if v == v]  # drop NaN
    return out


def resource_series(prom, ns, start, end, step=15):
    """Per-service resource utilisation series: {service: {metric: [(ts,val)...]}}.

    CPU (rate of usage seconds), memory (working set), network (rx+tx rate).
    Keyed by the pod's owning workload (label_app / pod prefix).
    """
    metrics = {
        "cpu": f'sum by (pod) (rate(container_cpu_usage_seconds_total{{namespace="{ns}", container!="", container!="POD"}}[1m]))',
        "mem": f'sum by (pod) (container_memory_working_set_bytes{{namespace="{ns}", container!="", container!="POD"}})',
        "net": f'sum by (pod) (rate(container_network_receive_bytes_total{{namespace="{ns}"}}[1m]) '
               f'+ rate(container_network_transmit_bytes_total{{namespace="{ns}"}}[1m]))',
    }
    out = {}
    for name, expr in metrics.items():
        for m, series in prom.query_range(expr, start, end, step):
            svc = _workload_of_pod(m.get("pod", ""))
            if not svc:
                continue
            out.setdefault(svc, {}).setdefault(name, [])
            # a workload can have several pods; sum aligned-ish by appending (mean later)
            out[svc][name].append([(ts, v) for ts, v in series if v == v])
    # collapse multiple pods per workload into a single mean series
    for svc, mm in out.items():
        for name, list_of_series in mm.items():
            mm[name] = _mean_align(list_of_series)
    return out


def _workload_of_pod(pod):
    """checkoutservice-56c5cffd57-mqbx6 -> checkoutservice (strip rs hash + pod hash)."""
    if not pod:
        return ""
    parts = pod.split("-")
    if len(parts) >= 3:
        return "-".join(parts[:-2])
    return pod


def _mean_align(list_of_series):
    """Average several [(ts,v)] series by timestamp."""
    from collections import defaultdict
    acc = defaultdict(list)
    for s in list_of_series:
        for ts, v in s:
            acc[ts].append(v)
    return [(ts, sum(vs) / len(vs)) for ts, vs in sorted(acc.items())]


def now():
    return int(time.time())
