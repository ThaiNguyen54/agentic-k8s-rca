# eval/baselines/efca_ablation_rca.py
"""C6 RCA+judge pass. Consumes the prompts built on the GPU node
(efca_ablation_prompts.json), runs each through the RCA agent, judges with the
gpt-5 judge, and aggregates per-config accuracy + the positional study.

Runs on the cluster host (needs kagent port-forward + judge). Neural-reranker
prompts were produced upstream by efca_ablation.py.

Env: RCA_AGENT_URL, JUDGE_AGENT_URL
    python efca_ablation_rca.py --in efca_ablation_prompts.json --out efca_ablation_results.json
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from e2e_ablation import call_rca
import judge as judgemod

CONFIG_ORDER = ["raw", "topk", "rerank", "compress", "edge", "expand"]
POS_ORDER = ["first", "middle", "last"]


def retry(fn, tries=4, base=3.0, label=""):
    last = None
    for t in range(tries):
        try:
            return fn()
        except Exception as e:
            last = e
            print(f"      retry {label} ({t+1}/{tries}): {str(e)[:90]}", flush=True)
            time.sleep(base * (t + 1))
    raise last


def judge_prompt(prompt_text, gt, out_rows, incident, config, tokens, kind):
    row = {"incident": incident, "config": config, "kind": kind, "tokens": tokens}
    try:
        text, lat, _ = retry(lambda: call_rca(prompt_text, timeout=300), label="rca")
        jr = retry(lambda: judgemod.judge(text, gt), label="judge")
        row.update({"latency_s": round(lat, 1), "recall": jr["judge_recall"],
                    "precision": jr["judge_precision"], "tp": jr["judge_tp"],
                    "faults": len(gt["primary"]), "response": text})
        print(f"    {config:9s} recall={jr['judge_recall']} tok={tokens} {lat:.0f}s", flush=True)
    except Exception as e:
        row.update({"error": str(e)[:200], "recall": None, "faults": len(gt["primary"])})
        print(f"    {config:9s} ! FAILED {str(e)[:90]}", flush=True)
    out_rows.append(row)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="efca_ablation_prompts.json")
    ap.add_argument("--out", default="efca_ablation_results.json")
    args = ap.parse_args()
    data = json.load(open(args.inp))
    print(f"reranker={data.get('reranker')}  incidents={len(data['incidents'])}\n")

    rows = []
    for rec in data["incidents"]:
        name = rec["incident"]
        # normalize gt primary tuples (json turns tuples into lists)
        gt = {"primary": [tuple(p) for p in rec["gt"]["primary"]],
              "distractors": rec["gt"].get("distractors", [])}
        print(f"=== {name} ({rec['faults']} faults, top_score={rec['top_score']}) ===", flush=True)
        for cfg in CONFIG_ORDER:
            c = rec["configs"].get(cfg)
            if c:
                judge_prompt(c["prompt"], gt, rows, name, cfg, c["tokens"], "ablation")
                json.dump(rows, open(args.out, "w"), indent=2)
        for pos in POS_ORDER:
            p = rec["positional"].get(pos)
            if p:
                judge_prompt(p["prompt"], gt, rows, name, pos, p["tokens"], "positional")
                json.dump(rows, open(args.out, "w"), indent=2)
    aggregate(rows)


def _micro(rows):
    tp = sum(r["tp"] for r in rows if r.get("recall") is not None)
    faults = sum(r["faults"] for r in rows if r.get("recall") is not None)
    return (100.0 * tp / faults) if faults else float("nan")


def aggregate(rows):
    print("\n" + "=" * 72)
    print("EFCA ABLATION (neural reranker) — micro-averaged localization accuracy")
    print("=" * 72)
    print(f"{'Config':<12}{'Accuracy':<12}{'Mean tokens':<14}{'n'}")
    print("-" * 72)
    for cfg in CONFIG_ORDER:
        rs = [r for r in rows if r["config"] == cfg and r["kind"] == "ablation" and r.get("recall") is not None]
        if rs:
            acc = _micro(rs); tok = sum(r["tokens"] for r in rs) / len(rs)
            print(f"{cfg:<12}{acc:>6.1f}%     {tok:>10,.0f}    {len(rs)}")
    print("\nPOSITIONAL STUDY — decisive signal at first / middle / last")
    print("-" * 72)
    for pos in POS_ORDER:
        rs = [r for r in rows if r["config"] == pos and r["kind"] == "positional" and r.get("recall") is not None]
        if rs:
            print(f"{pos:<12}{_micro(rs):>6.1f}%     (n={len(rs)})")
    print("=" * 72)


if __name__ == "__main__":
    main()
