# eval/baselines/microrca.py
"""Faithful re-implementation of MicroRCA (Wu et al., IEEE/IFIP NOMS 2020,
"MicroRCA: Root Cause Localization of Performance Issues in Microservices").

Pipeline (matches the paper):
  1. Anomaly detection on per-edge response times (BIRCH clustering, as in the
     paper; falls back to a robust z-score detector if BIRCH degenerates).
  2. Attributed-graph construction: the service call graph, restricted to an
     anomalous subgraph (anomalous services + their neighbours), with each node
     carrying its resource-utilisation attributes (CPU / memory / network).
  3. Root-cause localisation by *personalized PageRank* over the weighted
     anomalous subgraph. Edge/self weights come from the correlation between the
     anomalous response time and each node's resource usage (Eq. 1-3 in paper):
       - forward edge (caller->callee): w = corr(anom_rt, resource(callee))
       - backward edge (callee->caller): w = rho * corr(anom_rt, resource(caller))
       - self edge:  w = corr(anom_rt, resource(self)) if it dominates neighbours
     The stationary distribution ranks candidate root-cause services.

This is a re-implementation of the *technique* for baseline comparison, not the
authors' original code. Inputs come from Istio+Prometheus (see prom.py).

Two modes:
  mode="mesh"      : full MicroRCA (service graph from Istio + response times).
  mode="resource" : degraded variant for environments with NO service mesh -
                    topology from a provided adjacency (e.g. k8s ownerRefs) and
                    anomaly signal from resource usage only. Clearly NOT the
                    published method; used only for the coverage comparison.
"""
import os
import numpy as np
import networkx as nx


RHO = 0.3          # backward-edge damping (paper's rho)
PPR_ALPHA = 0.85   # PageRank teleport
CORR_FLOOR = 1e-3  # avoid all-zero transition rows


# ---------------------------------------------------------------- utilities
def _values(series):
    return np.array([v for _, v in series], dtype=float)


def _align(a, b):
    """Align two [(ts,v)] series on shared timestamps -> (va, vb)."""
    da, db = dict(a), dict(b)
    ts = sorted(set(da) & set(db))
    return np.array([da[t] for t in ts]), np.array([db[t] for t in ts])


def _corr(a, b):
    """|Pearson| between two aligned series; 0 if undefined/constant."""
    if len(a) < 3 or len(b) < 3:
        return 0.0
    if np.std(a) < 1e-9 or np.std(b) < 1e-9:
        return 0.0
    c = np.corrcoef(a, b)[0, 1]
    return 0.0 if np.isnan(c) else abs(float(c))


# ------------------------------------------------------- anomaly detection
def detect_anomalous_edges(edge_lat, baseline_frac=0.4, sigma=3.0):
    """Return {(src,dst): anomaly_score} for edges whose response time is
    anomalous. BIRCH clustering on the standardized latency series (paper),
    with a robust-z fallback. anomaly_score = normalized latency increase.
    """
    anomalous = {}
    for edge, series in edge_lat.items():
        vals = _values(series)
        if len(vals) < 5:
            continue
        n0 = max(2, int(len(vals) * baseline_frac))
        base, rest = vals[:n0], vals[n0:]
        med = np.median(base)
        mad = np.median(np.abs(base - med)) + 1e-9
        # robust z of the worst part of the anomaly window
        z = (np.max(rest) - med) / (1.4826 * mad)
        birch_hit = _birch_flag(vals, n0)
        if z >= sigma or birch_hit:
            # score = relative latency inflation (bounded)
            score = float((np.mean(rest) - med) / (med + 1e-9))
            anomalous[edge] = max(score, 0.01)
    return anomalous


def _birch_flag(vals, n0):
    """MicroRCA uses BIRCH to cluster response times; a point landing in a new
    cluster vs the baseline cluster is flagged anomalous. Degrades gracefully."""
    try:
        from sklearn.cluster import Birch
        x = ((vals - np.mean(vals)) / (np.std(vals) + 1e-9)).reshape(-1, 1)
        labels = Birch(n_clusters=None, threshold=0.5).fit_predict(x)
        base_label = np.bincount(labels[:n0].astype(int)).argmax() if n0 else labels[0]
        # anomalous if the tail spends time in a non-baseline cluster
        return bool(np.mean(labels[n0:] != base_label) > 0.3)
    except Exception:
        return False


# ------------------------------------------------- attributed subgraph
def build_anomalous_subgraph(edges, anomalous_edges):
    """Anomalous subgraph = anomalous edges + one-hop neighbourhood."""
    anom_nodes = set()
    for (s, d) in anomalous_edges:
        anom_nodes.add(s)
        anom_nodes.add(d)
    keep_nodes = set(anom_nodes)
    for (s, d) in edges:
        if s in anom_nodes or d in anom_nodes:
            keep_nodes.add(s)
            keep_nodes.add(d)
    g = nx.DiGraph()
    for (s, d), w in edges.items():
        if s in keep_nodes and d in keep_nodes:
            g.add_edge(s, d, call_rate=w)
    return g, anom_nodes


# -------------------------------------------------- localisation (PPR)
def localize(edges, edge_lat, resources, mode="mesh", topology=None):
    """Rank candidate root-cause services. Returns list of (service, score) desc.

    edges      : {(src,dst): call_rate}          (mesh mode)
    edge_lat   : {(src,dst): [(ts,ms)...]}       (mesh mode; response times)
    resources  : {service: {cpu|mem|net: [(ts,v)...]}}
    topology   : {(src,dst): w} adjacency for resource mode (no mesh).
    """
    if mode == "resource":
        return _localize_resource(topology or {}, resources)

    anomalous_edges = detect_anomalous_edges(edge_lat)
    if not anomalous_edges:
        return []
    g, anom_nodes = build_anomalous_subgraph(edges, anomalous_edges)
    if g.number_of_nodes() == 0:
        return []

    # representative anomalous response-time signal (worst anomalous edge)
    worst_edge = max(anomalous_edges, key=anomalous_edges.get)
    anom_rt = edge_lat.get(worst_edge, [])

    def corr_node(svc):
        """max |corr| between the anomalous response time and svc's resources."""
        best = 0.0
        for _, series in resources.get(svc, {}).items():
            va, vb = _align(anom_rt, series)
            best = max(best, _corr(va, vb))
        return best

    node_corr = {n: corr_node(n) for n in g.nodes()}

    # weighted transition graph per MicroRCA (forward/backward/self)
    W = nx.DiGraph()
    W.add_nodes_from(g.nodes())
    for (s, d) in g.edges():
        W.add_edge(s, d, weight=max(node_corr[d], CORR_FLOOR))          # forward
        W.add_edge(d, s, weight=max(RHO * node_corr[s], CORR_FLOOR))    # backward
    for n in g.nodes():
        nbr = [node_corr[m] for m in set(g.successors(n)) | set(g.predecessors(n))]
        self_w = node_corr[n] - max(nbr) if nbr else node_corr[n]
        if self_w > 0:
            W.add_edge(n, n, weight=self_w)

    # personalization: mass on detected anomalous nodes
    pers = {n: (1.0 if n in anom_nodes else 0.0) for n in W.nodes()}
    if sum(pers.values()) == 0:
        pers = None
    pr = nx.pagerank(W, alpha=PPR_ALPHA, personalization=pers, weight="weight")
    # combine PPR with each node's own resource correlation (paper ranks by both)
    ranked = sorted(((n, pr[n] * (0.5 + node_corr[n])) for n in pr),
                    key=lambda kv: kv[1], reverse=True)
    return ranked


def _localize_resource(topology, resources):
    """Degraded, mesh-free variant: no response-time signal, so rank services by
    resource-usage anomaly (deviation of the tail vs the head of each series),
    propagated over a provided topology with PageRank. NOT published MicroRCA."""
    scores = {}
    for svc, mm in resources.items():
        dev = 0.0
        for _, series in mm.items():
            vals = _values(series)
            if len(vals) < 5:
                continue
            n0 = max(2, int(len(vals) * 0.4))
            base, rest = vals[:n0], vals[n0:]
            med = np.median(base)
            mad = np.median(np.abs(base - med)) + 1e-9
            dev = max(dev, abs(np.max(rest) - med) / (1.4826 * mad))
        scores[svc] = dev
    if not any(scores.values()):
        return []
    g = nx.DiGraph()
    g.add_nodes_from(scores)
    for (s, d), w in topology.items():
        if s in scores and d in scores:
            g.add_edge(s, d, weight=max(w, CORR_FLOOR))
    if g.number_of_edges() == 0:
        return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    tot = sum(scores.values()) or 1.0
    pers = {n: scores.get(n, 0.0) / tot for n in g.nodes()}
    pr = nx.pagerank(g, alpha=PPR_ALPHA, personalization=pers, weight="weight")
    return sorted(((n, pr[n] * (0.5 + scores.get(n, 0.0))) for n in pr),
                  key=lambda kv: kv[1], reverse=True)
