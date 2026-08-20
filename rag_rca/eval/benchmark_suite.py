# eval/benchmark_suite.py
"""Diverse RCA incident suite for a statistically meaningful ablation.

Generates N incidents deterministically (seeded), each with:
  - 1-4 real faults of varied kinds (crashloop/oom, probe, imagepull, node
    memory/disk, no-endpoints, port-mismatch, config error),
  - decisive signals buried mid-stream in ~30k tokens of routine noise,
  - adversarial red-herrings (resolved/historical/transient fault-keyword lines),
  - ground truth {"primary":[(object,cause_kws)], "distractors":[...]}.

Aggregating per-fault across incidents x reps yields realistic (non-coarse)
accuracy percentages. The suite is fixed BEFORE any run (seeded) so difficulty
is not tuned to the outcome.
"""
import random

from app.schemas import EvidenceBundle, FaultSignature

SERVICES = ["checkout", "payment", "cart", "adservice", "recommendation", "shipping",
            "currency", "email", "catalog", "order", "inventory", "search", "notify",
            "profile", "pricing"]

_HERRINGS = [
    "3d   Warning   BackOff        pod/legacy-cron-9      Back-off restarting failed container (RESOLVED 3d ago)",
    "2d   Warning   OOMKilled      pod/batch-report-7     Container OOMKilled during nightly job (one-off)",
    "6h   Warning   Unhealthy      pod/telemetry-2        Readiness probe failed once, recovered 5s later",
    "1d   Warning   FailedMount    pod/archive-1          transient volume attach delay, mounted after retry",
    'level=error msg="context canceled on shutdown" (graceful)',
    "4d   Warning   Evicted        pod/spark-old-3        old eviction from a decommissioned node",
]
DISTRACTOR_OBJECTS = ["legacy-cron", "batch-report", "telemetry", "archive", "spark-old"]


def _h(rng):
    return "".join(rng.choice("0123456789abcdef") for _ in range(5)) + "-" + \
           "".join(rng.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(5))


def _routine_events(n, rng):
    out = []
    for i in range(n):
        out.append(f"5d   Normal   Pulled    pod/web-{i:05d}   Successfully pulled image")
        out.append(f"5d   Normal   Started   pod/web-{i:05d}   Started container server")
        out.append(f"4d   Normal   Scheduled pod/web-{i:05d}   Successfully assigned default/web-{i:05d}")
    rng.shuffle(out)
    return out


# each fault kind returns dict: events[], pod_status?, node_status?, log?, gt(object,kws)
def _fault(kind, rng):
    if kind == "oom":
        s = rng.choice(SERVICES); pod = f"{s}-{_h(rng)}"
        return {"events": [f"20s  Warning   BackOff   pod/{pod}   Back-off restarting failed container {s}"],
                "pod": f"{pod}   0/1   CrashLoopBackOff   9   24m",
                "log": (f"{pod}-logs.txt", f'level=error msg="OOMKilled" reason=OOMKilled container={s} exitCode=137 rss=512Mi limit=512Mi'),
                "gt": (s, ["oom", "oomkilled", "memory", "137", "limit"])}
    if kind == "probe":
        s = rng.choice(SERVICES); pod = f"{s}-{_h(rng)}"
        return {"events": [f"45s  Warning   Unhealthy pod/{pod}   Readiness probe failed: HTTP probe failed with statuscode: 500",
                           f"30s  Warning   BackOff   pod/{pod}   Back-off restarting failed container {s}"],
                "pod": f"{pod}   0/1   CrashLoopBackOff   7   18m",
                "log": (f"{pod}-logs.txt", 'level=error msg="readiness check failed: dial tcp 10.0.3.9:7070: connect: connection refused"'),
                "gt": (s, ["readiness", "probe", "crashloop", "500", "restart"])}
    if kind == "imagepull":
        s = rng.choice(SERVICES); pod = f"{s}-{_h(rng)}"; tag = rng.choice(["v2-bad", "canary", "latest-broken"])
        return {"events": [f"1m   Warning   Failed    pod/{pod}   Failed to pull image \"{s}:{tag}\": not found in registry",
                           f"1m   Warning   ImagePullBackOff pod/{pod}   Back-off pulling image \"{s}:{tag}\""],
                "pod": f"{pod}   0/1   ImagePullBackOff   0   9m",
                "gt": (s, ["image", "imagepull", "errimagepull", "pull", tag])}
    if kind == "node_mem":
        node = f"worker-{rng.randint(1,30)}"
        return {"events": [f"3m   Warning   MemoryPressure node/{node}   Node {node} under memory pressure; evicting pods",
                           f"2m   Warning   NodeNotReady   node/{node}   Node {node} status is now: NotReady"],
                "node": f"{node}   NotReady   <none>   40d   v1.29.2",
                "gt": (node, ["notready", "not ready", "memory pressure"])}
    if kind == "node_disk":
        node = f"worker-{rng.randint(1,30)}"
        return {"events": [f"4m   Warning   EvictionThresholdMet node/{node}   Attempting to reclaim ephemeral-storage (DiskPressure)",
                           f"3m   Warning   NodeNotReady   node/{node}   Node {node} status is now: NotReady"],
                "node": f"{node}   NotReady   <none>   40d   v1.29.2",
                "gt": (node, ["disk pressure", "diskpressure", "notready", "not ready"])}
    if kind == "no_endpoints":
        s = rng.choice(SERVICES)
        return {"events": [f"1m   Warning   Unhealthy endpoints/{s}   service_no_ready_endpoints readyAddresses=0 notReadyAddresses=0"],
                "gt": (s, ["endpoint", "no ready", "no_ready", "readyaddresses", "no endpoints"])}
    if kind == "port_mismatch":
        s = rng.choice(SERVICES)
        return {"events": [f"2m   Warning   Endpoints service/{s}   targetPort does not match container port; connection refused -> 502"],
                "gt": (s, ["port", "targetport", "mismatch", "502"])}
    if kind == "config":
        s = rng.choice(SERVICES); pod = f"{s}-{_h(rng)}"
        return {"events": [f"1m   Warning   Failed    pod/{pod}   Error: configmap \"{s}-config\" not found",
                           f"1m   Warning   FailedCreatePodContainer pod/{pod}   CreateContainerConfigError"],
                "pod": f"{pod}   0/1   CreateContainerConfigError   0   6m",
                "gt": (s, ["config", "configmap", "createcontainerconfig", "missing", "not found"])}
    raise ValueError(kind)


KINDS = ["oom", "probe", "imagepull", "node_mem", "node_disk", "no_endpoints", "port_mismatch", "config"]


def generate_incidents(n=15, seed=1234, noise=700):
    rng = random.Random(seed)
    incidents = []
    for i in range(n):
        k = rng.choice([1, 1, 2, 2, 2, 3, 3, 4])          # fault-count distribution
        kinds = rng.sample(KINDS, min(k, len(KINDS)))
        faults = []
        used_obj = set()
        for kind in kinds:
            f = _fault(kind, rng)
            if f["gt"][0] in used_obj:
                continue
            used_obj.add(f["gt"][0])
            faults.append(f)
        if not faults:
            continue

        # assemble evidence
        events = _routine_events(noise, rng)
        # insert red-herrings in the first ~60%
        for j, herr in enumerate(rng.sample(_HERRINGS, rng.randint(3, len(_HERRINGS)))):
            events.insert(rng.randint(0, int(len(events) * 0.6)), herr)
        # bury real signals in the last ~35%
        pods = ["NAME   READY   STATUS   RESTARTS   AGE"]
        nodes = ["NAME   STATUS   ROLES   AGE   VERSION"]
        logs = {}
        for f in faults:
            for e in f["events"]:
                events.insert(rng.randint(int(len(events) * 0.62), len(events)), e)
            if f.get("pod"):
                pods.append(f["pod"])
            if f.get("node"):
                nodes.append(f["node"])
            if f.get("log"):
                logs.setdefault(f["log"][0], []).append(f["log"][1])
        # healthy filler for pods/nodes tables
        for x in range(40):
            pods.append(f"web-{x:05d}   1/1   Running   0   5d")
        for x in range(3, 20):
            nodes.append(f"worker-{x}   Ready   <none>   40d   v1.29.2")

        primary = faults[0]
        fobj = primary["gt"][0]
        is_node = fobj.startswith("worker-")
        fault_object = f"node/{fobj}" if is_node else f"pod/{fobj}"
        sig = FaultSignature(
            cluster="edge1", namespace="default" if not is_node else "cluster-scoped",
            pod=None if is_node else fobj, node=fobj if is_node else None,
            fault_type="Infrastructure_Fault" if is_node else "Application_Fault",
            fault_object=fault_object,
            root_cause_hint=primary["gt"][1][0],
            summary=f"[edge1] incident #{i+1}: " + "; ".join(f["gt"][0] for f in faults))
        sources = {"events.txt": "\n".join(events), "pods-all.txt": "\n".join(pods), "nodes.txt": "\n".join(nodes)}
        for name, lines in logs.items():
            sources[name] = "\n".join(_routine_events(200, rng)[:0] + lines +
                                      [f'level=info msg="ok" seq={z}' for z in range(300)])
        bundle = EvidenceBundle(fault=sig, sources=sources)
        gt = {"primary": [f["gt"] for f in faults], "distractors": list(DISTRACTOR_OBJECTS)}
        incidents.append((f"inc-{i+1:02d} ({len(faults)}f)", bundle, gt))
    return incidents
