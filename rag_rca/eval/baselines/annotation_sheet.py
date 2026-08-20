# eval/baselines/annotation_sheet.py
"""C3 — build a BLIND human-annotation sheet to validate the LLM judge.

Samples RCA responses from a results file, strips system/mode identifiers, shuffles,
and emits a CSV for >=2 human annotators to independently mark whether each response
correctly identifies the gold root cause (and optionally rate faithfulness /
specificity / usefulness on 1-5). The judge's own verdict is stored SEPARATELY (key
file) so it can't bias the annotator, then joined back for agreement analysis.

    python annotation_sheet.py --in results_20_llm.json --n 30 \
        --out annotation_blind.csv --key annotation_key.json
"""
import argparse
import csv
import json
import os
import random


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="results_20_llm.json")
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="annotation_blind.csv")
    ap.add_argument("--key", default="annotation_key.json")
    args = ap.parse_args()

    rows = [r for r in json.load(open(args.inp)) if r.get("response") and r.get("recall") is not None]
    rng = random.Random(args.seed)
    rng.shuffle(rows)
    sample = rows[:args.n]

    key = []
    with open(args.out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["case_id", "gold_root_cause", "rca_response",
                    "human_correct_0_1", "faithfulness_1_5", "specificity_1_5", "usefulness_1_5", "notes"])
        for i, r in enumerate(sample):
            cid = f"C{i:03d}"
            gold = r.get("culprit") or (r.get("gt", {}) or {}).get("primary")
            resp = (r["response"] or "").replace("\n", " ").replace("\r", " ").strip()
            w.writerow([cid, gold, resp[:4000], "", "", "", "", ""])
            key.append({"case_id": cid, "system": r.get("system", r.get("mode")),
                        "incident": r["incident"], "judge_hit": bool(r.get("hit", r["recall"] >= 0.999)),
                        "judge_recall": r["recall"], "judge_precision": r.get("precision")})
    json.dump(key, open(args.key, "w"), indent=2)
    print(f"wrote {len(sample)} blind cases -> {args.out}")
    print(f"judge key (hidden from annotators) -> {args.key}")
    print("Give the CSV to >=2 annotators; fill human_correct_0_1 (and 1-5 ratings if scoring validation).")


if __name__ == "__main__":
    main()
