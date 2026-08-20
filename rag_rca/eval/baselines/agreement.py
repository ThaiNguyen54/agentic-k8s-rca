# eval/baselines/agreement.py
"""C3 — inter-rater agreement to validate the LLM judge against humans.

Reads the filled annotation CSV (human_correct_0_1 columns from >=1 annotators) and
the hidden judge key, and reports:
  - human vs judge agreement + Cohen's kappa (does the LLM judge agree with humans?)
  - human vs human agreement + Cohen's kappa (if 2 annotator columns given)

Cohen's kappa is computed directly (no sklearn dependency required).

    python agreement.py --csv annotation_blind.csv --key annotation_key.json \
        --cols human_correct_0_1               # one annotator vs judge
    python agreement.py --csv a.csv --key k.json --cols ann1 ann2   # + human-human
"""
import argparse
import csv
import json


def kappa(a, b):
    """Cohen's kappa for two lists of binary labels."""
    n = len(a)
    if n == 0:
        return float("nan"), float("nan")
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pa1 = sum(a) / n; pb1 = sum(b) / n
    pe = pa1 * pb1 + (1 - pa1) * (1 - pb1)
    k = (po - pe) / (1 - pe) if pe != 1 else 1.0
    return po, k


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--cols", nargs="+", default=["human_correct_0_1"])
    args = ap.parse_args()

    key = {r["case_id"]: r for r in json.load(open(args.key))}
    rows = list(csv.DictReader(open(args.csv)))

    def col(name):
        out = {}
        for r in rows:
            v = (r.get(name) or "").strip()
            if v in ("0", "1"):
                out[r["case_id"]] = int(v)
        return out

    anns = {c: col(c) for c in args.cols}
    judge = {cid: (1 if key[cid]["judge_hit"] else 0) for cid in key}

    print("=" * 60)
    print("C3 — LLM-judge vs human agreement")
    print("=" * 60)
    for c, a in anns.items():
        ids = [cid for cid in a if cid in judge]
        if not ids:
            print(f"  {c}: no filled labels yet"); continue
        po, k = kappa([a[i] for i in ids], [judge[i] for i in ids])
        print(f"  {c:22s} vs judge:  agreement={po*100:4.0f}%  kappa={k:.3f}  (n={len(ids)})")

    if len(args.cols) >= 2:
        c1, c2 = args.cols[0], args.cols[1]
        ids = [cid for cid in anns[c1] if cid in anns[c2]]
        if ids:
            po, k = kappa([anns[c1][i] for i in ids], [anns[c2][i] for i in ids])
            print(f"  human-human ({c1} vs {c2}): agreement={po*100:4.0f}%  kappa={k:.3f}  (n={len(ids)})")
    print("=" * 60)
    print("kappa: >0.8 almost perfect, 0.6-0.8 substantial, 0.4-0.6 moderate.")


if __name__ == "__main__":
    main()
