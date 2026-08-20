# Agentic Kubernetes Root Cause Analysis

**An agent-based fault-management system that takes a Kubernetes incident from raw runtime evidence → root-cause diagnosis → procedure-validated verdict → an executable, cluster-native remediation plan — automatically.**

Kubernetes faults cross many interacting layers (containers, pods, services, nodes, network, control plane), so a symptom at one layer often originates at another. Diagnosing them by hand means correlating heterogeneous, fast-changing telemetry under time pressure. This project automates that path end to end, and does it in a way that stays *grounded* (evidence comes from the live cluster, not a hand-pasted prompt) and *verifiable* (an independent stage checks each hypothesis before any action is proposed).

> This repository accompanies the paper *"Design and Implementation of an Agent-Based Fault Management System for Kubernetes Clusters"* (submitted to IEEE Access, 2026). The manuscript source is in [`access.tex`](access.tex).

---

## Why this is different

Most existing approaches solve only part of the problem:

| Approach | What it does | What it leaves open |
|---|---|---|
| Graph-based RCA | Localizes a suspect component | Stops at diagnosis; needs a well-maintained topology |
| Prompt-based LLM diagnosis | Explains symptoms in natural language | Reasons only over human-assembled context |
| Tool-using agents | Collect their own evidence | **Judge their own conclusions** → ungrounded, unverifiable |
| Rule/runbook recovery | Executes fixes | Not root-cause-aware; goes stale as the cluster changes |

This system closes all four gaps in a **single workflow**: it correlates cross-layer evidence from the live cluster, validates each hypothesis against expert-authored operational knowledge *before* any action, and emits a Kubernetes-native remediation plan built only from capabilities the cluster actually exposes.

## Two core ideas

**1. Evidence-First Context Assembly (EFCA)** — Instead of stuffing the entire collected cluster state into one prompt (which grows with cluster size, not incident complexity, and buries the decisive signal), EFCA *selects and arranges* the evidence through five stages:

1. **Top-k retrieval** — incident-scoped candidate snippets (pod / namespace / node)
2. **Task-aware reranking** — a cross-encoder reorders by relevance to the fault signature
3. **Signal-preserving compression** — decisive lines kept verbatim, routine noise collapsed into summaries
4. **Edge placement** — strongest evidence positioned where long-context models attend most
5. **Confidence-gated expansion** — the agent pulls the full raw timeline only when its confidence is low

**2. Procedure-guided validation skills** — Reusable, expert-authored modules (e.g. `node-health-validation`) that a Validation Agent executes against the live cluster to confirm or refute each hypothesis — an *independent* check, not the same model re-affirming itself. New skills can be registered without modifying the agent.

## Architecture

```
 Fault Detector ──► Orchestrator (deterministic control loop)
                        │
                        ├─ 1. System Context Collector
                        │     └─ SystemTopology · ServiceEndpoints · CRDRegistry
                        │
                        ├─ 2. Evidence-First Context Assembly (EFCA)
                        │     └─ retrieve → rerank → compress → place → expand
                        │
                        ├─ 3. RCA Agent ──► ranked root-cause hypotheses
                        │     └─ calls MCP tools when evidence is missing
                        │
                        ├─ 4. Validation Agent ──► verdict per hypothesis
                        │     └─ runs procedure-guided validation skills on the live cluster
                        │
                        └─ 5. Capability-Aware Planning
                              └─ emits a Kubernetes RemediationPlan CR (human-approved)
```

Each stage consumes only the structured artifact the previous one produced — a raw signal becomes grounded evidence, then a ranked hypothesis, then an evaluated diagnosis, then a plan — so defects are caught where they arise rather than propagating silently. Agents run in-cluster (deployed with [Kagent](https://github.com/kagent-dev/kagent)) and reach the cluster through a **Model Context Protocol (MCP)** server.

## Key results

Evaluated on a two-cluster testbed running the Online Boutique microservice demo, across three LLM backends (GPT-4o, Llama-3.3-70B, Qwen2.5-72B) and controlled ablations:

- **Grounding works.** Live cross-layer context lifted root-cause accuracy on every backend — e.g. GPT-4o from **60% → 93%**.
- **EFCA matches "collect-all" accuracy at a fraction of the cost.** Same localization accuracy while cutting prompt size by up to **~275× (≈98%)** as telemetry scales from ~85k to ~250k tokens.
- **The win is *selection*, not just a shorter prompt.** A stage-by-stage ablation shows accuracy comes from task-aware compression preserving the decisive signal lines — *which* evidence reaches the model matters more than *how much*.
- **Plans actually work.** System-emitted remediation plans restored health in **5/5** safely-executable fault classes, mean time-to-recover **≈6.2 s**.
- **Beats representative baselines** (RCAgent-style, RCACopilot-style, MicroRCA) on both a real-incident set and a service-mesh localization benchmark.

Results are backed by Wilson score intervals, McNemar's paired test, and a second-judge Cohen's κ cross-check (κ = 0.955) to avoid over-interpreting small samples. Full evaluation harness and data live in [`rag_rca/eval/`](rag_rca/eval/).

## Repository layout

| Path | What it is | Stack |
|---|---|---|
| [`rag_rca/`](rag_rca/) | Core EFCA + RCA pipeline: retrieval, reranking, compression, ordering, and the full evaluation/ablation suite | Python (Qdrant, BGE-m3, cross-encoder, FastAPI) |
| [`agent-mcp-server/`](agent-mcp-server/) | MCP servers exposing cluster / RAG / failover tools to the agents | Python |
| [`validator-skills/`](validator-skills/) | Procedure-guided validation skills (node health, pod, service-endpoint, network connectivity) | Markdown skills + containers |
| [`webhook-receiver/`](webhook-receiver/) | Fault-detection webhook / trigger that notifies the Orchestrator | Go (Kubebuilder) |
| [`multi-cluster-fault-controller/`](multi-cluster-fault-controller/) | Controller for multi-cluster fault handling / remediation resources | Go (Kubebuilder) |
| [`access.tex`](access.tex) | The IEEE Access paper source | LaTeX |
| [`rag_rca/docs/`](rag_rca/docs/) | Design notes: EFCA implementation, ablation, baselines, judge validation, reproducibility | Markdown |

## Getting started (EFCA pipeline)

```bash
# Setup Environment Varibale
export RCA_AGENT_URL="http://127.0.0.1:8083/api/a2a/kagent/k8s-agent/"
export RCA_AGENT_NAME=k8s-agent

export VALIDATOR_AGENT_URL="http://127.0.0.1:8083/api/a2a/kagent/validator/"
export VALIDATOR_AGENT_NAME=validator-agent

export WEBHOOK_URL="http://127.0.0.1:9093/webhook/fault"

# run the webhook-receiver
cd agentic-k8s-rca/webhook-receiver
go run ./cmd/main.go

# run the multi-cluster-fault-controller
cd agentic-k8s-rca/multi-cluster-fault-controller
go run ./cmd/main.go
```

> The cross-encoder reranker falls back to a lexical + signal scorer when a GPU or the model weights aren't available, so the core pipeline and tests run without a GPU.

## Tech stack

`Kubernetes` · `LLM agents` · `Model Context Protocol (MCP)` · `Retrieval-Augmented Generation` · `Qdrant` · `BAAI/bge-m3` + `bge-reranker-v2-m3` · `Kagent` · `Go / Kubebuilder` · `Python / FastAPI` · `Chaos Mesh` · `LitmusChaos`

## Author

**Nguyen Duc Thong Thai** — M.S., School of Electronic Engineering, Soongsil University (Distributed Cloud & Network Lab).
Research interests: cloud-native infrastructure, AIOps, LLM agents, and self-healing systems.
