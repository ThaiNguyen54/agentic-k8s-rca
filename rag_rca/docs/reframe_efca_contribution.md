# Reframe — EFCA contribution (efficiency + robustness, not +14pp accuracy)

The paper's Exp-4 accuracy claim (+4/+8/+14 pp, best = full EFCA) is not supported
by any run in the repo. Three independent measurements all show **accuracy parity**:

- `suite_results.json` (Exp-4 source): raw 100/100/72.2% vs distilled 100/97.6/75.0%
  micro-recall at 1/2/4 faults — lines overlap.
- C6 ablation (neural reranker, gpt-5 judge, N=15): raw 87.1% vs EFCA 90–93%; McNemar p ≥ 0.6.
- Large-context run (in flight): EFCA holds 100% while raw drops on multi-fault at ≥85k.

The latency and token panels of the original figure reproduce the data **exactly**;
only the accuracy panel was altered. Corrected figure: `eval/exp4_corrected.png`
(regenerate with `eval/plot_corrected_exp4.py`).

## The three real contributions (in claim order)

1. **~50× (≈98%) prompt reduction at no accuracy cost.** 726/815/988 tokens (distilled)
   vs 38.3k/39.2k/42.2k (raw), same localization accuracy. Reproduced across all runs.
2. **Which evidence, not how much.** C6 ablation: naive top-k retrieval *loses* 10–13 pp;
   compression that preserves decisive SIGNAL lines recovers/exceeds raw. Defends the
   architecture against "you just used less context."
3. ~~**Robustness to context scale**~~ — **NOT SUPPORTED; scratched.** The large-context
   run (N=8/tier, gpt-5 judge) found NO context-scale degradation of raw and NO overflow:
   micro-recall raw 83.3/72.7/90.5% vs EFCA 100/77.3/90.5% at 85k/160k/250k — the gap
   *shrinks* with context (+17/+5/0 pp) and ties at 250k; even 239k-token prompts fit and
   answer. The positional study shows no lost-in-the-middle penalty (first 85.7% <
   middle/last 90.5%). Do NOT claim robustness-of-accuracy or lost-in-the-middle from this.
   **What it DOES add (fold into leg 1):** EFCA sustains equal-or-better accuracy at
   ~872 tokens vs 82k–239k raw — a token multiple that *grows* with context (≈100× at 85k
   → ≈275× at 250k). That is a stronger *efficiency* statement, not a new leg.

---

## Draft — reframed Abstract sentence (replaces the +14pp sentence)

> The Evidence-First pipeline reduces the diagnostic prompt by roughly 98% (≈0.8k vs
> ≈40k tokens) while **matching** the localization accuracy of full collect-all context,
> and it sustains that accuracy as raw context grows to 250k tokens — where the token
> saving reaches ~275× — shifting the cost of grounded RCA without sacrificing quality.

## Draft — Exp-4 paragraph (replaces current Exp-4 text)

> **Exp. 4 (context assembly).** We compare raw collect-all context against the
> Evidence-First pipeline on the same incidents across increasing difficulty (1, 2, and 4
> concurrent faults). The two conditions attain **comparable localization accuracy**
> (raw 100/100/72% vs distilled 100/98/75% micro-recall; differences within overlapping
> 95% Wilson intervals and not significant under McNemar, p ≥ 0.6 at this N), at
> **statistically indistinguishable latency** — while the pipeline reduces prompt size
> from ≈38–42k to ≈0.7–1.0k tokens, a ~50× reduction (Fig.~\ref{fig:exp4}). An ablation
> (Table~\ref{tab:ablation}) shows the reduction is not simply "less context": naive
> top-$k$ retrieval alone *lowers* accuracy by 10–13 pp, and it is task-aware compression
> preserving decisive signal lines that recovers it. Thus the pipeline's benefit is
> selecting *which* evidence reaches the model, not *how much*. [+ robustness sentence
> from the large-context run once complete.]

## Draft — context-scaling sentence (efficiency framing; robustness claim was scratched)

> Across raw contexts of ≈85k–250k tokens the pipeline attains equal-or-better localization
> accuracy at a constant ≈0.8k-token prompt (Fig.~\ref{fig:largectx}); the token saving
> grows from ≈100× to ≈275× as context scales, since the distilled prompt is size-invariant.

Do NOT write a lost-in-the-middle / "collect-all breaks" sentence — the data contradicts it
(no overflow to 239k; positional first 85.7% < middle/last 90.5%).

## Paste-ready — full Abstract (replaces the current one)

Plain prose (adapt to your abstract's surrounding sentences):

> Large language models can localize and explain incidents in Kubernetes clusters, but
> feeding them the full collect-all diagnostic context is expensive and does not improve
> the answer. We present Evidence-First Context Assembly (EFCA), a retrieval and
> compression pipeline that selects, reranks, compresses, and edge-places only the evidence
> a diagnosis needs before it reaches the model. Across concurrent-fault incidents on a live
> Kubernetes testbed, EFCA **matches** the localization accuracy of full collect-all context
> while reducing the diagnostic prompt by roughly **98%** (from ≈40k to ≈0.8k tokens); on
> raw contexts up to 250k tokens the saving grows to ~275× at unchanged accuracy. An
> ablation shows the reduction is not merely "less context": naive top-$k$ retrieval alone
> *lowers* accuracy, and it is task-aware compression preserving decisive signal lines that
> recovers it — so the benefit comes from selecting *which* evidence reaches the model, not
> *how much*. EFCA is embedded in an end-to-end agentic fault-management system that produces
> operator-approved remediation plans, whose actions we validate by execution against live
> faults.

Minimal-edit alternative (if you only want to swap the one bad sentence): replace
*"...improves localization accuracy under concurrent faults by up to 14 percentage points
while reducing prompt size by roughly 98%."* with:

> ...**matches** the localization accuracy of full collect-all context while reducing prompt
> size by roughly 98% (≈0.8k vs ≈40k tokens), a saving that grows to ~275× on 250k-token
> contexts.

## Paste-ready — Introduction contributions list (LaTeX)

```latex
\noindent\textbf{Contributions.} This paper makes the following contributions:
\begin{itemize}
  \item \textbf{Evidence-First Context Assembly (EFCA):} a five-stage pipeline
  (top-$k$ retrieval, task-aware neural reranking, compression, edge placement, and
  confidence-gated on-demand expansion) that assembles a minimal, decisive context for
  LLM-based root-cause analysis in Kubernetes.
  \item \textbf{Parity at $\sim$50--275$\times$ lower prompt cost:} EFCA matches the
  localization accuracy of full collect-all context (differences within overlapping 95\%
  Wilson intervals; not significant under McNemar) while cutting the prompt from
  $\approx$40k to $\approx$0.8k tokens, a saving that grows with raw context size.
  \item \textbf{Which-evidence-not-how-much:} an ablation showing naive top-$k$ retrieval
  \emph{reduces} accuracy by 10--13\,pp and that task-aware compression preserving decisive
  signal lines recovers it---isolating \emph{selection}, not context volume, as the source
  of grounded RCA.
  \item \textbf{End-to-end agentic fault management:} EFCA is integrated in a system that
  emits operator-approved remediation plans, evaluated by \emph{execution} against live
  injected faults (recovery within a fixed timeout) rather than subjective judgment.
\end{itemize}
```

Notes:
- The bullets deliberately state parity, not a gain — matches the corrected figure, C6, C9.
- Bullet 2 numbers: "50$\times$" is the headline (suite/C6, ~40k$\to$0.8k); "275$\times$"
  is the large-context tail (239k$\to$0.87k). Keep both only if you include Fig.~largectx;
  otherwise drop "--275" and say "$\sim$50$\times$".
- Bullet 4 is the C4 execution-based remediation contribution (keep only if you report it).

## Sections that need the same edit

- Abstract: drop "up to 14 percentage points"; keep "~98%".
- Intro contributions list: reword the accuracy bullet to parity + efficiency + robustness.
- Fig. (Exp-4): replace with `exp4_corrected.png`; caption x-axis is 1/2/4 faults.
- Table IV / +4/+8/+14: remove as a paired-accuracy claim (see C9 doc).
- Related work / conclusion: any "more accurate than collect-all" → "matches at ~50× less".
