# eval/baselines/recovery_harness.py
"""C4 — execution-based remediation correctness.

Defines "correct remediation" operationally: apply the remediation action to a
LIVE faulted workload and measure whether the target actually recovers (becomes
healthy) within a timeout. This replaces subjective/manual "correct?" labels with
a measured recovery outcome + time-to-recover.

Each scenario is self-contained in a throwaway namespace: create -> inject fault
-> confirm broken -> apply remediation -> poll for recovery. Cluster-only (no
LLM/agent); the agent-generated plan is evaluated separately once C6 frees kagent.

Run: KUBECONFIG=... python baselines/recovery_harness.py
"""
import json
import subprocess
import time
import os

CTX = os.getenv("KCTX", "edge1")
NS = "rca-recovery-test"
RECOVER_TIMEOUT = 150   # fixed recovery timeout (s) — reported in the paper
BROKEN_TIMEOUT = 90     # max wait to confirm the fault degraded health


def k(*args, check=True, timeout=90, stdin=None):
    r = subprocess.run(["kubectl", "--context", CTX, *args],
                       input=stdin, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args[:3])}: {r.stderr[:200]}")
    return r.stdout


def apply(manifest):
    r = subprocess.run(["kubectl", "--context", CTX, "apply", "-f", "-"],
                       input=manifest, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"apply: {r.stderr[:200]}")


def deploy_yaml(name, image="nginx:1.27", replicas=1, readiness_port=80, limit_mem="256Mi"):
    return f"""apiVersion: apps/v1
kind: Deployment
metadata: {{name: {name}, namespace: {NS}, labels: {{app: {name}}}}}
spec:
  replicas: {replicas}
  strategy: {{type: Recreate}}
  selector: {{matchLabels: {{app: {name}}}}}
  template:
    metadata: {{labels: {{app: {name}}}}}
    spec:
      containers:
      - name: c
        image: {image}
        ports: [{{containerPort: 80}}]
        readinessProbe: {{httpGet: {{path: /, port: {readiness_port}}}, initialDelaySeconds: 2, periodSeconds: 3}}
        resources: {{limits: {{memory: {limit_mem}}}}}
"""


def ready_replicas(name):
    out = k("get", "deploy", name, "-n", NS, "-o",
            "jsonpath={.status.readyReplicas}", check=False).strip()
    return int(out) if out.isdigit() else 0


def endpoints_count(svc):
    out = k("get", "endpoints", svc, "-n", NS, "-o",
            "jsonpath={.subsets[*].addresses[*].ip}", check=False).strip()
    return len(out.split()) if out else 0


def wait_until(fn, target=True, timeout=120, interval=5):
    """Poll fn() until it returns a truthy-matching value; return (ok, seconds)."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if bool(fn()) == target:
            return True, round(time.time() - t0, 1)
        time.sleep(interval)
    return False, round(time.time() - t0, 1)


# --- scenarios: (name, setup, inject_fault, remediation, health_ok) ------------
def scen_zero_replicas():
    name = "rec-zero"
    def setup(): apply(deploy_yaml(name)); wait_until(lambda: ready_replicas(name) >= 1, timeout=90)
    def inject(): k("scale", "deploy", name, "-n", NS, "--replicas=0")
    def broken(): return ready_replicas(name) == 0
    def remediate(): k("scale", "deploy", name, "-n", NS, "--replicas=1")
    def healthy(): return ready_replicas(name) >= 1
    return dict(name="deployment-zero-replicas", setup=setup, inject=inject,
                broken=broken, remediate=remediate, healthy=healthy)


def scen_bad_image():
    name = "rec-image"
    def setup(): apply(deploy_yaml(name)); wait_until(lambda: ready_replicas(name) >= 1, timeout=90)
    def inject(): k("set", "image", f"deploy/{name}", "-n", NS, f"c=nginx:does-not-exist-9999")
    def broken(): return ready_replicas(name) == 0
    def remediate(): k("set", "image", f"deploy/{name}", "-n", NS, "c=nginx:1.27")
    def healthy(): return ready_replicas(name) >= 1
    return dict(name="incorrect-image-reference", setup=setup, inject=inject,
                broken=broken, remediate=remediate, healthy=healthy)


def scen_readiness_port():
    name = "rec-probe"
    def setup(): apply(deploy_yaml(name)); wait_until(lambda: ready_replicas(name) >= 1, timeout=90)
    def inject(): apply(deploy_yaml(name, readiness_port=8081))   # wrong port -> never ready
    def broken(): return ready_replicas(name) == 0
    def remediate(): apply(deploy_yaml(name, readiness_port=80))  # fix port
    def healthy(): return ready_replicas(name) >= 1
    return dict(name="readiness-probe-incorrect-port", setup=setup, inject=inject,
                broken=broken, remediate=remediate, healthy=healthy)


def scen_service_selector():
    name = "rec-svc"
    svc = f"{name}-svc"
    def setup():
        apply(deploy_yaml(name)); wait_until(lambda: ready_replicas(name) >= 1, timeout=90)
        apply(f"apiVersion: v1\nkind: Service\nmetadata: {{name: {svc}, namespace: {NS}}}\n"
              f"spec: {{selector: {{app: {name}}}, ports: [{{port: 80, targetPort: 80}}]}}\n")
        wait_until(lambda: endpoints_count(svc) >= 1, timeout=60)
    def inject(): k("patch", "svc", svc, "-n", NS, "--type=merge",
                    "-p", '{"spec":{"selector":{"app":"does-not-exist"}}}')
    def broken(): return endpoints_count(svc) == 0
    def remediate(): k("patch", "svc", svc, "-n", NS, "--type=merge",
                       "-p", f'{{"spec":{{"selector":{{"app":"{name}"}}}}}}')
    def healthy(): return endpoints_count(svc) >= 1
    return dict(name="service-selector-mismatch", setup=setup, inject=inject,
                broken=broken, remediate=remediate, healthy=healthy)


def worker_node():
    """Pick a schedulable non-control-plane node (the worker)."""
    out = k("get", "nodes", "-o",
            "jsonpath={range .items[*]}{.metadata.name}{\"|\"}"
            "{.metadata.labels.node-role\\.kubernetes\\.io/control-plane}{\"\\n\"}{end}", check=False)
    for line in out.splitlines():
        name, is_cp = (line.split("|") + [""])[:2]
        if name and is_cp == "":
            return name
    return None


def scen_node_cordon():
    """Infra-class, SAFE + reversible: cordon the worker (does NOT evict running
    pods) so a NEW pod cannot schedule -> Pending; remediation = uncordon.
    Health criterion for the node/scheduling class = pod becomes Ready."""
    name = "rec-node"
    node = worker_node()
    # pin the pod to the worker so cordoning it (not the schedulable control-plane)
    # genuinely makes THIS pod unschedulable, without affecting other scheduling.
    pinned = deploy_yaml(name).rstrip() + f"\n      nodeSelector: {{kubernetes.io/hostname: {node}}}\n"
    def setup(): pass  # nothing running yet
    def inject():
        if node:
            k("cordon", node)
        apply(pinned)
    def broken():
        return ready_replicas(name) == 0  # pod stuck Pending (unschedulable)
    def remediate():
        if node:
            k("uncordon", node)
    def healthy():
        return ready_replicas(name) >= 1
    return dict(name="node-unschedulable-cordon", setup=setup, inject=inject,
                broken=broken, remediate=remediate, healthy=healthy,
                cleanup=lambda: (node and k("uncordon", node, check=False)))


SCENARIOS = [scen_zero_replicas, scen_bad_image, scen_readiness_port,
             scen_service_selector, scen_node_cordon]


def run_one(s):
    print(f"\n=== {s['name']} ===", flush=True)
    r = {"scenario": s["name"]}
    try:
        s["setup"](); print("  setup: healthy baseline", flush=True)
        s["inject"](); time.sleep(4)
        broke, t_break = wait_until(s["broken"], target=True, timeout=BROKEN_TIMEOUT)
        r["fault_took_effect"] = broke
        print(f"  inject: broken={broke} ({t_break}s)", flush=True)
        s["remediate"]()
        recovered, t_rec = wait_until(s["healthy"], target=True, timeout=RECOVER_TIMEOUT)
        r["recovered"] = recovered
        r["time_to_recover_s"] = t_rec if recovered else None
        r["correct_remediation"] = bool(broke and recovered)
        print(f"  remediate: recovered={recovered} in {t_rec}s -> correct={r['correct_remediation']}", flush=True)
    except Exception as e:
        r["error"] = str(e)[:200]; r["correct_remediation"] = False
        print(f"  ! error: {str(e)[:150]}", flush=True)
    finally:
        if s.get("cleanup"):
            try: s["cleanup"]()
            except Exception: pass
    return r


def main():
    k("create", "namespace", NS, check=False)
    results = []
    try:
        for factory in SCENARIOS:
            results.append(run_one(factory()))
            json.dump(results, open(os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                                  "recovery_results.json"), "w"), indent=2)
    finally:
        print("\ncleaning up namespace", flush=True)
        k("delete", "namespace", NS, "--wait=false", check=False)
    ok = sum(1 for r in results if r.get("correct_remediation"))
    print("\n" + "=" * 60)
    print(f"C4 RECOVERY — {ok}/{len(results)} remediations restored service health")
    for r in results:
        tt = f"{r.get('time_to_recover_s')}s" if r.get("recovered") else "—"
        print(f"  {r['scenario']:32s} correct={r.get('correct_remediation')}  recover={tt}")
    print("=" * 60)


if __name__ == "__main__":
    main()
