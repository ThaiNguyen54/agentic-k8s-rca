# eval/judge.py
"""LLM-judge for RCA responses (fixes the string-match precision artifact).

Uses an independent gpt-5 judge agent (rca-judge) to decide, for each true root
cause, whether the candidate names it as a PRIMARY root cause, and whether it
misattributes any cascade/distractor as a root cause. Judges conclusions, not
incidental mentions — so correctly labeling something a "cascading effect" is
NOT penalised.

CLI:  python eval/judge.py --in hard_results.json --out hard_judged.json
"""
import argparse
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from hard_scenarios import hard_B, hard_C

JUDGE_AGENT_URL = os.getenv("JUDGE_AGENT_URL", "http://127.0.0.1:8083/api/a2a/kagent/rca-judge/")

_GT = {"Hard-B (2 faults)": hard_B()[2], "Hard-C (3 faults)": hard_C()[2]}


def _post(url, prompt, timeout=180):
    body = {"jsonrpc": "2.0", "id": f"judge-{int(time.time()*1000)}", "method": "message/send",
            "params": {"message": {"role": "user",
                                   "parts": [{"kind": "text", "text": prompt}],
                                   "messageId": f"msg-{int(time.time()*1000)}"}}}
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _text(obj):
    try:
        for a in obj["result"]["artifacts"]:
            for p in a.get("parts", []):
                if p.get("text", "").strip():
                    return p["text"]
    except Exception:
        pass
    return json.dumps(obj)


def _first_json(s):
    b = s.find("{")
    if b < 0:
        return {}
    d = 0
    for i in range(b, len(s)):
        if s[i] == "{":
            d += 1
        elif s[i] == "}":
            d -= 1
            if d == 0:
                try:
                    return json.loads(s[b:i + 1])
                except Exception:
                    return {}
    return {}


def judge(candidate, gt, timeout=180):
    primary = "\n".join(f"- {obj}: {', '.join(kws[:3])}" for obj, kws in gt["primary"])
    distract = ", ".join(gt.get("distractors", []))
    gold = f"\nAUTHORITATIVE root-cause description: {gt['gold_text']}\n" if gt.get("gold_text") else ""
    prompt = (f"GROUND_TRUTH primary root causes:\n{primary}\n{gold}\n"
              f"DISTRACTORS (cascading effects / unrelated — must NOT be primary root causes): {distract}\n\n"
              f"CANDIDATE analysis:\n\"\"\"\n{candidate}\n\"\"\"\n\n"
              "Return ONLY the JSON verdict.")
    verdict = _first_json(_text(_post(JUDGE_AGENT_URL, prompt, timeout)))
    ident = verdict.get("identified", [])
    tp = sum(1 for x in ident if x.get("is_primary"))
    total = len(gt["primary"])
    mis = len(verdict.get("misattributed", []) or [])
    recall = tp / total if total else 0.0
    precision = tp / (tp + mis) if (tp + mis) else 0.0
    f1 = 2 * recall * precision / (recall + precision) if (recall + precision) else 0.0
    return {"judge_recall": round(recall, 3), "judge_precision": round(precision, 3),
            "judge_f1": round(f1, 3), "judge_tp": tp, "judge_misattributed": mis,
            "judge_total": total, "judge_raw": verdict}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    rows = json.load(open(args.inp))
    for r in rows:
        gt = _GT.get(r["scenario"])
        if not gt or "response" not in r:
            continue
        print(f"  judging {r['scenario']} · {r['mode']} · rep {r.get('rep')} ...", flush=True)
        try:
            r.update(judge(r["response"], gt))
            print(f"    judge recall {r['judge_recall']} precision {r['judge_precision']} f1 {r['judge_f1']} (misattr {r['judge_misattributed']})")
        except Exception as e:
            print(f"    ! judge failed: {e}")
            r["judge_error"] = str(e)
    json.dump(rows, open(args.out, "w"), indent=2)
    print(f"\nsaved → {args.out}")


if __name__ == "__main__":
    main()
