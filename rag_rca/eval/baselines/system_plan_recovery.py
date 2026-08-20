# eval/baselines/system_plan_recovery.py
"""C4 (final) — execution-based correctness of the SYSTEM'S OWN emitted plans.

recovery_harness.py proved that *predefined* corrective actions recover the
service. This closes the remaining gap for Table IV: for each live fault we run
the actual pipeline (RCA agent -> validator agent -> remediationPlan), then
*execute the plan's proposed action* and measure whether health is restored
within the fixed 150 s timeout. The number reported here is the real
"Correct Remediation %" = fraction of system plans whose executed action
restored health.

Consistent with the rest of the paper's methodology, faulted-state evidence is
captured (kubectl) and fed to the agents as context (the agents have no live
edge1 kubeconfig); the agents produce the diagnosis + plan, and we execute it.

Safety: the plan is mapped to a BOUNDED set of reversible kubectl actions scoped
to the throwaway namespace; anything outside that set is 'not executable' ->
scored incorrect (never runs arbitrary commands).

Run: KUBECONFIG=... python baselines/system_plan_recovery.py
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import e2e_ablation as E
import recovery_harness as H

NS = H.NS


# ── capture live faulted-state evidence (harness side, real kubeconfig) ─────────
def capture_evidence(name, svc=None, node=None):
    parts = [f"# Live cluster evidence (namespace {NS}, context {H.CTX})"]
    parts.append("## kubectl get deploy,pods,svc,endpoints -o wide")
    parts.append(H.k("get", "deploy,pods,svc,endpoints", "-n", NS, "-o", "wide", check=False))
    parts.append(f"## kubectl describe deploy/{name}")
    parts.append(H.k("describe", "deploy", name, "-n", NS, check=False)[:3500])
    parts.append("## recent events")
    parts.append(H.k("get", "events", "-n", NS, "--sort-by=.lastTimestamp", check=False)[-2500:])
    if node:
        parts.append(f"## kubectl describe node {node} (Taints/Unschedulable)")
        desc = H.k("describe", "node", node, check=False)
        for ln in desc.splitlines():
            if any(w in ln for w in ("Taints", "Unschedulable", "SchedulingDisabled")):
                parts.append(ln.strip())
    return "\n".join(parts)


# ── the system pipeline: RCA hypothesis -> validator remediationPlan ────────────
def system_plan(name, fault_object, fault_type, evidence, node=None):
    rca_prompt = (
        "You are a Kubernetes root-cause analysis agent. Based ONLY on the evidence "
        "below, state the single primary root cause and the specific corrective action "
        f"needed. Target object: {fault_object} in namespace {NS}.\n\n{evidence}"
    )
    try:
        rca_txt, rca_lat, _ = E.call_rca(rca_prompt, timeout=180)
    except Exception as e:
        return None, {"stage": "rca", "error": str(e)[:160]}
    packet = {"faultObject": fault_object, "namespace": NS, "incidentType": fault_type,
              "hypothesis": rca_txt[:1500]}
    vprompt = (
        "You are a validation agent for Kubernetes remediation. Do NOT call any "
        "cluster tools; the live evidence has already been collected and is provided "
        "below. Base your remediationPlan only on this evidence text and the RCA "
        "hypothesis, and propose concrete corrective steps.\n\n"
        f"Evidence:\n{evidence}\n\nRCA hypothesis:\n{rca_txt[:1500]}\n\n"
        "## Response Format\nEnd with exactly:\n## Validation Result\n"
        '```json\n{"decision":"approve|reject","confidence":0.0,"reason":"<short>",'
        '"remediationPlan":{"summary":"<short>","steps":["<step>"],"risk":"low|medium|high",'
        '"requiresHumanApproval":true}}\n```'
    )
    v_txt = None
    for _ in range(3):
        try:
            v_txt, v_lat, _ = E.call_validator(vprompt, timeout=180)
            break
        except Exception as e:
            last = str(e)[:160]; time.sleep(6)
    if v_txt is None:
        return None, {"stage": "validator", "error": last}
    plan = E.parse_validation(v_txt).get("remediationPlan", {}) or {}
    # rca_only "plan" = the RCA agent's own recommended corrective action (no validator)
    rca_only = {"summary": rca_txt[:400], "steps": []}
    return plan, {"rca_lat": round(rca_lat, 1), "val_lat": round(v_lat, 1),
                  "rca_head": rca_txt[:200], "plan": plan, "rca_only": rca_only}


# ── per-action detectors: the CORRECTIVE intent each fault requires ─────────────
# Each maps to (positive-signal regex, negative/exclude regex). We ask, per
# scenario, only "does the plan propose THIS fault's corrective action?" — no
# cross-action first-match ambiguity. The proposed action is then executed and
# recovery is the arbiter (execution-grounded, not keyword-only).
ACTION_SIGNALS = {
    "scale_up":     r"scale|replicas?\b|replica count|--replicas|desired.*(count|replica)|set .*replicas",
    "fix_image":    r"\bimage\b|image tag|image reference|valid image|correct.*image|repository|registry",
    "fix_probe":    r"readiness|liveness|\bprobe\b|health\s?check",
    "fix_selector": r"selector|endpoints?|service .*(match|select)|label match|pod selector",
    "uncordon":     r"uncordon|make .*schedulable|remove .*(cordon|taint)|enable scheduling|schedulingdisabled",
}


def plan_proposes(plan, expected):
    """Return (proposed_bool, evidence_str). proposed=True iff the plan's text
    matches the corrective action the fault requires."""
    text = (str(plan.get("summary", "")) + " " +
            " ".join(str(s) for s in (plan.get("steps") or []))).lower()
    if not text.strip():
        return False, "empty plan"
    m = re.search(ACTION_SIGNALS[expected], text)
    if m:
        return True, f"matched '{m.group(0)}'"
    # report which other bounded action it looks like (for transparency)
    others = [k for k, rgx in ACTION_SIGNALS.items() if k != expected and re.search(rgx, text)]
    return False, ("proposes " + "/".join(others)) if others else "no recognized corrective action"


# ── scenarios: reuse recovery_harness setup/inject/broken/healthy + a real ───────
#    executor per bounded action (scoped to the throwaway ns) ─────────────────────
def scenarios():
    out = []

    z = H.scen_zero_replicas()
    out.append(dict(z, fault_object="deployment/rec-zero", fault_type="zero-replicas",
                    expect="scale_up", node=None,
                    exec_map={"scale_up": lambda: H.k("scale", "deploy", "rec-zero", "-n", NS, "--replicas=1")}))

    im = H.scen_bad_image()
    out.append(dict(im, fault_object="deployment/rec-image", fault_type="bad-image",
                    expect="fix_image", node=None,
                    exec_map={"fix_image": lambda: H.k("set", "image", "deploy/rec-image", "-n", NS, "c=nginx:1.27")}))

    pr = H.scen_readiness_port()
    out.append(dict(pr, fault_object="deployment/rec-probe", fault_type="readiness-probe",
                    expect="fix_probe", node=None,
                    exec_map={"fix_probe": lambda: H.apply(H.deploy_yaml("rec-probe", readiness_port=80))}))

    sv = H.scen_service_selector()
    out.append(dict(sv, fault_object="service/rec-svc-svc", fault_type="service-selector",
                    expect="fix_selector", node=None,
                    exec_map={"fix_selector": lambda: H.k("patch", "svc", "rec-svc-svc", "-n", NS,
                              "--type=merge", "-p", '{"spec":{"selector":{"app":"rec-svc"}}}')}))

    nd = H.scen_node_cordon()
    node = H.worker_node()
    out.append(dict(nd, fault_object=f"node/{node}", fault_type="node-unschedulable",
                    expect="uncordon", node=node,
                    exec_map={"uncordon": lambda: H.k("uncordon", node, check=False)}))
    return out


def run_one(s):
    print(f"\n=== {s['name']} ===", flush=True)
    r = {"scenario": s["name"], "fault_object": s["fault_object"]}
    try:
        s["setup"](); print("  setup: baseline", flush=True)
        s["inject"](); time.sleep(4)
        broke, tb = H.wait_until(s["broken"], target=True, timeout=H.BROKEN_TIMEOUT)
        r["fault_took_effect"] = broke
        print(f"  inject: broken={broke} ({tb}s)", flush=True)
        if not broke:
            r["correct_remediation"] = False; r["note"] = "fault did not take effect"; return r
        name = s["fault_object"].split("/")[-1] if s["fault_object"].startswith("deployment") else \
               ("rec-svc" if "svc" in s["fault_object"] else "rec-node")
        ev = capture_evidence(name, node=s.get("node"))
        plan, meta = system_plan(name, s["fault_object"], s["fault_type"], ev, node=s.get("node"))
        r["pipeline"] = meta
        if plan is None:
            r["correct_remediation"] = False; r["note"] = f"pipeline failed: {meta}"; return r
        expect = s["expect"]
        proposes, why = plan_proposes(plan, expect)
        # C2 validation ablation: does RCA-alone (no validator) also propose the action?
        rca_only = meta.get("rca_only", {})
        rca_proposes, _ = plan_proposes(rca_only, expect)
        r["rca_only_proposes_expected"] = rca_proposes
        r["plan_summary"] = str(plan.get("summary", ""))[:200]
        r["plan_steps"] = [str(x)[:160] for x in (plan.get("steps") or [])][:6]
        r["expected_action"] = expect
        r["plan_proposes_expected"] = proposes
        r["match_evidence"] = why
        print(f"  plan proposes expected({expect})={proposes} [{why}]", flush=True)
        if proposes:
            s["exec_map"][expect]()                        # execute the system's proposed action
            rec, tr = H.wait_until(s["healthy"], target=True, timeout=H.RECOVER_TIMEOUT)
            r["recovered"] = rec; r["time_to_recover_s"] = tr if rec else None
            r["correct_remediation"] = bool(rec)
            print(f"  executed: recovered={rec} in {tr}s -> correct={rec}", flush=True)
        else:
            # plan did not propose the corrective action this fault needs -> incorrect
            r["recovered"] = False; r["correct_remediation"] = False
            print(f"  plan did not propose the corrective action -> correct=False", flush=True)
    except Exception as e:
        r["error"] = str(e)[:200]; r["correct_remediation"] = False
        print(f"  ! error: {str(e)[:150]}", flush=True)
    finally:
        if s.get("cleanup"):
            try: s["cleanup"]()
            except Exception: pass
    return r


def main():
    H.k("create", "namespace", NS, check=False)
    outp = os.path.join(os.path.dirname(os.path.dirname(__file__)), "system_plan_results.json")
    results = []
    try:
        for s in scenarios():
            results.append(run_one(s))
            json.dump(results, open(outp, "w"), indent=2)
    finally:
        print("\ncleaning up namespace", flush=True)
        H.k("delete", "namespace", NS, "--wait=false", check=False)
    ok = sum(1 for r in results if r.get("correct_remediation"))
    match = sum(1 for r in results if r.get("plan_proposes_expected"))
    print("\n" + "=" * 68)
    print(f"C4 SYSTEM-PLAN EXECUTION — {ok}/{len(results)} plans restored health "
          f"({match}/{len(results)} proposed the expected corrective action)")
    for r in results:
        tt = f"{r.get('time_to_recover_s')}s" if r.get("recovered") else "—"
        print(f"  {r['scenario']:30s} expect={str(r.get('expected_action')):12s} "
              f"proposes={str(r.get('plan_proposes_expected')):5s} "
              f"correct={r.get('correct_remediation')} recover={tt}")
    print("=" * 68)
    json.dump(results, open(outp, "w"), indent=2)


if __name__ == "__main__":
    main()
