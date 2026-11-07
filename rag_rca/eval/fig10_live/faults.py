# eval/fig10_live/faults.py
"""Live injection of the nine RQ1 fault configurations on edge2 / onlineboutique,
and the reset to the saved healthy baseline (baseline/*.json, captured
2026-11-07 before any run).

Faults follow the scenario READMEs and rq1_clean/gold.py. Deviations from the
original scenarios (namespace-scoped drain in 2-1, 3-1's unknown network fault
not injected) are listed in PREREGISTRATION.md.
"""
import json
import os
import subprocess
import time

CTX = os.getenv("FIG10_CTX", "edge2")
NS = "onlineboutique"
WORKER = "edge2-md-0-4whsm-xcq92"
HERE = os.path.dirname(os.path.abspath(__file__))
BAD_PAYMENT_IMAGE = "us-central1-docker.pkg.dev/google-samples/microservices-demo/paymentservice:v9.9.9-nonexistent"

MULTUS_TEST = """apiVersion: v1
kind: Pod
metadata:
  name: multus-test
  namespace: onlineboutique
  annotations:
    k8s.v1.cni.cncf.io/networks: nonexistent-network
spec:
  containers:
  - name: pause
    image: registry.k8s.io/pause:3.9
"""


def k(*args, check=True, stdin=None, timeout=120):
    r = subprocess.run(["kubectl", "--context", CTX, *args], input=stdin,
                       capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args[:4])}: {r.stderr.strip()[:300]}")
    return r.stdout


def kn(*args, **kw):
    return k(*args, "-n", NS, **kw)


# Each step is a kubectl invocation; a configuration is the list of its steps.
def _env(deploy, assignment):
    return lambda: kn("set", "env", f"deploy/{deploy}", assignment)


def _mem(deploy, limit, request):
    return lambda: kn("set", "resources", f"deploy/{deploy}", f"--limits=memory={limit}",
                      f"--requests=memory={request}")


def _bad_image():
    return lambda: kn("set", "image", "deploy/paymentservice", f"server={BAD_PAYMENT_IMAGE}")


def _cordon():
    return lambda: k("cordon", WORKER)


def _evict_namespace():
    # Namespace-scoped drain: a full drain would also evict longhorn/argocd on
    # the single worker (and is blocked by a longhorn PDB).
    return lambda: kn("delete", "pods", "--all", "--wait=false")


def _crash_command():
    patch = [{"op": "add", "path": "/spec/template/spec/containers/0/command", "value": ["/bin/sh", "-c"]},
             {"op": "add", "path": "/spec/template/spec/containers/0/args", "value": ["echo crash-injected; exit 1"]}]
    return lambda: kn("patch", "deploy", "checkoutservice", "--type=json", "-p", json.dumps(patch))


def _target_port():
    patch = [{"op": "replace", "path": "/spec/ports/0/targetPort", "value": 9090}]
    return lambda: kn("patch", "svc", "frontend", "--type=json", "-p", json.dumps(patch))


def _multus():
    return lambda: k("apply", "-f", "-", stdin=MULTUS_TEST)


CONFIGS = {
    "1-1": [_env("checkoutservice", "CURRENCY_SERVICE_ADDR=bad-currency:7000")],
    "1-2": [_env("frontend", "CHECKOUT_SERVICE_ADDR=bad-checkout:5050")],
    "1-3": [_mem("paymentservice", "16Mi", "16Mi")],
    "2-1": [_bad_image(), _cordon(), _evict_namespace()],
    "2-2": [_cordon(), _bad_image()],          # image change masked: its new pod cannot schedule
    "2-3": [_bad_image(), _env("checkoutservice", "PRODUCT_CATALOG_SERVICE_ADDR-")],
    "3-1": [_crash_command(), _mem("recommendationservice", "10Mi", "5Mi")],
    "3-2": [_target_port(), _env("cartservice", "REDIS_ADDR=bad-redis:6379"),
            _env("shippingservice", "PORT=50052"), _multus()],
    "3-3": [_env("checkoutservice", "CURRENCY_SERVICE_ADDR=bad-currency:7000"),
            _env("frontend", "CHECKOUT_SERVICE_ADDR=bad-checkout:5050"),
            _mem("paymentservice", "16Mi", "16Mi")],
}


def inject(cfg):
    for step in CONFIGS[cfg]:
        step()


def reset(timeout=600):
    """Restore the saved baseline and wait until it is healthy. Returns the check log."""
    k("uncordon", WORKER)
    kn("delete", "pod", "multus-test", "--ignore-not-found", "--wait=false")
    for kind in ("deployments", "services"):
        # replace (not apply) so env vars and patches added by `kubectl set` are removed
        k("replace", "-f", os.path.join(HERE, "baseline", f"{kind}.json"))
    return wait_healthy(timeout)


def health():
    """(healthy, problems) for the baseline definition of healthy."""
    problems = []
    deps = json.loads(kn("get", "deploy", "-o", "json"))["items"]
    for d in deps:
        s, want = d.get("status", {}), d["spec"].get("replicas", 1)
        if s.get("readyReplicas", 0) != want or s.get("updatedReplicas", 0) != want or s.get("replicas", 0) != want:
            problems.append(f"deploy/{d['metadata']['name']} ready={s.get('readyReplicas', 0)}/{want}")
    for p in json.loads(kn("get", "pods", "-o", "json"))["items"]:
        st = p.get("status", {})
        ready = all(c.get("ready") for c in st.get("containerStatuses", []) or [{}])
        if st.get("phase") != "Running" or not ready or p["metadata"].get("deletionTimestamp"):
            problems.append(f"pod/{p['metadata']['name']} {st.get('phase')}")
    node = json.loads(k("get", "node", WORKER, "-o", "json"))
    if node["spec"].get("unschedulable"):
        problems.append("worker cordoned")
    svc = json.loads(kn("get", "svc", "frontend", "-o", "json"))
    if svc["spec"]["ports"][0].get("targetPort") != 8080:
        problems.append("frontend targetPort changed")
    return not problems, problems


def wait_healthy(timeout=600, interval=10):
    t0 = time.time()
    while True:
        ok, problems = health()
        if ok:
            return {"healthy": True, "seconds": round(time.time() - t0, 1)}
        if time.time() - t0 > timeout:
            return {"healthy": False, "seconds": round(time.time() - t0, 1), "problems": problems}
        time.sleep(interval)


def unhealthy_pod_rows():
    """The not-running / not-ready rows of `kubectl get pods` (NAME READY STATUS RESTARTS),
    built exactly as rq1_clean/build_prompts.py builds the plain alert."""
    rows = kn("get", "pods").splitlines()
    bad = []
    for r in rows[1:]:
        f = r.split()
        if len(f) < 3:
            continue
        a, _, b = f[1].partition("/")
        if f[2] != "Running" or (a.isdigit() and b.isdigit() and a != b):
            bad.append(" ".join(f[:4]))
    return bad
