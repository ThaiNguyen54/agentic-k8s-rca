# eval/hard_scenarios.py
"""Harder, fair evaluation scenarios for the RCA ablation.

Difficulty (vs benchmark_pipeline scenarios):
  - decisive signals are buried DEEP and mid-stream (not in top-ordered sources),
  - adversarial red-herrings: unrelated Warning/error/OOMKilled lines AND resolved
    or historical incidents that carry fault keywords (so the model can be misled
    and keyword-scoring can't inflate raw),
  - cascades: one root cause produces many downstream symptoms that must NOT be
    reported as separate root causes,
  - distractors: plausible-but-wrong conditions that must NOT be named as the cause.

Ground truth per scenario is {"primary": [(object, cause_kws)], "distractors": [obj,...]}.
Strict scoring (below) checks the response's ROOT-CAUSE section only and penalises
naming a distractor as the cause.
"""
import re

from app.schemas import EvidenceBundle, FaultSignature


def _routine_events(n, start=0):
    out = []
    for i in range(n):
        out.append(f"5d   Normal   Pulled    pod/frontend-{i+start:05d}   Successfully pulled image")
        out.append(f"5d   Normal   Started   pod/frontend-{i+start:05d}   Started container server")
        out.append(f"4d   Normal   Scheduled pod/frontend-{i+start:05d}   Successfully assigned default/frontend-{i+start:05d}")
    return out


def _routine_logs(n, svc):
    return [f'level=info msg="handled GET /healthz 200" svc={svc} seq={i}' for i in range(n)]


# Red-herrings: carry fault keywords but are NOT the current root cause
def _red_herrings():
    return [
        "3d   Warning   BackOff        pod/legacy-cron-9        Back-off restarting failed container (RESOLVED 3d ago)",
        "2d   Warning   OOMKilled      pod/batch-report-7       Container OOMKilled during nightly job (one-off, node has capacity)",
        "6h   Warning   Unhealthy      pod/frontend-00007       Readiness probe failed once, recovered 5s later",
        "5d   Normal    Killing        pod/frontend-00012       Stopping container during rollout (expected)",
        "1d   Warning   FailedMount    pod/archive-1            transient volume attach delay, mounted after retry",
        'level=error msg="context canceled on shutdown" svc=frontend seq=deadbeef (graceful)',
    ]


def _interleave(routine, buried, red, depth=0.68):
    """Place buried signals + red-herrings inside a routine stream; buried signals
    go near `depth` fraction, red-herrings scattered earlier."""
    ev = list(routine)
    # scatter red-herrings across the first ~60%
    step = max(1, int(len(ev) * 0.6 / max(len(red), 1)))
    for i, h in enumerate(red):
        ev.insert(min(len(ev), (i + 1) * step), h)
    at = int(len(ev) * depth)
    for j, s in enumerate(buried):
        ev.insert(min(len(ev), at + j), s)
    return ev


def hard_B(noise=1800):
    """2 real root causes (node memory pressure; payment image/config), heavy
    cascades + red-herrings. Cascaded evicted pods are NOT root causes."""
    node = "worker-2"
    buried = [
        f"2m   Warning   NodeNotReady    node/{node}   Node {node} status is now: NotReady",
        f"3m   Warning   MemoryPressure  node/{node}   Node {node} under memory pressure; evicting pods",
        f"90s  Warning   Evicted         pod/cartservice-a         The node had condition: MemoryPressure",
        f"90s  Warning   Evicted         pod/recommendation-b      The node had condition: MemoryPressure",
        "1m   Warning   Failed          pod/paymentservice-x   Failed to pull image \"payment:v2-bad\": manifest unknown",
        "1m   Warning   ErrImagePull    pod/paymentservice-x   Error: ErrImagePull for image payment:v2-bad",
    ]
    events = _interleave(_routine_events(noise), buried, _red_herrings())
    nodes = ["NAME   STATUS   ROLES   AGE   VERSION",
             f"{node}   NotReady   <none>   40d   v1.29.2"] + \
            [f"worker-{i}   Ready   <none>   40d   v1.29.2" for i in range(3, 18)]
    logs = _routine_logs(noise, "payment")
    fault = FaultSignature(cluster="edge1", namespace="cluster-scoped", node=node,
                           fault_type="Infrastructure_Fault", fault_object=f"node/{node}",
                           root_cause_hint="node_not_ready",
                           summary=f"[edge1] node_not_ready on node/{node}; payment image pull failing")
    bundle = EvidenceBundle(fault=fault, sources={
        "nodes.txt": "\n".join(nodes),
        "events.txt": "\n".join(events),
        f"node-{node}.txt": f"Name: {node}\n\nConditions:\n  MemoryPressure True\n  Ready False\n",
        "paymentservice-x-logs.txt": "\n".join(logs),
    })
    gt = {"primary": [("worker-2", ["memory pressure", "notready", "not ready"]),
                      ("paymentservice", ["image", "errimagepull", "pull", "manifest", "payment:v2-bad"])],
          "distractors": ["cartservice", "recommendation", "legacy-cron", "batch-report", "archive"]}
    return "Hard-B (2 faults)", bundle, gt


def hard_C(noise=2600):
    """3 real root causes (checkout OOM; node worker-5 NotReady; adservice bad image),
    with cascades and multiple fault-keyword red-herrings."""
    pod = "checkoutservice-7d9f8c6b45-abcde"
    node = "worker-5"
    buried = [
        f"20s  Warning   BackOff       pod/{pod}   Back-off restarting failed container checkout",
        'level=error msg="OOMKilled" reason=OOMKilled container=checkout exitCode=137 rss=512Mi limit=512Mi',
        f"1m   Warning   NodeNotReady  node/{node}   Node {node} status is now: NotReady (kubelet stopped posting status)",
        "2m   Warning   Failed        pod/adservice-y   Failed to pull image \"adservice:canary\": not found in registry",
        "2m   Warning   ImagePullBackOff pod/adservice-y   Back-off pulling image \"adservice:canary\"",
        f"30s  Warning   Evicted       pod/frontend-00042   node {node} NotReady, rescheduling",
    ]
    events = _interleave(_routine_events(noise), buried, _red_herrings(), depth=0.72)
    pods = ["NAME   READY   STATUS   RESTARTS   AGE",
            f"{pod}   0/1   CrashLoopBackOff   11   26m",
            "adservice-y   0/1   ImagePullBackOff   0   8m"] + \
           [f"frontend-{i:05d}   1/1   Running   0   5d" for i in range(70)]
    nodes = ["NAME   STATUS   ROLES   AGE   VERSION", f"{node}   NotReady   <none>   40d   v1.29.2"] + \
            [f"worker-{i}   Ready   <none>   40d   v1.29.2" for i in range(6, 22)]
    logs = _routine_logs(noise, "checkout")
    fault = FaultSignature(cluster="edge1", namespace="default", pod=pod,
                           fault_type="Application_Fault", fault_object=f"pod/{pod}",
                           root_cause_hint="crashloopbackoff",
                           summary=f"[edge1] crashloopbackoff on pod/{pod}; node {node} NotReady; adservice image error")
    bundle = EvidenceBundle(fault=fault, sources={
        "pods-all.txt": "\n".join(pods),
        "nodes.txt": "\n".join(nodes),
        "events.txt": "\n".join(events),
        f"{pod}-logs.txt": "\n".join(logs),
    })
    gt = {"primary": [("checkoutservice", ["oom", "oomkilled", "memory", "limit", "137"]),
                      ("worker-5", ["notready", "not ready", "kubelet"]),
                      ("adservice", ["image", "imagepull", "registry", "not found", "adservice:canary"])],
          "distractors": ["frontend-00042", "legacy-cron", "batch-report", "archive", "frontend-00007"]}
    return "Hard-C (3 faults)", bundle, gt


# ── strict scoring: root-cause section only + misattribution penalty ─────────
_RC_HEADERS = [r"most likely root cause", r"primary root cause", r"\broot cause"]
_END_HEADERS = [r"confidence", r"open questions", r"next validation", r"minimal next", r"remediation", r"hypotheses considered"]


def _root_cause_section(response):
    low = response.lower()
    start = None
    for h in _RC_HEADERS:
        m = re.search(h, low)
        if m:
            start = m.start()
            break
    if start is None:
        return response  # fallback: whole text (lenient) — flagged in output
    rest = response[start:]
    lowrest = rest.lower()
    end = len(rest)
    for h in _END_HEADERS:
        m = re.search(h, lowrest[8:])  # skip the header word itself
        if m:
            end = min(end, m.start() + 8)
    return rest[:end]


def score_strict(response, gt):
    section = _root_cause_section(response).lower()
    tp = 0
    detail = []
    for obj, causes in gt["primary"]:
        obj_hit = obj.lower() in section
        cause_hit = any(c in section for c in causes)
        ok = obj_hit and cause_hit
        tp += 1 if ok else 0
        detail.append({"object": obj, "in_root_cause": obj_hit, "cause": cause_hit, "identified": ok})
    fp = sum(1 for d in gt.get("distractors", []) if d.lower() in section)
    total = len(gt["primary"])
    recall = tp / total if total else 0.0
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    f1 = 2 * recall * precision / (recall + precision) if (recall + precision) else 0.0
    return {"recall": round(recall, 3), "precision": round(precision, 3), "f1": round(f1, 3),
            "tp": tp, "fp": fp, "total": total, "detail": detail}
