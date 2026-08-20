# eval/baselines/c6_stats.py
"""C6 + C9 finalizer. Reads efca_ablation_results.json (per-component ablation +
positional study, neural reranker) and computes, with real numbers:

  * per-config micro-accuracy (tp/faults) and strict per-incident hit-rate + Wilson CI
  * per-config mean prompt tokens
  * McNemar paired test raw-vs-EFCA (and raw-vs-each-stage) on per-incident strict hits
  * positional study (decisive signal first/middle/last) accuracy + Wilson CI

This produces the honest numbers to fill C9's tab:protocol-ci and back (or re-scope)
the EFCA accuracy claim from the measured effect.
"""
import json
import os
from stats_ci import wilson, mcnemar   # reuse the same functions

EVAL = os.path.dirname(os.path.dirname(__file__))
CONFIGS = ["raw", "topk", "rerank", "compress", "edge", "expand"]
POS = ["first", "middle", "last"]


def load():
    p = os.path.join(EVAL, "efca_ablation_results.json")
    return json.load(open(p)) if os.path.isfile(p) else []


def strict_hit(r):
    return 1 if (r.get("recall") is not None and r["recall"] >= 0.999) else 0


def by_config(rows, kind, names):
    out = {}
    for cfg in names:
        rs = [r for r in rows if r["config"] == cfg and r["kind"] == kind and r.get("recall") is not None]
        out[cfg] = rs
    return out


def main():
    rows = load()
    if not rows:
        print("no efca_ablation_results.json yet"); return
    print("=" * 82)
    print("C6 — EFCA per-component ablation (neural reranker bge-reranker-v2-m3)")
    print("=" * 82)

    abl = by_config(rows, "ablation", CONFIGS)
    # strict per-incident hit table with Wilson; micro fault-recall; tokens
    print(f"{'Config':<10}{'Strict hit (Wilson 95% CI)':<30}{'Micro-recall':<14}{'Mean tok':<10}")
    print("-" * 82)
    hits = {}
    for cfg in CONFIGS:
        rs = abl[cfg]
        if not rs:
            print(f"{cfg:<10}(no data)"); continue
        n = len(rs); k = sum(strict_hit(r) for r in rs)
        hits[cfg] = {r["incident"]: strict_hit(r) for r in rs}
        p, lo, hi = wilson(k, n)
        tp = sum(r["tp"] for r in rs); fa = sum(r["faults"] for r in rs)
        micro = 100 * tp / fa if fa else float("nan")
        tok = sum(r["tokens"] for r in rs) / n
        print(f"{cfg:<10}{p:5.1f}% [{lo:4.1f},{hi:5.1f}]  ({k}/{n}){'':<6}{micro:5.1f}%{'':<7}{tok:8,.0f}")

    # McNemar: raw vs each later stage, paired by incident (strict hit)
    print("\nMcNemar paired vs raw (strict per-incident hit):")
    print("-" * 82)
    base = hits.get("raw", {})
    for cfg in CONFIGS[1:]:
        h = hits.get(cfg, {})
        common = [i for i in base if i in h]
        b = sum(1 for i in common if base[i] == 1 and h[i] == 0)  # raw hit, cfg miss
        c = sum(1 for i in common if base[i] == 0 and h[i] == 1)  # raw miss, cfg hit
        chi2, p_chi2, p_exact = mcnemar(b, c)
        d = 100 * (sum(h.values()) - sum(base[i] for i in common)) / max(1, len(common))
        print(f"  raw vs {cfg:<9} delta={d:+5.1f}pp  b={b} c={c}  McNemar exact p={p_exact:.3f}")

    # positional study
    pos = by_config(rows, "positional", POS)
    if any(pos.values()):
        print("\nPositional study — decisive signal position (Wilson 95% CI):")
        print("-" * 82)
        for k_ in POS:
            rs = pos[k_]
            if rs:
                n = len(rs); kk = sum(strict_hit(r) for r in rs)
                tp = sum(r["tp"] for r in rs); fa = sum(r["faults"] for r in rs)
                p, lo, hi = wilson(kk, n)
                micro = 100 * tp / fa if fa else float("nan")
                print(f"  {k_:<8}{p:5.1f}% [{lo:4.1f},{hi:5.1f}]  micro-recall={micro:5.1f}%  (n={n})")
    print("=" * 82)
    print("Note: strict hit = all faults in the incident localized (recall=1.0).")


if __name__ == "__main__":
    main()
