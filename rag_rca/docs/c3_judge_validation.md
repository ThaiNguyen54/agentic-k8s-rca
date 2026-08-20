# C3 — LLM-Judge Protocol + Human Validation

Addresses: LLM-as-judge lacked reported setup, human verification, and control for
self-evaluation bias (a model judging its own output).

## 1. Judge protocol (report this explicitly)

| Item | Setting |
|---|---|
| Judge model | **gpt-5** (`rca-judge` agent) — distinct from the judged RCA model (**gpt-5-mini**) |
| Self-bias control | judge ≠ generator; the judge never scores its own model's output |
| Rubric | strict JSON verdict: per gold cause `is_primary` true/false + `misattributed` distractors; judges **conclusions only**, not incidental mentions (system message in `rca-judge` agent) |
| Blinding | judge sees only GROUND_TRUTH + CANDIDATE text; no system/mode identifier |
| Temperature | not pinned in the ModelConfig → provider default (disclose as such; C9 §3b) |
| Output | `{"identified":[{object,is_primary}], "misattributed":[...], "notes":...}` |

**For Exp. 2 (validation-quality judge — faithfulness/specificity/usefulness):** use the
same principle — a **fixed independent judge model** for all three backends (GPT-4o /
Llama / Qwen), so e.g. GPT-4o's validation output is never judged by GPT-4o. Report the
judge model, rubric (Fig. of the rubric), and temperature.

## 2. Human validation of the judge (tooling ready)

Two scripts operationalize the human study:

- `annotation_sheet.py` — samples N responses, **blinds** them (strips system/mode,
  shuffles), and writes a CSV for ≥2 annotators; the judge's verdict is stored in a
  separate key file so it cannot bias the annotator. Verified: 30 blind cases generated
  from the 20-incident runs.
- `agreement.py` — computes **human-vs-judge** and **human-vs-human** agreement +
  **Cohen's κ** (no extra deps).

Protocol to run:
1. `python annotation_sheet.py --in results_20_llm.json --n 30` → `annotation_blind.csv`.
2. Two independent annotators fill `human_correct_0_1` (and 1–5 faithfulness/specificity/
   usefulness if validating the Exp. 2 judge), blind to system.
3. `python agreement.py --csv annotation_blind.csv --key annotation_key.json --cols ann1 ann2`
   → report agreement % and κ (human–judge and human–human).

Interpretation to include: κ > 0.6 (substantial) supports using the LLM judge as a
proxy; report the human–human κ too, so the judge is held to the same bar as the humans.

## 3. Second-judge cross-check — DONE (κ = 0.955)

A **second independent judge** (`rca-judge-2`, **gpt-5-mini**) re-scored all 80
20-incident candidate responses under the identical blind rubric; `judge_agreement.py`
computes Cohen's κ vs the primary **gpt-5** judge on the per-item primary-hit decision.

| Metric | Value |
|---|---|
| n (paired candidates) | 80 |
| Raw agreement | **98.8%** (79/80) |
| **Cohen's κ** | **0.955** (almost-perfect) |
| Disagreements | 1 (`edge1-oomkilled-memory-001`, yours_raw) |
| Positive rate | gpt-5 82.5% vs gpt-5-mini 83.8% |
| Per-system agreement | yours_distilled 20/20, rcacopilot 20/20, rcagent 20/20, yours_raw 19/20 |

**Interpretation:** at κ=0.955 the judge verdicts are essentially model-agnostic — the
reported accuracies are not a gpt-5 idiosyncrasy. This is the strongest available cross-judge
evidence and complements (does not replace) the human study in §2.

**Honest limitation (state it):** the two judges are the same *family* (GPT-5 / GPT-5-mini).
A cross-*family* judge (o4-mini, Llama-3.3-70B, DeepSeek-R1) was attempted but the OpenAI
account returned `insufficient_quota` for those models and the local Ollama endpoint was
unreachable — so only the GPT-5 family was available. Disclose this; do not claim
cross-family robustness. (Regenerate: `python eval/judge_agreement.py`.)

## 4. Honest limitation to state
If only one human annotator is available, say so explicitly (single-annotator limitation)
rather than implying consensus. The tooling supports two columns precisely so agreement
can be reported when a second annotator exists.
