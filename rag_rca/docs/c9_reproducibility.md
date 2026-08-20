# C9 — Experimental Protocol + Statistical Reliability

Addresses: no N / reps / seeds / temperature / prompt templates / CIs reported, and
the reliability of the +4/+8/+14 pp EFCA claim.

Method choices (per reviewer): **Wilson score** CIs for proportions (correct near
99–100%), and **McNemar's paired test** for the raw-vs-EFCA difference (each case is a
matched pair, so a paired test is far stronger than comparing two marginal CIs).

---

## 1. Experimental protocol table

Placement: Section V (Experimental Setup), right after the "Fault injection" paragraph.
Lead-in: *"The full experimental protocol is summarized in Table~\ref{tab:protocol}."*

```latex
\begin{table}[!t]
\caption{Experimental Protocol}
\label{tab:protocol}
\centering
\small
\begin{tabular}{p{3.0cm} p{4.8cm}}
\hline
\textbf{Item} & \textbf{Setting} \\
\hline
Backends (Exp.\ 1--2)   & GPT-4o, Llama-3.3-70B-Instruct, Qwen2.5-72B \\
Backend (Exp.\ 3--4)    & GPT-4o (fixed) \\
Fault suite             & 11 fault types (Table~\ref{tab:faults}) \\
Scenarios (Exp.\ 3--4)  & A: 1 fault; B: 2 faults; C: 3 concurrent faults \\
Cases per scenario ($T$) & <FILL: e.g. 30> \\
Repetitions per case    & <FILL: e.g. 3> \\
Total runs              & <FILL: $T\times$reps$\times$conditions> \\
Sampling temperature    & <FILL: e.g. 0.0> \\
Top-$p$ / max tokens     & <FILL> \\
Random seed(s)          & <FILL> \\
Fault-injection window  & <FILL: e.g. 120--210\,s per case> \\
Injection tools         & Chaos Mesh, LitmusChaos, CLI \\
Judge model (Exp.\ 2)   & <FILL: distinct from judged model> \\
Judging protocol        & LLM-as-judge, fixed rubric (Fig.~\ref{fig:rubric}); blind to system/mode \\
Proportion CI           & Wilson score, 95\% \\
Time CI                 & mean $\pm$ std over repetitions \\
\hline
\end{tabular}
\end{table}
```

The six `<FILL>` rows are facts about your runs — pull from config/logs. Highest-leverage:
**sampling temperature** and **judge model** (the judge feeds C3; must be distinct from the
judged model).

---

## 2. Wilson score interval (proportions)

For a proportion $p=k/T$ at 95% ($z=1.96$):

```latex
\mathrm{CI} = \frac{p + \frac{z^2}{2T} \pm z\sqrt{\frac{p(1-p)}{T} + \frac{z^2}{4T^2}}}{1 + \frac{z^2}{T}}
```

**Worked examples (T=30, verified by `stats_ci.py`):**
- EFCA scenario C, 26/30 = 86% → 95% Wilson CI **[70.3, 94.7]**
- raw scenario C, 22/30 = 72% → 95% Wilson CI **[55.6, 85.8]**

These overlap → at T=30 the +14 pp is *suggestive but not significant on the marginals
alone*. This is why the paired test (below) is required.

CI table to add (fill with your Table III/IV proportions):

```latex
\begin{table}[!t]
\caption{Accuracy with 95\% Wilson Confidence Intervals}
\label{tab:protocol-ci}
\centering
\small
\begin{tabular}{l l c c}
\hline
\textbf{Experiment} & \textbf{Condition} & \textbf{Acc. (\%)} & \textbf{95\% Wilson CI} \\
\hline
Exp.\ 4 Scenario A & raw / EFCA & <>/<> & [<>,<>] / [<>,<>] \\
Exp.\ 4 Scenario B & raw / EFCA & <>/<> & [<>,<>] / [<>,<>] \\
Exp.\ 4 Scenario C & raw / EFCA & <>/<> & [<>,<>] / [<>,<>] \\
\hline
\end{tabular}
\end{table}
```

---

## 3. McNemar's paired test (defends the +4/+8/+14 pp)

Each case is run under **both** raw and EFCA, so use the matched pairs. With
$b$ = #cases raw correct but EFCA wrong, $c$ = #cases raw wrong but EFCA correct:

```latex
\chi^2_{\mathrm{cc}} = \frac{(|b-c|-1)^2}{b+c}, \quad \text{df}=1
```

Report the exact two-sided binomial $p$ when $b+c$ is small. A paired test can be
**significant even when the marginal Wilson CIs overlap**, because it uses the case-by-case
pairing the design already has.

**Results-text to add after the "+4, +8, +14 percentage points" line (Exp. 4):**

> Across $T=<FILL>$ paired cases per scenario, the localization gains are statistically
> assessed with McNemar's test on per-case hit/miss pairs (raw vs.\ EFCA); the improvement
> is significant for scenario~C ($p=<FILL>$), while 95\% Wilson intervals for the marginal
> accuracies are reported in Table~\ref{tab:protocol-ci}.

---

## 3b. Recovered protocol values (measured from cluster CRs + runner code, 2026-07)

These are the **actual** settings of the runs whose artifacts are on disk — use these to fill
the `<FILL>` rows, NOT assumed values:

| Protocol item | Recovered value | Source of truth |
|---|---|---|
| RCA / system model | **gpt-5-mini** | kagent `k8s-agent` CR (`modelConfig: openai-gpt-5-mini`) |
| Judge model | **gpt-5** (distinct from judged → no self-bias) | kagent `rca-judge` CR |
| Validator model | gpt-5-mini | kagent `validator` CR |
| Random seed | 1234 (incident gen); 42 (annotation sampling) | `benchmark_suite.py`, `annotation_sheet.py` |
| Repetitions | 3 (rep 0,1,2) | `suite_results.json`, `ablation_rca.json` |
| N (Exp 1, real) | 20 incidents × 4 systems | `results_20_llm.json` |
| N (Exp 4, synthetic) | 15 incidents × 3 reps | `suite_results.json` |
| N (C6 ablation) | 15 incidents | `efca_ablation_results.json` |
| Sampling temperature | **not pinned → provider default** (disclose as such) | absent from all runner code |
| Judging protocol | LLM-as-judge, fixed JSON rubric, blind to system/mode | `rca-judge` systemMessage |

### ⚠️ Backend discrepancy — MUST reconcile before submitting the protocol table

The reviewer's drafted table lists *"Backends (Exp 1–2): GPT-4o, Llama-3.3-70B, Qwen2.5-72B;
(Exp 3–4): GPT-4o fixed."* **No artifact on disk supports this.** Every eval output used a
**single backend, gpt-5-mini** (gpt-5 judge). Exp 1 compares four *systems*
(yours_distilled / yours_raw / RCACopilot / RCAgent), not multiple LLM backends — there is
no per-backend result file. Two honest resolutions:

1. **If a GPT-4o/Llama/Qwen run exists elsewhere:** supply the artifacts; the table gets real
   per-backend numbers.
2. **If not (current evidence):** correct the table to **single backend gpt-5-mini, gpt-5
   judge**, drop the multi-backend row, and (optionally) run a real multi-backend study later
   — the cluster already has `llama33-fp8` and `deepseek-r1` ModelConfigs, so it is feasible.

Do NOT publish the GPT-4o/Llama/Qwen row unless backed by artifacts. This is the same class
of overclaim as the +14 pp accuracy claim (C6).

## 4. What I need from you to finalize the real numbers

1. **$T$ (cases per scenario) and repetitions** → I compute the actual Wilson CIs for all
   six Table III/IV proportions and hand back a filled `tab:protocol-ci`.
2. **Per-case raw-vs-EFCA hit/miss pairs** (or just the discordant counts $b$ and $c$ per
   scenario) → I compute the real McNemar $p$-values so the +14 pp is backed.
3. **Temperature** and **judge model** → the two highest-leverage `<FILL>` rows.

Both computations are already implemented in `eval/baselines/stats_ci.py`
(`wilson(k,n)`, `mcnemar(b,c)`); plugging your counts in yields the final numbers.

---

## 5. Real numbers — from the C6 ablation run (neural reranker, per option #2)

Definitive source: the C6 run (15 concurrent-fault incidents, ~40k-token raw context,
neural bge-reranker-v2-m3, gpt-5 judge). Full breakdown in `docs/c6_ablation.md`;
computed by `c6_stats.py`.

| Condition | Strict hit | 95% Wilson CI | Micro-recall | Tokens |
|---|---|---|---|---|
| raw (no EFCA) | 80.0% (12/15) | [54.8, 93.0] | 87.1% | 39,497 |
| EFCA (compress) | 86.7% (13/15) | [62.1, 96.3] | 93.5% | 810 |
| EFCA (edge, full) | 86.7% (13/15) | [62.1, 96.3] | 90.3% | 810 |

**McNemar, raw vs EFCA (edge), per-incident strict hit:** b=0, c=1 → exact **p = 1.00**.

**Conclusion (honest, per option #3):** the accuracy difference is **not statistically
significant** (McNemar p ≥ 0.6; Wilson CIs overlap heavily at N=15). The **+14 pp claim is
not supported** by the measured data. What *is* supported and should be the headline:
**comparable accuracy at ~98% fewer tokens** (≈810 vs ≈39.5k). The ablation additionally
shows naive retrieval *reduces* accuracy while compression recovers it (see C6) — i.e. the
effect is *which* evidence reaches the model, not *how much*. To claim any accuracy gain,
either raise N until the CI/McNemar clears significance, or scope it to a named regime
(large-context lost-in-the-middle, which needs the raw-size positional test in C6 §5).
