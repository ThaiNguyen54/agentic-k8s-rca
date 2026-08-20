# C2 — Stronger Baselines (coverage map)

Addresses: evaluation mainly pits plain-LLM vs. the full system, so gains could be
attributed to "more context" rather than the architecture. C2 asks for intermediate
and competitor baselines that isolate each contribution.

## What each requested baseline maps to

| Reviewer-requested baseline | Status | Where it lives |
|---|---|---|
| Raw-context agent (collect-all) | ✅ done | `yours_raw` (run on 20 real + mesh + Exp 4) |
| RAG-only (retrieve, no rerank/compress/place) | ✅ done | EFCA ablation **`topk`** config (C6) |
| Tool-agent **without EFCA** | ✅ done | `yours_raw` = tool-agent on full context |
| Tool-agent **without validation skill** | 🔄 queued (needs kagent) | RCA-agent-only vs RCA+Validation (validation ablation) |
| RCAgent-style (self-consistency) | ✅ done | `rcagent` baseline (mesh + 20) |
| RCACopilot-style (retrieval few-shot) | ✅ done | `rcacopilot` baseline (mesh + 20) |
| K8sGPT / HolmesGPT | ⬜ external tools — deploy | see below |

**Key point for the rebuttal:** the isolation the reviewer wants ("gain from architecture
vs. just more context") is answered directly by the **C6 per-component ablation** —
`raw → topk → rerank → compress → edge → expand` holds the agent/tools fixed and varies
only the context-assembly policy. If accuracy rises across stages at ~1/40th the tokens,
the gain is from *placement/selection*, not volume.

## Results already in hand (LLM baselines, gpt-5 judge)

- **20 real incidents:** yours_raw 90%, yours_distilled 85%, RCAgent 85%, RCACopilot 75%.
- **Mesh bench:** yours_raw / RCACopilot / RCAgent 100%, yours_distilled 50%, MicroRCA
  33% top-1 / 83% top-3.
- **MicroRCA** (graph baseline) faithfully run on the mesh bench and as a degraded
  resource-only variant on the 20 (5% top-1) — the graph-vs-LLM comparison.

## Validation-skill ablation — DONE (RCA-only vs RCA+validation)

`system_plan_recovery.py` records, per fault, whether the corrective action is proposed by
the **RCA agent alone** vs by the **RCA→validator pipeline**:

| | Proposed correct corrective action |
|---|---|
| RCA-only (no validation) | 3/4 valid scenarios |
| RCA + validation | **4/4** valid scenarios |

On `node-unschedulable-cordon`, RCA-alone did not propose the uncordon action; the
validation stage completed it. **Directional evidence that the validation layer adds
correctness over the tool-agent alone** — small N (4 executable scenarios; one hit a
transient agent error this run and is excluded), so report as directional, not a headline.

## Decision (user, 2026-07): lean on existing baselines; do NOT deploy K8sGPT/HolmesGPT

External-tool baselines are **out of scope** for this revision. The baseline set is the five
already run: **raw (collect-all)**, **topk-RAG** (C6), **RCACopilot**, **RCAgent**, and
**MicroRCA** — spanning collect-all, retrieval-only, few-shot-retrieval, self-consistency,
and graph-based methods. State this coverage explicitly and list the omission of external
LLM-ops tools (K8sGPT/HolmesGPT) as a limitation / future work.

## System improvement — neural reranker upgrade (85% → 90% on the 20 real incidents)

The original 20-incident `yours_distilled` used the **lexical** reranker fallback
(`Reranker(use_model=False)`), not the neural cross-encoder the architecture specifies
(C5). The lexical ranker let distractor lines outrank the decisive root-cause line, costing
two incidents vs raw (OOMKilled, PodNetworkDelay). Re-running all 20 with the **neural**
reranker (`bge-reranker-v2-m3`) — an implementation fix aligning the run with the described
pipeline, measured on all 20, not just the failures — gives:

| System | Strict hits | Mean tokens | Notes |
|---|---|---|---|
| **yours_distilled (neural)** | **18/20 = 90%** | 3,684 | ↑ from 85%; fixes OOMKilled misdiagnosis |
| yours_raw (collect-all) | 18/20 = 90% | 11,580 | parity, ~3× more tokens |
| yours_distilled (lexical, old) | 17/20 = 85% | 3,631 | superseded |
| RCAgent | 17/20 = 85% | — | |
| RCACopilot | 15/20 = 75% | — | |

**Honest outcome:** the neural reranker fixes the OOMKilled case (the reranker now scores
`OOMKilled/Exit 137` above the `OCI StartError` distractor), lifting distilled to **parity
with collect-all raw (90%) at ~1/3 the tokens**, ahead of both LLM baselines. **No
regressions** (no incident that lexical got, neural lost). Regenerate:
`python eval/baselines/run_20_neural.py` → `results_20_neural.json`.

**Remaining miss (PodNetworkDelay) — honest limitation:** the neural RCA correctly names the
NetworkChaos + 300 ms delay + frontend pod, but frames the root cause as the chaos "failing
to apply" because the evidence contains 26 `failed to apply` events vs only 2 `Applied`
events, and compression over-weights the larger (noise) cluster. Raw sees every event and
disambiguates. This is an event-sampling artifact of compression on adversarial evidence;
not fixed here (fixing it risks overfitting to one case). Report as a limitation.

## Honest framing note
The intermediate baselines (RAG-only = `topk`, no-EFCA = `raw`) come *free* from the C6
ablation, so C2 and C6 share evidence. State clearly which baseline each table row is, so
a reviewer sees the ladder from plain-LLM → RAG → full EFCA rather than a single leap.
