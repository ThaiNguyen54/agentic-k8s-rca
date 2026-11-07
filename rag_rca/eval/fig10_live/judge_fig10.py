# eval/fig10_live/judge_fig10.py
"""Judge cross-check for the Fig. 10 live runs: the RQ1 judge prompt and fault
descriptions (rq1_clean/judge_rq1.py) applied to each scored answer. Writes
judge/<run>.json and judge_agreement.json (strict rubric vs judge).

    AAP_BASE_URL=... AAP_API_KEY=... python fig10_live/judge_fig10.py
"""
import csv
import glob
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

import httpx
from openai import OpenAI

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "rq1_clean"))
from gold import SCENARIOS  # noqa: E402
from judge_rq1 import DESC, PROMPT  # noqa: E402
from score_fig10 import answer  # noqa: E402

JUDGE = "qwen3.5-27b"


def judge_one(client, f):
    rec = json.load(open(f))
    out = os.path.join(HERE, "judge", f"{rec['config']}_{rec['condition']}_run{rec['run']:02d}.json")
    if os.path.exists(out):
        return f, "cached"
    ids = [x["id"] for x in SCENARIOS[rec["config"]]["faults"]]
    text = answer(rec)
    if not text.strip():  # error / timeout / empty: a miss, no judge call needed
        json.dump({"verdict": {i: False for i in ids}, "judge": None, "note": "no answer"}, open(out, "w"))
        return f, "no answer"
    msg = PROMPT.format(faults="\n".join(f"- {i}: {DESC[i]}" for i in ids), answer=text)
    try:
        r = client.chat.completions.create(model=JUDGE, messages=[{"role": "user", "content": msg}],
                                           temperature=0, max_tokens=16000)
        txt = r.choices[0].message.content or ""
        m = re.search(r"\{[^{}]*\}", txt.split("</think>")[-1], re.S)
        v = json.loads(m.group(0)) if m else None
        if not v or set(v) != set(ids):
            return f, f"bad verdict: {txt[-200:]!r}"
        json.dump({"verdict": {i: bool(v[i]) for i in ids}, "judge": JUDGE}, open(out, "w"))
        return f, "ok"
    except Exception as e:
        return f, repr(e)[:200]


def main():
    os.makedirs(os.path.join(HERE, "judge"), exist_ok=True)
    client = OpenAI(base_url=os.environ["AAP_BASE_URL"], api_key=os.environ["AAP_API_KEY"],
                    http_client=httpx.Client(verify=False, timeout=900), max_retries=1)
    files = sorted(glob.glob(os.path.join(HERE, "runs", "*", "*", "run*.json")))
    with ThreadPoolExecutor(3) as ex:
        for f, st in ex.map(lambda f: judge_one(client, f), files):
            if st not in ("cached", "ok", "no answer"):
                print(os.path.relpath(f, HERE), st, flush=True)

    rows = list(csv.DictReader(open(os.path.join(HERE, "scores.csv"))))
    n = agree = 0
    conf = {"both": 0, "rubric_only": 0, "judge_only": 0, "neither": 0}
    strict = {}
    for r in rows:
        jf = os.path.join(HERE, "judge", f"{r['config']}_{r['condition']}_run{int(r['run']):02d}.json")
        if not os.path.exists(jf):
            continue
        v = json.load(open(jf))["verdict"]
        for fid, jv in v.items():
            rv = r[f"strict:{fid}"] == "True"
            n += 1
            agree += rv == jv
            conf["both" if rv and jv else "rubric_only" if rv else "judge_only" if jv else "neither"] += 1
        strict.setdefault((r["condition"], r["level"]), []).append(all(v.values()))
    po = agree / n
    pr, pj = (conf["both"] + conf["rubric_only"]) / n, (conf["both"] + conf["judge_only"]) / n
    pe = pr * pj + (1 - pr) * (1 - pj)
    res = {"judge": JUDGE, "fault_decisions": n, "agreement_pct": round(100 * po, 1),
           "cohen_kappa": round((po - pe) / (1 - pe), 3) if pe < 1 else None, "confusion": conf,
           "judge_strict_accuracy_pct": {f"{c}/L{lvl}": round(100 * sum(v) / len(v), 1)
                                         for (c, lvl), v in sorted(strict.items())}}
    json.dump(res, open(os.path.join(HERE, "judge_agreement.json"), "w"), indent=2)
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
