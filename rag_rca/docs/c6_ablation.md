# C6 — Per-Component EFCA Ablation + Positional Study

Neural cross-encoder reranker (BAAI/bge-reranker-v2-m3, GPU). 15 concurrent-fault
incidents (1–4 faults, ~38–42k-token raw context), gpt-5 judge. Cumulative configs.

## Per-component ablation

| Config | Micro-recall | Strict hit (Wilson 95% CI) | Mean tokens |
|---|---|---|---|
| raw (no EFCA) | 87.1% | 80.0% [54.8, 93.0] | 39,497 |
| + top-k retrieval | 74.2% | 66.7% [41.7, 84.8] | 575 |
| + rerank (neural) | 77.4% | 73.3% [48.0, 89.1] | 569 |
| + compression | **93.5%** | **86.7% [62.1, 96.3]** | 810 |
| + edge placement | 90.3% | 86.7% [62.1, 96.3] | 810 |
| + on-demand expansion (full EFCA) | 83.9% | 73.3% [48.0, 89.1] | 820 |

## McNemar paired test (raw vs each stage, per-incident strict hit)

| Comparison | Δ | b / c | McNemar exact p |
|---|---|---|---|
| raw vs top-k | −13.3 pp | 3 / 1 | 0.625 |
| raw vs rerank | −6.7 pp | 1 / 0 | 1.000 |
| raw vs compress | +6.7 pp | 0 / 1 | 1.000 |
| raw vs edge | +6.7 pp | 0 / 1 | 1.000 |
| raw vs expand | −6.7 pp | 1 / 0 | 1.000 |

## Positional study (decisive signal first / middle / last)

| Position | Strict hit | Micro-recall |
|---|---|---|
| first | 86.7% | 90.3% |
| middle | 80.0% | 90.3% |
| last | 80.0% | 90.3% |

## Honest interpretation (what the data supports)

1. **Token reduction is the robust, large effect:** full EFCA reaches 84–94% at **~810
   tokens vs 39,497 for raw — a 48× (~98%) reduction.** This is the claim to lead with.
2. **Accuracy is parity, not a significant gain.** Full EFCA (compress/edge) matches or
   slightly exceeds raw on micro-recall (90–93% vs 87%), but **no stage differs from raw
   significantly** (all McNemar p ≥ 0.625; Wilson CIs overlap heavily at N=15). The
   original **+14 pp claim is not supported by this run** — re-scope to *"comparable
   accuracy at ~98% fewer tokens."*
3. **The component ladder is informative:** retrieval alone (top-k/rerank) *loses* ~10–13
   pp vs raw (recall drops to 74–77%) — cutting context naively hurts — but **compression
   recovers and slightly exceeds raw (93.5%)**. So the active ingredient is
   *selection + compression that keeps decisive SIGNAL lines verbatim*, not retrieval by
   itself. This directly answers "is the gain from architecture or just less context?" —
   naive less-context (top-k) is worse; structured selection is what works.
4. **`expand` ≈ `edge` (the dip is noise).** All incidents had top reranked score
   0.58–0.68, above the 0.35 gate, so **expansion never fired** — `expand`'s prompt ≈
   `edge`'s, and the 90.3→83.9 micro-recall difference is run-to-run RCA sampling variance,
   not an effect of expansion. State this; don't over-read it.
5. **Positional study is inconclusive — and the large-context follow-up confirms there is
   no lost-in-the-middle effect to exploit.** Within the *distilled* ~800-token prompt,
   signal position barely matters (strict hit first 86.7% vs middle/last 80%, not
   significant). The recommended raw-size follow-up has now been **run** (large-context
   sweep, signal moved to first/middle/last of a ~239k-token raw dump, N=8, gpt-5 judge):
   micro-recall **first 85.7% < middle 90.5% = last 90.5%** — i.e. the raw prompt shows
   *no* lost-in-the-middle penalty (middle is not worse). **Do not claim lost-in-the-middle
   motivation from this data; it contradicts it.**

6. **Large-context scaling adds efficiency, not accuracy-robustness (honest).** Sweeping raw
   context 85k→160k→250k tokens: raw 83.3/72.7/90.5% vs EFCA 100/77.3/90.5% micro-recall —
   EFCA is equal-or-better at every tier but the gap *shrinks* (+17/+5/0 pp) and ties at
   250k, with **no overflow** even at 239k tokens (N=8, CIs overlap heavily). The robust
   takeaway is the **token multiple growing with context** (≈100× at 85k → ≈275× at 250k,
   EFCA flat at ~840 tokens), which strengthens the *efficiency* claim, not a
   robustness-of-accuracy claim. See `docs/reframe_efca_contribution.md` and
   `eval/largectx_sweep.png`.

## Recommended paper wording (Exp. 4)

> The Evidence-First pipeline attains comparable localization accuracy to the full
> collect-all context (90–93% vs 87% micro-recall; differences not significant at N=15,
> McNemar p ≥ 0.6) while reducing prompt size by ~98% (≈810 vs ≈39.5k tokens). An ablation
> shows naive retrieval alone *reduces* accuracy (74–77%); compression that preserves
> decisive signal lines recovers it — indicating that *which* evidence reaches the model,
> not *how much*, drives grounded RCA.

## Regenerate
`python eval/baselines/c6_stats.py` (reads `efca_ablation_results.json`).
