# eval/baselines/efca_largecontext_rca.py
"""RCA+judge for the large-context stress + positional test. Detects context
OVERFLOW (raw too big for the model window) as a distinct outcome, and reports
accuracy vs. context size for raw vs EFCA, plus the positional study.

Env: RCA_AGENT_URL, JUDGE_AGENT_URL
    python efca_largecontext_rca.py --in efca_largectx_prompts.json --out efca_largectx_results.json
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from e2e_ablation import call_rca
import judge as judgemod
from stats_ci import wilson, mcnemar

SIZES_ORDER = ["~85k", "~160k", "~250k"]


def is_overflow(msg):
    m = msg.lower()
    return any(k in m for k in ["context", "maximum", "length", "token", "too large", "400", "413"])


def run(prompt, gt, timeout=420):
    """Return dict with recall/tp or overflow/error."""
    try:
        text, lat, _ = call_rca(prompt, timeout=timeout)
        jr = judgemod.judge(text, gt)
        return {"ok": True, "latency_s": round(lat, 1), "recall": jr["judge_recall"],
                "tp": jr["judge_tp"], "precision": jr["judge_precision"]}
    except Exception as e:
        msg = str(e)
        return {"ok": False, "overflow": is_overflow(msg), "recall": 0.0, "tp": 0, "error": msg[:180]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="efca_largectx_prompts.json")
    ap.add_argument("--out", default="efca_largectx_results.json")
    args = ap.parse_args()
    data = json.load(open(args.inp))
    print(f"reranker={data.get('reranker')} records={len(data['records'])}\n")

    rows = []
    for rec in data["records"]:
        gt = {"primary": [tuple(p) for p in rec["gt"]["primary"]],
              "distractors": rec["gt"].get("distractors", [])}
        faults = rec["faults"]
        print(f"=== {rec['size']} {rec['incident']} ({faults}f) ===", flush=True)
        for cfg in ("raw", "efca"):
            c = rec["configs"][cfg]
            res = run(c["prompt"], gt)
            row = {"kind": "size", "size": rec["size"], "incident": rec["incident"],
                   "config": cfg, "faults": faults, "tokens": c["tokens"], **res}
            rows.append(row)
            tag = "OVERFLOW" if (not res["ok"] and res.get("overflow")) else ("ok" if res["ok"] else "ERR")
            print(f"  {cfg:5s} {tag:9s} recall={res['recall']} tok={c['tokens']:,}", flush=True)
            json.dump(rows, open(args.out, "w"), indent=2)
        for pos, pc in (rec.get("positional") or {}).items():
            res = run(pc["prompt"], gt)
            rows.append({"kind": "positional", "size": rec["size"], "incident": rec["incident"],
                         "config": pos, "faults": faults, "tokens": pc["tokens"], **res})
            print(f"  pos:{pos:6s} recall={res['recall']}", flush=True)
            json.dump(rows, open(args.out, "w"), indent=2)
    aggregate(rows)


def _micro(rs):
    tp = sum(r["tp"] for r in rs); fa = sum(r["faults"] for r in rs)
    return (100.0 * tp / fa) if fa else float("nan")


def aggregate(rows):
    print("\n" + "=" * 78)
    print("LARGE-CONTEXT STRESS — accuracy vs raw context size")
    print("=" * 78)
    print(f"{'Size':<10}{'raw acc':<12}{'raw overflow':<14}{'EFCA acc':<12}{'EFCA tok':<10}")
    print("-" * 78)
    for sz in SIZES_ORDER:
        rw = [r for r in rows if r["kind"] == "size" and r["size"] == sz and r["config"] == "raw"]
        ef = [r for r in rows if r["kind"] == "size" and r["size"] == sz and r["config"] == "efca"]
        if not rw:
            continue
        ovf = sum(1 for r in rw if not r["ok"] and r.get("overflow"))
        raw_acc = _micro([r for r in rw if r["ok"]]) if any(r["ok"] for r in rw) else 0.0
        # count failed raw as 0 accuracy (it produced no diagnosis)
        raw_acc_all = _micro(rw)
        ef_acc = _micro(ef)
        eftok = sum(r["tokens"] for r in ef) / len(ef)
        print(f"{sz:<10}{raw_acc_all:>5.1f}%      {ovf}/{len(rw)}        {ef_acc:>5.1f}%      {eftok:>7,.0f}")
    print("\nPOSITIONAL (largest size) — decisive signal in RAW dump")
    print("-" * 78)
    for pos in ("first", "middle", "last"):
        rs = [r for r in rows if r["kind"] == "positional" and r["config"] == pos]
        if rs:
            k = sum(1 for r in rs if r["recall"] >= 0.999); n = len(rs)
            p, lo, hi = wilson(k, n)
            print(f"  {pos:<8}{_micro(rs):>5.1f}% micro   strict {p:.0f}% [{lo:.0f},{hi:.0f}]  (n={n})")
    print("=" * 78)


if __name__ == "__main__":
    main()
