# eval/baselines/stats_ci.py
"""C9 — statistical reliability for the paper.

Two tools the reviewer asked for:
  * Wilson score 95% CI for proportions (localization %, remediation %) — correct
    near 99/100% where the normal approximation fails.
  * McNemar's paired test on per-case raw-vs-EFCA hit/miss — the rigorous way to
    back the +4/+8/+14 pp claim, since each case is a matched pair (a paired test
    can be significant even when the two marginal CIs overlap).

Computes real numbers on the paired data available in suite_results.json
(raw vs distilled, matched by incident+rep). The paper's A/B/C scenario data
plugs into the same functions once T and the per-case pairs are supplied.
"""
import json
import math
import os

EVAL = os.path.dirname(os.path.dirname(__file__))
Z = 1.96  # 95%


def wilson(k, n, z=Z):
    """Wilson score interval for k successes in n trials. Returns (p, lo, hi) in %."""
    if n == 0:
        return float("nan"), float("nan"), float("nan")
    p = k / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return 100 * p, 100 * (centre - half) / denom, 100 * (centre + half) / denom


def mcnemar(b, c):
    """McNemar on discordant pairs: b=(raw right, EFCA wrong), c=(raw wrong, EFCA right).
    Returns (chi2_cc, p_chi2_cc, p_exact). Exact = two-sided binomial(min(b,c); b+c, .5)."""
    n = b + c
    if n == 0:
        return 0.0, 1.0, 1.0
    chi2 = (abs(b - c) - 1) ** 2 / n  # continuity-corrected
    # chi2 survival, df=1
    try:
        from scipy.stats import chi2 as _chi2, binomtest
        p_chi2 = float(_chi2.sf(chi2, 1))
        p_exact = float(binomtest(min(b, c), n, 0.5, alternative="two-sided").pvalue)
    except Exception:
        p_chi2 = math.erfc(math.sqrt(chi2 / 2))  # crude fallback
        p_exact = p_chi2
    return chi2, p_chi2, p_exact


def _hit(r):
    return 1 if (r.get("recall") is not None and r["recall"] >= 0.999) else 0


def paired_from_suite(path="suite_results.json"):
    """Build matched raw-vs-distilled pairs keyed by (incident, rep)."""
    p = os.path.join(EVAL, path)
    if not os.path.isfile(p):
        return None
    rows = json.load(open(p))
    by_key = {}
    for r in rows:
        if r.get("recall") is None:
            continue
        by_key.setdefault((r["incident"], r.get("rep")), {})[r["mode"]] = _hit(r)
    pairs = [(v["raw"], v["distilled"]) for v in by_key.values() if "raw" in v and "distilled" in v]
    return pairs


def main():
    print("=" * 74)
    print("C9 — Wilson 95% CIs + McNemar paired test")
    print("=" * 74)

    pairs = paired_from_suite()
    if pairs:
        n = len(pairs)
        raw_k = sum(a for a, _ in pairs); efca_k = sum(b for _, b in pairs)
        b = sum(1 for a, e in pairs if a == 1 and e == 0)   # raw right, EFCA wrong
        c = sum(1 for a, e in pairs if a == 0 and e == 1)   # raw wrong, EFCA right
        pr, plo, phi = wilson(raw_k, n)
        er, elo, ehi = wilson(efca_k, n)
        chi2, p_chi2, p_exact = mcnemar(b, c)
        print(f"\nPaired cases (suite_results, raw vs EFCA): T = {n}")
        print(f"  raw   accuracy {pr:5.1f}%   Wilson 95% CI [{plo:.1f}, {phi:.1f}]  ({raw_k}/{n})")
        print(f"  EFCA  accuracy {er:5.1f}%   Wilson 95% CI [{elo:.1f}, {ehi:.1f}]  ({efca_k}/{n})")
        print(f"  discordant pairs: b(raw✓ EFCA✗)={b}  c(raw✗ EFCA✓)={c}")
        print(f"  McNemar: chi2_cc={chi2:.3f}  p(chi2)={p_chi2:.4f}  p(exact binomial)={p_exact:.4f}")
        sig = "SIGNIFICANT" if p_exact < 0.05 else "NOT significant"
        print(f"  -> raw vs EFCA difference is {sig} at alpha=0.05 on this suite")

    # worked Wilson example from the reviewer (T=30) so the paper text can cite it
    print("\nWorked Wilson examples (T=30):")
    for label, k in [("EFCA 86%", 26), ("raw 72%", 22)]:
        p, lo, hi = wilson(k, 30)
        print(f"  {label:10s} ({k}/30) -> {p:.0f}%  95% CI [{lo:.1f}, {hi:.1f}]")
    print("=" * 74)


if __name__ == "__main__":
    main()
