# eval/judge_agreement.py
"""C3 — inter-judge agreement (is the LLM-as-judge verdict model-robust?).

Re-judges the same candidate RCA responses with TWO independent judge models
(rca-judge = gpt-5, rca-judge-2 = o4-mini), both blind and using the identical
rubric, then reports Cohen's kappa + raw agreement on the per-item primary-hit
decision. High agreement => the reported accuracies are not an artifact of one
judge model. This complements (not replaces) human annotation.

Run: KUBECONFIG unneeded (hits kagent A2A over the port-forward)
    python eval/judge_agreement.py --in results_20_llm.json --out judge_agreement.json
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "baselines")))

import judge as J
from dataset_loader import load_incidents

J1 = "http://127.0.0.1:8083/api/a2a/kagent/rca-judge/"     # gpt-5
J2 = "http://127.0.0.1:8083/api/a2a/kagent/rca-judge-2/"   # gpt-5-mini (only quota-available 2nd model)


def hit_from(url, candidate, gt, retries=3):
    """Return 1 if the judge marks the single primary cause as primary, else 0."""
    primary = "\n".join(f"- {obj}: {', '.join(kws[:3])}" for obj, kws in gt["primary"])
    distract = ", ".join(gt.get("distractors", []))
    gold = f"\nAUTHORITATIVE root-cause description: {gt['gold_text']}\n" if gt.get("gold_text") else ""
    prompt = (f"GROUND_TRUTH primary root causes:\n{primary}\n{gold}\n"
              f"DISTRACTORS (must NOT be primary): {distract}\n\n"
              f"CANDIDATE analysis:\n\"\"\"\n{candidate}\n\"\"\"\n\nReturn ONLY the JSON verdict.")
    for _ in range(retries):
        try:
            raw = J._text(J._post(url, prompt, 180))
            # never count an API/quota/connection error as a "miss"
            if any(s in raw for s in ("insufficient_quota", "API_ERROR", "Connection error", '"code": 429')):
                time.sleep(6); continue
            v = J._first_json(raw)
            ident = v.get("identified", [])
            if not ident and "identified" not in raw:   # unparseable -> not a valid verdict
                time.sleep(4); continue
            tp = sum(1 for x in ident if x.get("is_primary"))
            return 1 if tp >= len(gt["primary"]) else 0
        except Exception:
            time.sleep(5)
    return None


def cohen_kappa(a, b):
    """Cohen's kappa for two binary label lists."""
    n = len(a)
    if not n:
        return float("nan")
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pa1 = sum(a) / n; pb1 = sum(b) / n
    pe = pa1 * pb1 + (1 - pa1) * (1 - pb1)
    return (po - pe) / (1 - pe) if pe != 1 else 1.0, po


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="results_20_llm.json")
    ap.add_argument("--out", default="judge_agreement.json")
    ap.add_argument("--systems", nargs="*", default=None, help="limit to these systems")
    args = ap.parse_args()

    gt_map = {iid: gt for iid, _b, gt, _m in load_incidents()}
    cands = json.load(open(args.inp))
    if args.systems:
        cands = [c for c in cands if c["system"] in args.systems]
    print(f"judging {len(cands)} candidates with 2 judges (gpt-5 vs o4-mini)\n", flush=True)

    rows, j1v, j2v = [], [], []
    for i, c in enumerate(cands):
        gt = gt_map.get(c["incident"])
        if not gt or "response" not in c:
            continue
        h1 = hit_from(J1, c["response"], gt)
        h2 = hit_from(J2, c["response"], gt)
        if h1 is None or h2 is None:
            print(f"  [{i}] {c['incident']}/{c['system']}: judge error, skipped", flush=True)
            continue
        j1v.append(h1); j2v.append(h2)
        rows.append({"incident": c["incident"], "system": c["system"], "j1_gpt5": h1, "j2_o4mini": h2})
        print(f"  [{i}] {c['incident']:16s} {c['system']:16s} j1={h1} j2={h2} {'' if h1==h2 else '<DISAGREE>'}", flush=True)
        json.dump({"rows": rows}, open(args.out, "w"), indent=2)

    k, po = cohen_kappa(j1v, j2v)
    disagree = sum(1 for x, y in zip(j1v, j2v) if x != y)
    summary = {"n": len(j1v), "raw_agreement": round(po, 3), "cohen_kappa": round(k, 3),
               "disagreements": disagree, "j1_pos_rate": round(sum(j1v) / len(j1v), 3),
               "j2_pos_rate": round(sum(j2v) / len(j2v), 3),
               "judges": {"j1": "gpt-5 (rca-judge)", "j2": "o4-mini (rca-judge-2)"}}
    json.dump({"summary": summary, "rows": rows}, open(args.out, "w"), indent=2)
    print("\n" + "=" * 60)
    print(f"C3 INTER-JUDGE AGREEMENT (n={summary['n']})")
    print(f"  raw agreement : {po:.1%}")
    print(f"  Cohen's kappa : {k:.3f}")
    print(f"  disagreements : {disagree}")
    print(f"  positive rate : gpt-5 {summary['j1_pos_rate']:.1%} | o4-mini {summary['j2_pos_rate']:.1%}")
    print("=" * 60)


if __name__ == "__main__":
    main()
