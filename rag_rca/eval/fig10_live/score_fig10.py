# eval/fig10_live/score_fig10.py
"""Score the Fig. 10 live runs and aggregate (see PREREGISTRATION.md).

Per run and scored fault (rq1_clean/gold.py, rubric v2), inside the answer's
"Primary root cause(s)" section:
  - localization: the fault's component pattern matches;
  - strict: component and cause patterns match within WINDOW characters.
A run is correct when every scored fault is hit. Errors, timeouts, empty or
truncated answers are misses. The proposed answer is the RCA agent's analysis
as returned by the Orchestrator; the validator's verdict is recorded beside it.

    python fig10_live/score_fig10.py
"""
import csv
import glob
import json
import math
import os
import re
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "rq1_clean"))
sys.path.insert(0, os.path.join(HERE, "..", "baselines"))
from gold import SCENARIOS, WINDOW  # noqa: E402
from newcombe import wilson  # noqa: E402
from score_rq1 import NORM, primary_section  # noqa: E402  (same section rule as RQ1)

CONDS = ["plain", "proposed"]


def near(text, a, b):
    pa = [m.start() for m in re.finditer(a, text, re.I)]
    pb = [m.start() for m in re.finditer(b, text, re.I)]
    return any(abs(x - y) <= WINDOW for x in pa for y in pb)


def hits(sec, fault):
    pairs = fault.get("any_of") or [(fault["component"], fault["cause"])]
    loc = any(re.search(a, sec, re.I) for a, _ in pairs)
    strict = any(near(sec, a, b) for a, b in pairs)
    return loc, strict


def answer(rec):
    if rec["condition"] == "plain":
        ok = rec.get("finish_reason") == "stop" and not rec.get("timed_out")
        return (rec.get("content") or "") if ok else ""
    if rec.get("error") or rec.get("timed_out"):
        return ""
    return ((rec.get("response") or {}).get("rca") or {}).get("humanResponse") or ""


def sign_test(better, worse):
    n = better + worse
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(min(better, worse) + 1)) / 2 ** n)


def main():
    rows = []
    for f in sorted(glob.glob(os.path.join(HERE, "runs", "*", "*", "run*.json"))):
        rec = json.load(open(f))
        cfg, cond = rec["config"], rec["condition"]
        sec, found = primary_section(answer(rec).translate(NORM))
        res = {flt["id"]: hits(sec, flt) if sec.strip() else (False, False) for flt in SCENARIOS[cfg]["faults"]}
        verdict = (((rec.get("response") or {}).get("validation") or {}).get("decision") or {}).get("decision")
        rows.append({"config": cfg, "level": SCENARIOS[cfg]["level"], "condition": cond, "run": rec["run"],
                     "faults": len(res), "loc_tp": sum(v[0] for v in res.values()),
                     "strict_tp": sum(v[1] for v in res.values()),
                     "localized": all(v[0] for v in res.values()), "strict": all(v[1] for v in res.values()),
                     "diagnosis_time_s": rec["diagnosis_time_s"], "timed_out": rec["timed_out"],
                     "error": rec.get("error", ""), "section_found": found,
                     "leak_hits": len(rec.get("leak_hits") or []),
                     "skill_invoked": rec.get("skill_invoked", ""),
                     "rca_tool_calls": len(rec.get("rca_tool_calls") or []),
                     "validator_tool_calls": len(rec.get("validator_tool_calls") or []),
                     "validator_decision": verdict or "",
                     **{f"loc:{k}": v[0] for k, v in res.items()}, **{f"strict:{k}": v[1] for k, v in res.items()}})

    keys = sorted({k for r in rows for k in r}, key=lambda k: (":" in k, k))
    with open(os.path.join(HERE, "scores.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)

    def pct(sel, key):
        return round(100 * sum(r[key] for r in sel) / len(sel), 1) if sel else None

    out = {"model": "gpt-oss-120b", "rubric": "rq1_clean/gold.py (v2)", "configs": {}, "levels": {},
           "sign_tests": {}, "counts": {}}
    for cfg in SCENARIOS:
        out["configs"][cfg] = {}
        for c in CONDS:
            sel = [r for r in rows if r["config"] == cfg and r["condition"] == c]
            if sel:
                out["configs"][cfg][c] = {"runs": len(sel), "localization_pct": pct(sel, "localized"),
                                          "strict_pct": pct(sel, "strict"),
                                          "mean_time_s": round(st.mean(r["diagnosis_time_s"] for r in sel), 1)}
    for lvl in (1, 2, 3):
        out["levels"][lvl] = {}
        for c in CONDS:
            per = [out["configs"][cfg][c] for cfg, s in SCENARIOS.items()
                   if s["level"] == lvl and c in out["configs"][cfg]]
            sel = [r for r in rows if r["level"] == lvl and r["condition"] == c]
            if not per:
                continue
            k_loc, k_str = sum(r["localized"] for r in sel), sum(r["strict"] for r in sel)
            out["levels"][lvl][c] = {
                "configs": len(per), "runs": len(sel),
                "localization_pct": pct(sel, "localized"), "strict_pct": pct(sel, "strict"),
                "localization_cfg_mean_sd": [round(st.mean(p["localization_pct"] for p in per), 1),
                                             round(st.pstdev(p["localization_pct"] for p in per), 1)],
                "strict_cfg_mean_sd": [round(st.mean(p["strict_pct"] for p in per), 1),
                                       round(st.pstdev(p["strict_pct"] for p in per), 1)],
                "localization_wilson95": [round(100 * x, 1) for x in wilson(k_loc, len(sel), 1.96)],
                "strict_wilson95": [round(100 * x, 1) for x in wilson(k_str, len(sel), 1.96)],
                "mean_time_s": round(st.mean(r["diagnosis_time_s"] for r in sel), 1),
                "sd_time_s": round(st.pstdev(r["diagnosis_time_s"] for r in sel), 1)}
    for metric, key in (("localization", "localization_pct"), ("strict", "strict_pct")):
        better = equal = worse = 0
        for cfg, d in out["configs"].items():
            if "plain" in d and "proposed" in d:
                a, b = d["proposed"][key], d["plain"][key]
                better += a > b
                equal += a == b
                worse += a < b
        out["sign_tests"][metric] = {"proposed_better": better, "equal": equal, "proposed_worse": worse,
                                     "p_exact_two_sided": round(sign_test(better, worse), 4)}
    for c in CONDS:
        sel = [r for r in rows if r["condition"] == c]
        out["counts"][c] = {"runs": len(sel), "timeouts": sum(r["timed_out"] for r in sel),
                            "errors": sum(bool(r["error"]) and not r["timed_out"] for r in sel),
                            "runs_with_leak_hits": sum(r["leak_hits"] > 0 for r in sel),
                            "sections_not_found": sum(not r["section_found"] for r in sel)}
        if c == "proposed":
            out["counts"][c].update(
                skill_invoked=sum(r["skill_invoked"] is True for r in sel),
                mean_rca_tool_calls=round(st.mean(r["rca_tool_calls"] for r in sel), 1) if sel else None,
                mean_validator_tool_calls=round(st.mean(r["validator_tool_calls"] for r in sel), 1) if sel else None,
                validator_decisions={d: sum(r["validator_decision"] == d for r in sel)
                                     for d in sorted({r["validator_decision"] for r in sel})})
    json.dump(out, open(os.path.join(HERE, "summary.json"), "w"), indent=2)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
