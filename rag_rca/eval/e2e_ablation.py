# eval/e2e_ablation.py
"""End-to-end ablation: does the Evidence-First pipeline change what the RCA
LLM actually concludes?

For each labeled incident we build TWO prompts from the SAME evidence bundle —
  raw       : the collect-all evidence dump (pipeline OFF)
  distilled : the Evidence-First block   (pipeline ON)
— send each to the real RCA agent (A2A message/send), and score the response
against ground truth. Only the evidence section differs, so any change in the
answer is attributable to the pipeline.

Metrics per (scenario, mode):
  - localization accuracy : true root-cause faults correctly named / total
  - diagnosis latency     : RCA agent wall-clock (s)
  - prompt tokens (est)   : chars / 4  (cost proxy)
  - hypothesis/confidence : parsed from the trailing Validator Packet if present

Env:
  RCA_AGENT_URL   e.g. http://127.0.0.1:8083/api/a2a/kagent/k8s-agent/
  RCA_AGENT_NAME  (informational)

Run:
  python eval/e2e_ablation.py --reps 3 --out results.json
  python eval/e2e_ablation.py --reps 1 --only A        # quick pilot
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app.pipelines.evidence_first_pipeline import distill_evidence
from app.rerank.cross_encoder_reranker import Reranker
from benchmark_pipeline import scenario_A, scenario_B, scenario_C, build_raw_prompt
from hard_scenarios import hard_B, hard_C, score_strict

RCA_AGENT_URL = os.getenv("RCA_AGENT_URL", "http://127.0.0.1:8083/api/a2a/kagent/k8s-agent/")
VALIDATOR_AGENT_URL = os.getenv("VALIDATOR_AGENT_URL", "http://127.0.0.1:8083/api/a2a/kagent/validator/")

# ── ground truth: the faults injected into each scenario ─────────────────────
GROUND_TRUTH = {
    "A (1 fault)": [
        ("checkoutservice", ["crashloop", "crash loop", "back-off", "backoff", "readiness", "probe", "restart"]),
    ],
    "B (2 faults)": [
        ("worker-2", ["notready", "not ready", "memory pressure"]),
        ("paymentservice", ["endpoint", "no ready", "unhealthy", "no_ready"]),
    ],
    "C (3 faults)": [
        ("checkoutservice", ["crashloop", "crash loop", "oom", "restart"]),
        ("worker-5", ["notready", "not ready"]),
        ("adservice", ["imagepull", "image pull", "errimagepull", "pull"]),
    ],
}

# ── expected corrective action per ground-truth fault (for remediation scoring) ─
# A remediation is scored "correct" for a fault if the validator's plan text
# mentions any of the expected corrective actions. This is a proxy — full plans
# are saved to JSON for manual review.
GROUND_TRUTH_REMEDIATION = {
    "A (1 fault)": [
        ("checkoutservice", ["restart", "rollback", "redeploy", "roll back", "readiness", "probe", "image", "config", "revert"]),
    ],
    "B (2 faults)": [
        ("worker-2", ["cordon", "drain", "reboot", "restart kubelet", "reschedule", "evict", "node"]),
        ("paymentservice", ["endpoint", "selector", "readiness", "scale", "restart", "redeploy", "service"]),
    ],
    "C (3 faults)": [
        ("checkoutservice", ["restart", "rollback", "memory", "limit", "resource", "oom", "redeploy"]),
        ("worker-5", ["cordon", "drain", "reboot", "node", "reschedule"]),
        ("adservice", ["image", "tag", "registry", "pull", "fix image", "correct image", "redeploy"]),
    ],
}


def rca_base_prompt(fault, evidence_section):
    """Mirrors webhook-receiver's BuildPrompt + buildRCAPrompt so the agent sees
    the same structure it does in production. Identical for raw and distilled;
    only `evidence_section` differs."""
    return f"""You are a Kubernetes SRE agent performing autonomous root cause analysis.

Rules:
- Analyze the Kubernetes evidence sections first.
- Identify PRIMARY root causes — failures that have distinct, independent origins.
- Distinguish root causes from cascading effects. Do NOT list downstream symptoms as separate faults.
- Cite specific evidence to support every conclusion.
- Do not ask for approval. Proceed autonomously.

## Incident Report
Cluster: {fault.cluster}
Namespace: {fault.namespace}
Summary: {fault.summary}

## Expected Output
1. Primary Root Cause(s) — one entry per independent origin
2. Cascading Effects
3. Impacted Services
4. Remediation Steps
5. Confidence Level

{evidence_section}

## Response Format
Write your analysis in markdown. End with this exact section (nothing after it):

## Validator Packet
```json
{{"hypothesis": "<one sentence>", "confidence": 0.0, "evidenceSummary": "<2-3 sentences>", "safeToFailover": true}}
```
"""


# ── A2A client (same shape as kagent/client.go TriggerRCA/TriggerValidation) ──
def _post(url, prompt, timeout=300):
    body = {
        "jsonrpc": "2.0",
        "id": f"eval-{int(time.time()*1000)}",
        "method": "message/send",
        "params": {"message": {
            "role": "user",
            "parts": [{"kind": "text", "text": prompt}],
            "messageId": f"msg-{int(time.time()*1000)}",
        }},
    }
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode()
    latency = time.perf_counter() - t0
    obj = json.loads(raw)
    # A kagent task can return HTTP 200 with status.state=failed (e.g. an upstream
    # OpenAI 429/quota or context-length error). Surface it instead of silently
    # returning an empty analysis.
    status = (obj.get("result") or {}).get("status") or {}
    if status.get("state") == "failed":
        msg = ""
        try:
            msg = status["message"]["parts"][0]["text"]
        except Exception:
            msg = json.dumps(status)[:300]
        raise RuntimeError(f"agent task failed: {msg[:300]}")
    return extract_text(obj), latency, raw


def call_rca(prompt, timeout=300):
    return _post(RCA_AGENT_URL, prompt, timeout)


def call_validator(prompt, timeout=300):
    return _post(VALIDATOR_AGENT_URL, prompt, timeout)


def validator_prompt(packet, fault):
    """Mirrors webhook-receiver buildValidatorPrompt: hand the RCA hypothesis
    packet to the validator, which must gather fresh evidence and emit a
    decision + remediationPlan."""
    it = fault.fault_type or ""
    return f"""You are a validation agent for Kubernetes remediation decisions.

## Validation Task
Use your available skills and Kubernetes tools to gather fresh live evidence and
validate the hypothesis below. Never trust the RCA packet as proof.

Critical rules:
- Always gather fresh live evidence using the skill tools before deciding.
- If a required tool call cannot be completed, return decision=reject.
- Always set the tool argument "context" to the exact clusterContext value below.
- If faultObject starts with "node/", extract the node name after "node/" and pass it to node tools.

Execution context (authoritative):
{{"context": "{fault.cluster}", "namespace": "{fault.namespace}", "incidentType": "{it}", "faultObject": "{fault.fault_object}"}}

Remediation rules:
- Base the remediationPlan only on what you observed with the skill tools.
- If decision is approve, recommend the most relevant corrective action and verification steps.
- If decision is reject, recommend the next safest diagnostic steps instead.
- Prefer reversible and low-risk actions first.

## Response Format
Write your validation analysis in markdown. End with this exact section (nothing after it):

## Validation Result
```json
{{"decision": "approve|reject", "confidence": 0.0, "reason": "<short>", "safeToFailover": true, "remediationPlan": {{"summary": "<short>", "steps": ["<step 1>", "<step 2>"], "risk": "low|medium|high", "requiresHumanApproval": true}}}}
```

RCA hypothesis packet to validate:
{json.dumps(packet, indent=2)}
"""


def extract_text(obj):
    """Pull the assistant text out of the A2A response (result.artifacts[].parts[].text),
    with a deep-search fallback."""
    try:
        for a in obj["result"]["artifacts"]:
            for p in a.get("parts", []):
                if p.get("text", "").strip():
                    return p["text"]
    except Exception:
        pass
    def deep(v):
        if isinstance(v, str):
            return v
        if isinstance(v, dict):
            for k in ("result", "artifacts", "message", "parts", "content", "text"):
                if k in v:
                    s = deep(v[k])
                    if s:
                        return s
        if isinstance(v, list):
            return "\n".join(filter(None, (deep(x) for x in v)))
        return ""
    return deep(obj)


# ── scoring ──────────────────────────────────────────────────────────────────
def score_localization(response, gt_faults):
    text = response.lower()
    identified = 0
    detail = []
    for obj, causes in gt_faults:
        obj_hit = obj.lower() in text
        cause_hit = any(c in text for c in causes)
        ok = obj_hit and cause_hit
        identified += 1 if ok else 0
        detail.append({"object": obj, "found_object": obj_hit, "found_cause": cause_hit, "identified": ok})
    return identified, len(gt_faults), detail


def _json_after(response, header_regex):
    m = re.search(header_regex, response)
    seg = response[m.end():] if m else response
    b = seg.find("{")
    if b < 0:
        return {}
    depth = 0
    for i in range(b, len(seg)):
        if seg[i] == "{":
            depth += 1
        elif seg[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(seg[b:i+1])
                except Exception:
                    return {}
    return {}


def parse_packet(response):
    return _json_after(response, r"##\s*Validator Packet")


def parse_validation(response):
    return _json_after(response, r"##\s*Validation Result")


def score_remediation(plan, gt_remediation):
    """Does the remediationPlan propose the expected corrective action per fault?"""
    text = " ".join([
        str(plan.get("summary", "")),
        " ".join(str(s) for s in plan.get("steps", []) or []),
    ]).lower()
    ok = 0
    detail = []
    for obj, actions in gt_remediation:
        hit = any(a in text for a in actions)
        ok += 1 if hit else 0
        detail.append({"object": obj, "action_addressed": hit})
    return ok, len(gt_remediation), detail


def est_tokens(s):
    return round(len(s) / 4)


def run(scenarios, reps, modes, out_path, use_validator=True, strict=False):
    reranker = Reranker(use_model=False)
    results = []
    for name, bundle, gt in scenarios:
        gt_rem = GROUND_TRUTH_REMEDIATION.get(name)
        raw_ev = "## Kubernetes Evidence\n" + build_raw_prompt(bundle)
        distilled = distill_evidence(bundle, reranker=reranker).prompt_block
        prompts = {
            "raw": rca_base_prompt(bundle.fault, raw_ev),
            "distilled": rca_base_prompt(bundle.fault, distilled),
        }
        for mode in modes:
            prompt = prompts[mode]
            for rep in range(reps):
                print(f"  → {name} · {mode} · rep {rep+1}/{reps} · prompt≈{est_tokens(prompt):,} tok ...", flush=True)
                try:
                    resp, latency, _raw = call_rca(prompt)
                except Exception as e:
                    print(f"    ! RCA call failed: {e}")
                    results.append({"scenario": name, "mode": mode, "rep": rep, "error": str(e)})
                    continue
                pkt = parse_packet(resp)
                row = {
                    "scenario": name, "mode": mode, "rep": rep,
                    "latency_s": round(latency, 2),
                    "prompt_tokens": est_tokens(prompt),
                    "response_chars": len(resp),
                    "hypothesis": pkt.get("hypothesis", ""),
                    "confidence": pkt.get("confidence", None),
                    "response": resp,
                }
                if strict:
                    s = score_strict(resp, gt)
                    idn, tot = s["tp"], s["total"]
                    row.update({"localized": idn, "faults": tot,
                                "localization_acc": s["recall"], "precision": s["precision"],
                                "f1": s["f1"], "false_pos": s["fp"], "detail": s["detail"]})
                    print(f"    strict recall {idn}/{tot} · precision {s['precision']} · f1 {s['f1']} · {latency:.1f}s")
                else:
                    idn, tot, detail = score_localization(resp, gt)
                    row.update({"localized": idn, "faults": tot,
                                "localization_acc": round(idn / tot, 3), "detail": detail})
                    print(f"    localized {idn}/{tot} · {latency:.1f}s · conf={pkt.get('confidence')}")

                # ── forward the RCA hypothesis to the validator agent ──────────
                if use_validator and gt_rem and pkt.get("hypothesis"):
                    print("      → validator ...", flush=True)
                    try:
                        vresp, vlat, _vraw = call_validator(validator_prompt(pkt, bundle.fault))
                        vres = parse_validation(vresp)
                        plan = vres.get("remediationPlan", {}) or {}
                        r_idn, r_tot, r_detail = score_remediation(plan, gt_rem)
                        row.update({
                            "validator_latency_s": round(vlat, 2),
                            "decision": vres.get("decision", ""),
                            "decision_correct": vres.get("decision", "") == "approve",  # injected faults are real
                            "remediation_correct": r_idn, "remediation_total": r_tot,
                            "remediation_acc": round(r_idn / r_tot, 3) if r_tot else None,
                            "remediation_detail": r_detail,
                            "validator_response": vresp,
                        })
                        print(f"      decision={vres.get('decision')} · remediation {r_idn}/{r_tot} · {vlat:.1f}s")
                    except Exception as e:
                        print(f"      ! validator call failed: {e}")
                        row["validator_error"] = str(e)

                results.append(row)
    if out_path:
        with open(out_path, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nsaved raw results → {out_path}")
    summarize(results)
    return results


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else float("nan")


def summarize(results):
    agg = {}
    for r in results:
        if "error" in r:
            continue
        k = (r["scenario"], r["mode"])
        a = agg.setdefault(k, {"acc": [], "lat": [], "tok": r["prompt_tokens"], "rem": [], "appr": []})
        a["acc"].append(r["localization_acc"])
        a["lat"].append(r["latency_s"])
        if r.get("remediation_acc") is not None:
            a["rem"].append(r["remediation_acc"])
        if "decision_correct" in r:
            a["appr"].append(1.0 if r["decision_correct"] else 0.0)
    print("\n" + "=" * 104)
    print("END-TO-END ABLATION — RCA + Validator agents, raw vs distilled context")
    print("=" * 104)
    print(f"{'Scenario':<14}{'Mode':<11}{'Localization':<15}{'Correct remed.':<16}{'Approve rate':<15}{'Diag latency':<15}{'Prompt tok'}")
    print("-" * 104)
    for (scn, mode), a in sorted(agg.items()):
        acc = _mean(a["acc"]) * 100
        rem = _mean(a["rem"]) * 100 if a["rem"] else float("nan")
        appr = _mean(a["appr"]) * 100 if a["appr"] else float("nan")
        lat = _mean(a["lat"])
        rem_s = f"{rem:>5.1f}%" if rem == rem else "   n/a"
        appr_s = f"{appr:>5.1f}%" if appr == appr else "   n/a"
        print(f"{scn:<14}{mode:<11}{acc:>6.1f}%        {rem_s:<16}{appr_s:<15}{lat:>6.1f} s        {a['tok']:>8,}")
    print("-" * 104)
    print("Localization  : true root-cause faults correctly named (RCA agent).")
    print("Correct remed.: validator remediationPlan proposes the expected corrective action per fault.")
    print("Approve rate  : validator decision = approve (injected faults are all genuinely real).")
    print("All are means over reps; prompt tok is the fixed prompt size per mode. Full responses saved to JSON.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--only", choices=["A", "B", "C"], help="run one scenario only")
    ap.add_argument("--mode", choices=["raw", "distilled", "both"], default="both")
    ap.add_argument("--hard", action="store_true",
                    help="use the harder scenarios (buried signals + red-herrings); implies strict scoring")
    ap.add_argument("--strict", action=argparse.BooleanOptionalAction, default=None,
                    help="strict root-cause-section scoring + misattribution penalty")
    ap.add_argument("--validator", action=argparse.BooleanOptionalAction, default=True,
                    help="also forward RCA hypothesis to the validator agent (--no-validator to skip)")
    ap.add_argument("--out", default="e2e_ablation_results.json")
    args = ap.parse_args()

    strict = args.strict if args.strict is not None else args.hard
    if args.hard:
        scenarios = [hard_B(), hard_C()]
    else:
        allsc = {"A": scenario_A(), "B": scenario_B(), "C": scenario_C()}
        chosen = [allsc[args.only]] if args.only else list(allsc.values())
        scenarios = [(n, b, GROUND_TRUTH[n]) for (n, b, _s) in chosen]
    modes = ["raw", "distilled"] if args.mode == "both" else [args.mode]

    print(f"RCA_AGENT_URL       = {RCA_AGENT_URL}")
    if args.validator:
        print(f"VALIDATOR_AGENT_URL = {VALIDATOR_AGENT_URL}")
    print(f"scenarios={[s[0] for s in scenarios]} modes={modes} reps={args.reps} strict={strict} hard={args.hard}\n")
    run(scenarios, args.reps, modes, args.out, use_validator=args.validator, strict=strict)


if __name__ == "__main__":
    main()
