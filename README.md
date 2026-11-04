# Agentic Kubernetes Root Cause Analysis

**An agent-based fault-management system that takes a Kubernetes incident from raw runtime evidence → root-cause diagnosis → procedure-validated verdict → an executable, cluster-native remediation plan — automatically.**

Kubernetes faults cross many interacting layers (containers, pods, services, nodes, network, control plane), so a symptom at one layer often originates at another. Diagnosing them by hand means correlating heterogeneous, fast-changing telemetry under time pressure. This project automates that path end to end, and does it in a way that stays *grounded* (evidence comes from the live cluster, not a hand-pasted prompt) and *verifiable* (an independent stage checks each hypothesis before any action is proposed).

> This repository accompanies the paper *"An Evidence-Grounded Agent Framework for Kubernetes Fault Diagnosis and Remediation Planning"* (N. D. T. Thai, P. B. Ta, Y. Kim; under review at IEEE Access, 2026).

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

Evaluated on a Kubernetes testbed (one management cluster, two workload clusters) running the Online Boutique microservice demo. Unless stated otherwise, the RCA model is `gpt-5-mini` and the judge is `gpt-5`. Most diagnosis benchmarks use seeded synthetic incidents; the recovery test runs on the live cluster.

- **Grounding works.** Live cross-layer context improved root-cause accuracy by 23–41 percentage points across three LLM backends (GPT-4o, Llama-3.3-70B-Instruct, Qwen2.5-72B).
- **EFCA is non-inferior to collect-all context at ~2% of the prompt.** On 60 seeded incidents × 3 runs, the strict-hit difference was −1.7 pp, with a one-sided 95% Newcombe lower bound of −9.2 pp (margin 10 pp). Mean prompt size was 810 vs 38,867 tokens.
- **The SIGNAL rules matter.** Removing them lowered micro-recall from 86.5 ± 2.7% to 76.8 ± 2.7% (15 incidents × 5 runs).
- **Known limitation.** On 30 incidents from seven fault types outside the SIGNAL vocabulary (one exploratory run, no cause hint), EFCA found all faults in 4/30 incidents vs 29/30 for collect-all. For fault types EFCA does not cover, use collect-all context.
- **Validation skills.** With the procedure-guided skill, the Validation Agent returned a correct, well-formed verdict on 22/24 labeled hypotheses, vs 4/23 without it (strict scoring).
- **Live recovery.** Across 8 fault classes × 3 repetitions, the system recovered 18/23 runs, and 18/18 whenever it produced a remediation plan. Median time to recover was 5.2 s.
- **Remediation controller.** The `RemediationPlan` controller passes 6/6 envtest lifecycle tests (approval, hold, capability block, dry-run → execute, failure, auto-approval).
- **Baselines.** On the 6-culprit service-mesh benchmark, MicroRCA placed the culprit first in 2/6 cases (top-3: 5/6), while the LLM-based systems, including EFCA, found all 6.

Statistics use Wilson score intervals, exact McNemar tests, Newcombe paired intervals and paired bootstrap. Pre-registrations, scripts and raw results are in [`rag_rca/eval/`](rag_rca/eval/).

## Repository layout

| Path | What it is | Stack |
|---|---|---|
| [`rag_rca/`](rag_rca/) | Core EFCA + RCA pipeline: retrieval, reranking, compression, ordering, and the full evaluation/ablation suite | Python (Qdrant, BGE-m3, cross-encoder, FastAPI) |
| [`agent-mcp-server/`](agent-mcp-server/) | MCP servers exposing cluster / RAG / failover tools to the agents | Python |
| [`validator-skills/`](validator-skills/) | Procedure-guided validation skills (node health, pod, service-endpoint, network connectivity) | Markdown skills + containers |
| [`webhook-receiver/`](webhook-receiver/) | Fault-detection webhook / trigger that notifies the Orchestrator | Go (Kubebuilder) |
| [`multi-cluster-fault-controller/`](multi-cluster-fault-controller/) | Controller for multi-cluster fault handling / remediation resources | Go (Kubebuilder) |
| [`remediationplan-controller/`](remediationplan-controller/) | `RemediationPlan` CRD (`rca.system/v1`) and controller that hands approved plans to the transition operator | Go (controller-runtime) |
| [`rag_rca/docs/`](rag_rca/docs/) | Design notes: EFCA implementation, ablation, baselines, judge validation, reproducibility | Markdown |

## Installation

### Prerequisites

| Requirement | Version used |
|---|---|
| Management cluster (hosts agents and MCP servers) | Kubernetes v1.28.9, Ubuntu 22.04, 3 nodes × 16 vCPU / 32 GB |
| Workload cluster(s) (system under observation) | Kubernetes v1.32.8, Ubuntu 22.04 |
| [Kagent](https://github.com/kagent-dev/kagent) / [KMCP](https://github.com/kagent-dev/kmcp) | 0.7.12 / 0.2.2 |
| Python | 3.10+ for `rag_rca`, 3.11 for the MCP servers (images use `python:3.11-slim`) |
| [uv](https://docs.astral.sh/uv/) | for the MCP servers |
| Go | 1.22+ (`webhook-receiver`), 1.24+ (`remediationplan-controller`), 1.25+ (`multi-cluster-fault-controller`) |
| `kubectl`, Docker | for building images and applying manifests |
| LLM endpoint | any OpenAI-compatible API (the paper uses `gpt-5-mini` and `gpt-5`) |

### 1. EFCA pipeline (`rag_rca`)

```bash
cd rag_rca
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt      # pydantic, pyyaml, sentence-transformers, qdrant-client, fastapi, uvicorn
pip install pytest

# Unit tests (run on CPU; no cluster or LLM needed)
python -m pytest -q tests

# Optional: evidence HTTP service used by the agents
uvicorn app.service.evidence_api:app --host 0.0.0.0 --port 8100
curl http://127.0.0.1:8100/healthz
```

On first use, `sentence-transformers` downloads the models `BAAI/bge-m3` and `BAAI/bge-reranker-v2-m3` from Hugging Face. When the reranker weights or a GPU are unavailable, the reranker falls back to a lexical + signal scorer, so the pipeline and tests still run. The results reported in the paper use the neural reranker; `/healthz` reports `reranker_model_loaded`.

### 2. MCP servers (`agent-mcp-server/`)

| Server | Tools |
|---|---|
| `k8s-multi-cluster` | read-only, multi-cluster: pods, services, endpoints, events, logs, ingress, port-mismatch / ingress-502 analysis |
| `failover-mcp-server` | ClusterPolicy schema and the transition (failover) API, dry-run or live |
| `rag-mcp-server` | evidence retrieval |
| `my-mcp-server` | additional cluster tools |

Each server is a FastMCP project managed with `uv` and `kmcp`.

**Run locally:**
```bash
cd agent-mcp-server/k8s-multi-cluster      # or failover-mcp-server, rag-mcp-server, my-mcp-server
cp .env.example .env 2>/dev/null || true  # fill in values; .env is git-ignored
uv sync
uv run python src/main.py --transport http --host 0.0.0.0 --port 8080
```

**Deploy to the management cluster with KMCP:**
```bash
# k8s-multi-cluster reads the workload-cluster kubeconfig from a secret (never commit kubeconfigs)
kubectl -n kagent create secret generic multi-cluster-kubeconfig \
  --from-literal=KUBECONFIG_B64="$(base64 -w0 /path/to/multi-cluster.kubeconfig)"
# host-level checks also need an SSH key secret
kubectl -n kagent create secret generic host-ssh-key --from-file=agentic-ai.pem=/path/to/key.pem

kmcp build --verbose          # builds the image from the Dockerfile
kmcp deploy mcp --apply       # creates the MCPServer resource from kmcp.yaml
```

Set `FASTMCP_CHECK_FOR_UPDATES=off` and `FASTMCP_SHOW_CLI_BANNER=false` in the server environment. Otherwise startup can exceed Kagent's 5 s MCP session timeout.

### 3. Agents and validation skills

1. Create a model configuration for your LLM endpoint (examples in [`agent-mcp-server/resources/models/`](agent-mcp-server/resources/models/)).
2. Apply the agent resources, for example the Validation Agent in [`agent-mcp-server/resources/agents/declarative/validator.yaml`](agent-mcp-server/resources/agents/declarative/validator.yaml).
3. Build and push the skill images in [`validator-skills/`](validator-skills/), then reference them from the Validation Agent (`node-health-validation-skill`, `k8s-pod-validation-skill`, `k8s-node-validation-skill`, …).
4. Agents are reachable over A2A through the Kagent controller:

```bash
kubectl -n kagent port-forward svc/kagent-controller 8083:8083
export RCA_AGENT_URL="http://127.0.0.1:8083/api/a2a/kagent/k8s-agent/"
export VALIDATOR_AGENT_URL="http://127.0.0.1:8083/api/a2a/kagent/validator/"
export WEBHOOK_URL="http://127.0.0.1:9093/webhook/fault"
```

### 4. Fault detection and remediation controllers

```bash
cd webhook-receiver && go run ./cmd/main.go                   # receives fault notifications
cd multi-cluster-fault-controller && go run ./cmd/main.go     # multi-cluster fault handling

cd remediationplan-controller
kubectl apply -f config/crd/rca.system_remediationplans.yaml
go run ./cmd --transition-url http://<transition-operator>:8080/transition
```

See [`remediationplan-controller/README.md`](remediationplan-controller/README.md) for the envtest suite.

### 5. Reproducing the evaluation

Scripts, pre-registrations and saved results are in [`rag_rca/eval/`](rag_rca/eval/); design notes are in [`rag_rca/docs/`](rag_rca/docs/) (see `c9_reproducibility.md`). The LLM-based runs call the Kagent agents above and need an API key with credit; the scoring scripts (`rag_rca/eval/baselines/aggregate_*.py`) work offline on the saved JSON.

## Tech stack

`Kubernetes` · `LLM agents` · `Model Context Protocol (MCP)` · `Retrieval-Augmented Generation` · `Qdrant` · `BAAI/bge-m3` + `bge-reranker-v2-m3` · `Kagent` · `Go / Kubebuilder` · `Python / FastAPI` · `Chaos Mesh` · `LitmusChaos`

## License

Licensed under the [Apache License 2.0](LICENSE). See [NOTICE](NOTICE) for attribution and [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md) for the licenses of dependencies, models and platforms.

## Author

**Nguyen Duc Thong Thai** — M.S., School of Electronic Engineering, Soongsil University (Distributed Cloud & Network Lab).
Research interests: cloud-native infrastructure, AIOps, LLM agents, and self-healing systems.
