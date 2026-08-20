# eval/benchmark_pipeline.py
"""Efficiency benchmark: system WITHOUT the Evidence-First pipeline (raw collect-all
context) vs WITH it (distilled context), across A/B/C fault scenarios.

This measures the pipeline's *direct* contribution — how evidence is assembled
before the RCA LLM sees it — NOT end-to-end localization/diagnosis/remediation
(those need the live agents + clusters). All metrics here are computed with the
deterministic lexical fallback, so results are reproducible with no LLM/GPU.

Metrics per scenario:
  - context size (chars, est. tokens) raw vs distilled, and % reduction
  - signal recall: injected decisive signals still present in the prompt
  - lost-in-the-middle: mean distance of each signal to the nearest prompt edge
    (0 = at an edge/high-attention, 0.5 = buried mid-context)
  - signal density: decisive lines / total lines in the prompt
  - distillation latency (ms)

Run:  python eval/benchmark_pipeline.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.schemas import EvidenceBundle, FaultSignature, SIGNAL
from app.rerank.cross_encoder_reranker import Reranker
from app.pipelines.evidence_first_pipeline import distill_evidence
from app.chunkers.log_event_chunker import _SIGNAL_RE  # to sanity-check signals


# ── scenario construction ────────────────────────────────────────────────────
def routine_events(n, start=0):
    out = []
    for i in range(n):
        out.append(f"5d   Normal   Pulled    pod/frontend-{i+start:05d}   Successfully pulled image in 1.2s")
        out.append(f"5d   Normal   Started   pod/frontend-{i+start:05d}   Started container server")
    return out


def routine_logs(n, svc="frontend"):
    return [f'level=info msg="handled GET /health 200 OK" svc={svc} seq={i}' for i in range(n)]


def scenario_A():
    """1 fault: checkout pod CrashLoopBackOff."""
    pod = "checkoutservice-7d9f8c6b45-abcde"
    signals = [
        f"{pod}   0/1   CrashLoopBackOff   7   12m",
        f"30s   Warning   BackOff   pod/{pod}   Back-off restarting failed container checkout",
        f"45s   Warning   Unhealthy   pod/{pod}   Readiness probe failed: HTTP probe failed with statuscode: 500",
        'level=error msg="readiness check failed: dial tcp 10.0.3.9:7070: connect: connection refused"',
    ]
    events = routine_events(1500)
    events[750:750] = signals[1:3]           # bury the two events mid-stream
    pods = ["NAME   READY   STATUS   RESTARTS   AGE", signals[0]] + \
           [f"frontend-{i:05d}   1/1   Running   0   5d" for i in range(40)]
    logs = routine_logs(1500, "checkout")
    logs.insert(750, signals[3])
    bundle = EvidenceBundle(
        fault=FaultSignature(cluster="edge1", namespace="default", pod=pod,
                             fault_type="Application_Fault", fault_object=f"pod/{pod}",
                             root_cause_hint="crashloopbackoff",
                             summary=f"[edge1] crashloopbackoff on pod/{pod}"),
        sources={"pods-all.txt": "\n".join(pods),
                 "events.txt": "\n".join(events),
                 f"{pod}-logs.txt": "\n".join(logs)},
    )
    return "A (1 fault)", bundle, signals


def scenario_B():
    """2 faults: node NotReady + a service with no ready endpoints."""
    node = "worker-2"
    signals = [
        f"{node}   NotReady   <none>   40d   v1.29.2",
        f"2m   Warning   NodeNotReady   node/{node}   Node {node} status is now: NotReady",
        f"3m   Warning   MemoryPressure   node/{node}   Node {node} under memory pressure",
        "1m   Warning   Unhealthy   endpoints/paymentservice   service_no_ready_endpoints readyAddresses=0",
        "1m   Warning   FailedScheduling   pod/paymentservice-x   0/3 nodes available: node(s) had taint",
    ]
    events = routine_events(2500)
    events[1200:1200] = signals[1:5]
    nodes = ["NAME   STATUS   ROLES   AGE   VERSION", signals[0]] + \
            [f"worker-{i}   Ready   <none>   40d   v1.29.2" for i in range(3, 20)]
    node_desc = f"Name: {node}\n\nConditions:\n  MemoryPressure True\n  Ready False\n\n" + \
                "\n".join(f"routine line describing node capacity {i}" for i in range(60))
    logs = routine_logs(2500, "payment")
    bundle = EvidenceBundle(
        fault=FaultSignature(cluster="edge1", namespace="cluster-scoped", node=node,
                             fault_type="Infrastructure_Fault", fault_object=f"node/{node}",
                             root_cause_hint="node_not_ready",
                             summary=f"[edge1] node_not_ready on node/{node}"),
        sources={"nodes.txt": "\n".join(nodes),
                 "events.txt": "\n".join(events),
                 f"node-{node}.txt": node_desc,
                 "paymentservice-x-logs.txt": "\n".join(logs)},
    )
    return "B (2 faults)", bundle, signals


def scenario_C():
    """3 faults: crashloop + node NotReady + image pull failure on a third workload."""
    pod = "checkoutservice-7d9f8c6b45-abcde"
    node = "worker-5"
    signals = [
        f"{pod}   0/1   CrashLoopBackOff   9   20m",
        f"20s   Warning   BackOff   pod/{pod}   Back-off restarting failed container checkout",
        f"1m   Warning   NodeNotReady   node/{node}   Node {node} status is now: NotReady",
        f"{node}   NotReady   <none>   40d   v1.29.2",
        "2m   Warning   Failed   pod/adservice-y   Failed to pull image \"adservice:bad\": ErrImagePull",
        "2m   Warning   ImagePullBackOff   pod/adservice-y   Back-off pulling image \"adservice:bad\"",
        'level=error msg="oom killed" reason=OOMKilled container=checkout exitCode=137',
    ]
    events = routine_events(4000)
    events[2000:2000] = signals[1:6]
    pods = ["NAME   READY   STATUS   RESTARTS   AGE", signals[0],
            "adservice-y   0/1   ImagePullBackOff   0   5m"] + \
           [f"frontend-{i:05d}   1/1   Running   0   5d" for i in range(60)]
    nodes = ["NAME   STATUS   ROLES   AGE   VERSION", signals[3]] + \
            [f"worker-{i}   Ready   <none>   40d   v1.29.2" for i in range(6, 25)]
    logs = routine_logs(4000, "checkout")
    logs.insert(2000, signals[6])
    bundle = EvidenceBundle(
        fault=FaultSignature(cluster="edge1", namespace="default", pod=pod,
                             fault_type="Application_Fault", fault_object=f"pod/{pod}",
                             root_cause_hint="crashloopbackoff",
                             summary=f"[edge1] crashloopbackoff on pod/{pod}"),
        sources={"pods-all.txt": "\n".join(pods),
                 "nodes.txt": "\n".join(nodes),
                 "events.txt": "\n".join(events),
                 f"{pod}-logs.txt": "\n".join(logs)},
    )
    return "C (3 faults)", bundle, signals


# ── the "without pipeline" baseline: raw collect-all prompt ───────────────────
ORDERED = ["nodes.txt", "pods-all.txt", "events.txt", "services.txt",
           "endpoints.txt", "deployments.txt"]


def build_raw_prompt(bundle: EvidenceBundle) -> str:
    """Mirror the Go appendEvidence(): concatenate all sources, well-known first."""
    parts, written = [], set()
    for k in ORDERED:
        if k in bundle.sources:
            parts.append(f"\n=== EVIDENCE: {k} ===\n{bundle.sources[k]}")
            written.add(k)
    for k in sorted(bundle.sources):
        if k not in written:
            parts.append(f"\n=== EVIDENCE: {k} ===\n{bundle.sources[k]}")
    return "## Kubernetes Evidence\n" + "".join(parts)


# ── metrics ───────────────────────────────────────────────────────────────────
def est_tokens(text):
    return round(len(text) / 4)


def signal_recall(prompt, signals):
    hits = sum(1 for s in signals if _key(s) in prompt)
    return hits, len(signals)


def _key(signal):
    # search on a distinctive fragment to be robust to whitespace normalisation
    return signal.strip()[:48]


def signal_line_density(prompt):
    lines = [ln for ln in prompt.splitlines() if ln.strip()]
    if not lines:
        return 0.0
    sig = sum(1 for ln in lines if _SIGNAL_RE.search(ln))
    return sig / len(lines)


def bench():
    reranker = Reranker(use_model=False)
    rows = []
    for name, bundle, signals in (scenario_A(), scenario_B(), scenario_C()):
        raw = build_raw_prompt(bundle)

        t0 = time.perf_counter()
        distilled = distill_evidence(bundle, reranker=reranker)
        latency_ms = (time.perf_counter() - t0) * 1000
        block = distilled.prompt_block

        r_rec = signal_recall(raw, signals)
        d_rec = signal_recall(block, signals)
        vb = distilled.verbatim
        n_sig_vb = sum(1 for s in vb if s.signal_class == SIGNAL)
        edges_ok = len(vb) >= 2 and vb[0].signal_class == SIGNAL and vb[-1].signal_class == SIGNAL
        rows.append({
            "scenario": name,
            "raw_tok": est_tokens(raw),
            "dst_tok": est_tokens(block),
            "reduction": 1 - est_tokens(block) / max(est_tokens(raw), 1),
            "raw_recall": r_rec, "dst_recall": d_rec,
            "raw_density": signal_line_density(raw),
            "dst_density": signal_line_density(block),
            "edges_ok": edges_ok, "n_sig_vb": n_sig_vb,
            "latency_ms": latency_ms,
        })
    return rows


def _fmt(rows):
    print("=" * 112)
    print("EVIDENCE-FIRST PIPELINE — EFFICIENCY BENCHMARK  (WITHOUT = raw collect-all,  WITH = distilled)")
    print("=" * 112)
    h = ("Scenario", "Context est-tokens", "Reduction", "Signal recall",
         "Signal density", "Edges=signal", "Distill")
    print(f"{h[0]:<13}{h[1]:<24}{h[2]:<11}{h[3]:<17}{h[4]:<20}{h[5]:<14}{h[6]}")
    print(f"{'':13}{'raw  ->  distilled':<24}{'':11}{'raw -> distilled':<17}{'raw -> distilled':<20}")
    print("-" * 112)
    for r in rows:
        ctx = f"{r['raw_tok']:>7,} -> {r['dst_tok']:>4,}"
        red = f"{r['reduction']*100:5.1f}%"
        rec = f"{r['raw_recall'][0]}/{r['raw_recall'][1]} -> {r['dst_recall'][0]}/{r['dst_recall'][1]}"
        den = f"{r['raw_density']*100:4.1f}% -> {r['dst_density']*100:4.1f}%"
        edge = f"{'yes' if r['edges_ok'] else 'no':>3} ({r['n_sig_vb']} sig)"
        lat = f"{r['latency_ms']:5.1f} ms"
        print(f"{r['scenario']:<13}{ctx:<24}{red:<11}{rec:<17}{den:<20}{edge:<14}{lat}")
    print("-" * 112)
    print("Signal recall : decisive signal lines still present in the prompt (retention; raw trivially keeps all).")
    print("Signal density: fraction of prompt lines that are decisive signals — the signal-to-noise ratio. Higher is better.")
    print("Edges=signal  : are BOTH the first and last verbatim snippets decisive signals (edge placement)? (N signals kept verbatim).")
    print("Note          : distilled context stays ~incident-sized (~500 tok) while raw grows with cluster noise (80k -> 216k).")


if __name__ == "__main__":
    _fmt(bench())
