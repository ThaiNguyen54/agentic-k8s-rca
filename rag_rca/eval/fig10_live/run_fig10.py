# eval/fig10_live/run_fig10.py
"""Fig. 10 live rerun (see PREREGISTRATION.md). For each (configuration, run):
reset edge2 to the baseline -> inject -> wait SETTLE s -> start, at the same
instant, the plain LLM call and the proposed pipeline (Orchestrator webhook ->
RCA agent -> Validation agent) -> save both runs. Resumable: an injection whose
two run files exist is skipped.

    AAP_BASE_URL=... AAP_API_KEY=... ORCH_URL=http://127.0.0.1:9094/webhook/fault ORCH_LOG=... \
        python fig10_live/run_fig10.py [--only 1-1,3-2] [--runs 10]
"""
import argparse
import datetime as dt
import json
import os
import random
import re
import sys
import threading
import time
import uuid

import httpx
import requests
from openai import OpenAI

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "rq1_clean"))
sys.path.insert(0, os.path.join(HERE, "..", "baselines"))
import faults as F  # noqa: E402
from build_prompts import ALERT, INSTRUCTIONS  # noqa: E402
from gold import SCENARIOS, readme_gold_lines  # noqa: E402

BASE_URL = os.environ.get("AAP_BASE_URL", "")  # lab vLLM endpoint; never written to files
MODEL = "gpt-oss-120b"
ORDER_SEED = 20261107
SETTLE_S = 60
TIMEOUT_S = 600
# Generic alert: identical for every configuration (spec rule 1).
ALERT_LABELS = {"cluster": "edge2", "cluster_context": "edge2", "namespace": "onlineboutique",
                "fault_type": "ApplicationDegraded", "fault_object": "", "root_cause_hint": ""}
LEAK = [r"scenario\s*\d-\d", r"\b[123]-faults?\b", r"one-fault", r"multi-fault", r"fault categor",
        r"expected root", r"experiment intent", r"\bfault [a-c1-3]:"]


def leak_scan(cfg, texts):
    hits = []
    gold = readme_gold_lines(cfg)
    for name, t in texts.items():
        low = (t or "").lower()
        hits += [f"{name}: /{w}/" for w in LEAK if re.search(w, low)]
        hits += [f"{name}: README line" for g in gold if g.lower() in low]
    return hits


def tool_calls(raw):
    """Tool names the agent invoked, from the kagent A2A task (as in live_validation_suite)."""
    names = []

    def walk(o):
        if isinstance(o, dict):
            md = o.get("metadata") or {}
            if isinstance(md, dict) and "function_call" in str(md.get("kagent_type", "")):
                d = o.get("data") or {}
                if isinstance(d, dict) and d.get("name"):
                    names.append(d["name"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(raw)
    return names


def run_plain(seed, rows, deadline, out):
    alert = f"Alert:\n- {ALERT}\n"
    if rows:
        alert += "- Pods not running or not ready (NAME READY STATUS RESTARTS):\n" + "".join(f"  {r}\n" for r in rows)
    prompt = INSTRUCTIONS.format(source="alert") + "\n" + alert
    client = OpenAI(base_url=BASE_URL, api_key=os.environ["AAP_API_KEY"],
                    http_client=httpx.Client(verify=False), max_retries=0)
    out["prompt"] = prompt
    try:
        r = client.chat.completions.create(
            model=MODEL, messages=[{"role": "user", "content": prompt}], temperature=1.0, seed=seed,
            max_tokens=8000, timeout=max(1.0, deadline - time.time()))
        m = r.choices[0].message
        out.update(content=m.content or "", finish_reason=r.choices[0].finish_reason,
                   usage=r.usage.model_dump() if r.usage else None)
    except Exception as e:
        out["error"] = repr(e)[:500]
    out["t_done"] = time.time()


def run_proposed(incident_id, rows, t_injected, deadline, out):
    payload = {"incidentId": incident_id, "labels": ALERT_LABELS,
               "startsAt": dt.datetime.fromtimestamp(t_injected, dt.timezone.utc).isoformat(),
               "annotations": {"summary": ALERT, "detail": "\n".join(rows)}}
    out["alert_payload"] = payload
    try:
        r = requests.post(os.environ.get("ORCH_URL", "http://127.0.0.1:9094/webhook/fault"),
                          json=payload, timeout=max(1.0, deadline - time.time()))
        out["http_status"] = r.status_code
        try:
            out["response"] = r.json()
        except ValueError:
            out["error"] = f"HTTP {r.status_code}: {r.text[:500]}"
    except requests.Timeout:
        out["error"] = "timeout"
    except Exception as e:
        out["error"] = repr(e)[:500]
    out["t_done"] = time.time()


def orch_prompts(log_text):
    """RCA and validator prompts as logged by the Orchestrator for one request."""
    def grab(tag):
        m = re.search(rf"\[webhook\] {tag} prompt begin\n(.*?)\n[^\n]*\[webhook\] {tag} prompt end", log_text, re.S)
        return m.group(1) if m else ""
    return {"rca_prompt": grab("RCA"), "validator_prompt": grab("validator")}


def one(cfg, k, order_index):
    rec = {"config": cfg, "run": k, "order_index": order_index, "model": MODEL,
           "settle_s": SETTLE_S, "timeout_s": TIMEOUT_S}
    rec["reset_before"] = F.reset()
    if not rec["reset_before"]["healthy"]:
        raise RuntimeError(f"baseline not healthy before {cfg} r{k}: {rec['reset_before']}")
    rec["t_inject_start"] = time.time()
    F.inject(cfg)
    rec["t_injected"] = t_inj = time.time()
    time.sleep(SETTLE_S)
    rows = F.unhealthy_pod_rows()
    rec["unhealthy_pod_rows"] = rows
    deadline = t_inj + TIMEOUT_S
    log_path = os.environ.get("ORCH_LOG", "")
    log_off = os.path.getsize(log_path) if log_path and os.path.exists(log_path) else 0

    plain, prop = {}, {}
    rec["t_start"] = time.time()
    th = [threading.Thread(target=run_plain, args=(k, rows, deadline, plain)),
          threading.Thread(target=run_proposed, args=(str(uuid.uuid4()), rows, t_inj, deadline, prop))]
    for t in th:
        t.start()
    for t in th:
        t.join()

    if log_path and os.path.exists(log_path):
        with open(log_path) as fh:
            fh.seek(log_off)
            prop.update(orch_prompts(fh.read()))
    resp = prop.get("response") or {}
    prop["rca_tool_calls"] = tool_calls((resp.get("rca") or {}).get("raw"))
    prop["validator_tool_calls"] = tool_calls((resp.get("validation") or {}).get("raw"))
    prop["skill_invoked"] = "skills" in prop["validator_tool_calls"]
    for res in (plain, prop):
        t_done = res.get("t_done", time.time())
        res["timed_out"] = res.get("error") == "timeout" or t_done > deadline
        res["diagnosis_time_s"] = TIMEOUT_S if res["timed_out"] else round(t_done - t_inj, 1)
    plain["leak_hits"] = leak_scan(cfg, {"plain_prompt": plain.get("prompt")})
    prop["leak_hits"] = leak_scan(cfg, {"alert": json.dumps(prop.get("alert_payload")),
                                        "rca_prompt": prop.get("rca_prompt"),
                                        "validator_prompt": prop.get("validator_prompt")})
    rec["reset_after"] = F.reset()
    for cond, res in (("plain", plain), ("proposed", prop)):
        d = os.path.join(HERE, "runs", cfg, cond)
        os.makedirs(d, exist_ok=True)
        json.dump({**rec, "condition": cond, **res}, open(os.path.join(d, f"run{k:02d}.json"), "w"), indent=1)
    return plain, prop


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    cfgs = args.only.split(",") if args.only else list(SCENARIOS)
    order = [(c, k) for c in SCENARIOS for k in range(args.runs)]
    random.Random(ORDER_SEED).shuffle(order)
    todo = [(i, c, k) for i, (c, k) in enumerate(order) if c in cfgs and not all(
        os.path.exists(os.path.join(HERE, "runs", c, cond, f"run{k:02d}.json")) for cond in ("plain", "proposed"))]
    print(f"{len(todo)} injections to run", flush=True)
    try:
        for n, (i, c, k) in enumerate(todo, 1):
            t0 = time.time()
            plain, prop = one(c, k, i)
            print(f"[{n}/{len(todo)}] {c} r{k}: plain {plain['diagnosis_time_s']}s {plain.get('error', 'ok')} | "
                  f"proposed {prop['diagnosis_time_s']}s {prop.get('error', 'ok')} skill={prop['skill_invoked']} "
                  f"tools={len(prop['rca_tool_calls'])}+{len(prop['validator_tool_calls'])} "
                  f"leaks={len(plain['leak_hits']) + len(prop['leak_hits'])} ({round(time.time() - t0)}s)", flush=True)
    finally:
        print("final reset:", F.reset(), flush=True)


if __name__ == "__main__":
    main()
