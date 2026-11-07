# Fig. 10 live rerun, fixed before the first run (2026-11-07)

Spec: `FIG10_LIVE_RERUN_SPEC.md` (Section V-A2 / Fig. 10).

## Why
The RQ1 rerun (`../rq1_clean/`) found scenario descriptions in the original RQ1
prompts. Fig. 10 uses the same nine fault configurations and its plain baseline
localized 88.3% of single faults, against 3.3% for the leak-free plain LLM. It
is rerun live under the same leak-free rules.

**Question:** on the nine configurations, how do the complete system and a plain
LLM compare in localization accuracy, strict root-cause accuracy and diagnosis
time as the number of concurrent faults grows from one to three?

The result is reported whatever it is.

## Setup

| Item | Setting |
|---|---|
| Cluster | edge2 (Kubernetes v1.32.8): edge2-control-plane-vvhsl (control plane), edge2-md-0-4whsm-xcq92 (single worker); namespace `onlineboutique` |
| Management | kagent on the `kubernetes` context; agents reached over A2A via port-forward 8083 |
| Healthy baseline | `baseline/deployments.json`, `baseline/services.json` (12 + 12 objects, captured 2026-11-07, checked free of every injected value) |
| Configurations | the nine of `../rq1_clean/gold.py`, injected by `faults.py` |
| Proposed | the Orchestrator from `webhook-receiver/` at commit 3740da9, built and run locally (port 9094): context collection → EFCA distillation (evidence API, neural reranker) → RCA agent `k8s-agent` (live MCP tools) → Validation agent `validator` with skills `node-health-validation-skill:v1`, `k8s-pod-validation-skill:v3`, `k8s-node-validation-skill:v4` |
| Plain | one call with the RQ1 plain template: fixed user complaint + not-running / not-ready rows of `kubectl get pods`, captured live; no tools, no skill |
| Model | `gpt-oss-120b` (lab vLLM 0.31.0) for every LLM call; temperature 1.0; max 8,000 output tokens. Plain: seed = run index. Agents: no per-run seed (a kagent ModelConfig holds one static setting); left unset |
| Runs | 10 per configuration and condition = 90 injections, 180 runs |
| Order | the 90 (configuration, run) pairs shuffled with `random.Random(20261107)` |
| Pairing | plain and proposed share one injection. Both start at the same instant, `SETTLE_S` = 60 s after injection completes, and run concurrently |
| Timeout | 600 s from injection completion. A timeout is incorrect, with diagnosis time 600 s |

## Leak-free rules
- **Alert.** The alert sent to the Orchestrator is the same for every
  configuration:
  - labels: `cluster=edge2`, `namespace=onlineboutique`,
    `fault_type=ApplicationDegraded`, empty `fault_object` and
    `root_cause_hint`;
  - summary: the plain condition's user complaint;
  - detail: the same unhealthy-pod rows the plain condition gets;
  - incident id: a random UUID.

  The labels feed the collector, the EFCA reranker hint and the validator
  prompt, so none of them names a fault.
- **Agent prompts** (`agents/`) contain no fault list, count or scenario hint.
  The skill files are listed with their hashes in `agents/skills.txt`.
- **Per-run leak scan.** The plain prompt, the alert payload, and the RCA and
  validator prompts as logged by the Orchestrator are scanned for:
  - scenario ids, fault-count wording and fault categories;
  - "expected root";
  - the README fault and expected-root-cause lines.

  A run with a hit is invalid and rerun after the cause is fixed. The count is
  reported. Evidence-borne strings (`bad-currency`, `v9.9.9-nonexistent`, …)
  are cluster state and allowed.

## Metrics (`score_fig10.py`)
The text scored is:
- **plain:** the answer;
- **proposed:** the RCA agent's analysis (`rca.humanResponse`) returned by the
  Orchestrator after validation. The validator's approve/reject verdict is
  recorded and reported, and does not change the score.

Scoring is done inside the "Primary root cause(s)" section, using the RQ1
section rule (rubric v3). The metrics are:
- **Localization:** every scored fault's component pattern from `gold.py` is
  named.
- **Strict:** every scored fault's component and cause patterns match within
  300 characters (RQ1 rubric v2/v3).
- **Diagnosis time:** final answer returned minus injection completion, for
  every run including wrong ones. Tool calls and skill invocation are also
  recorded.

Errors and timeouts count as incorrect. A pipeline failure caused by the
infrastructure (agent unreachable, HTTP connection error before any model
output) is logged and the injection rerun. A model output the Orchestrator
cannot parse is incorrect.

## Analysis
- Accuracy per configuration over its 10 runs.
- **Primary comparison:** on each of the 9 configurations, proposed is better,
  equal or worse than plain, with an exact two-sided sign test (separately for
  localization and strict).
- Per fault count: mean ± SD of per-configuration accuracy, and mean diagnosis
  time.
- Wilson intervals over runs are descriptive only.
- Judge cross-check: Qwen3.5-27B with the RQ1 judge prompt
  (`judge_fig10.py`), reporting agreement and Cohen's κ.

## Scored faults and deviations from the original scenarios
- **17 of 18 faults are scored.** 3-1's third fault (network isolation) has no
  recorded target. It is **not injected and not scored**, so 3-1 runs as a
  two-fault configuration inside the three-fault group.
- **2-1 "node drain"** is cordon + delete of the `onlineboutique` pods. A full
  drain would evict Longhorn, Argo CD and Velero pods on the single worker, and
  is blocked by a Longhorn PDB.
- **2-2** is cordon, then the bad image. The image fault stays masked because
  its new pod cannot schedule, as in the README.
- **The validator's skills are reattached** (they were removed for the E5
  no-skill condition) so that the validation skill can run.

## Amendments after the smoke test, before the full run (2026-11-07)
The smoke test (1-1 and 3-2, one injection each, kept in `smoke/` and **not**
used in any result) found three problems with the procedure. No metric, rubric,
run count or timeout changed.

1. **The agents' MCP tools had no kubeconfig.** The shared `k8s-multi-cluster`
   server has none. As in E5, a copy, `k8s-multi-cluster-live`, with the
   existing `multi-cluster-kubeconfig` secret now serves `k8s-agent` and
   `validator` (`setup_mcp_live.sh`; undone by `restore_cluster.sh`).
2. **Kagent runtime 500s are infrastructure failures.** The validator finished
   its model calls, then kagent's runner failed while closing its MCP toolsets
   ("Attempted to exit a cancel scope …") and returned HTTP 500.
   - Such errors (HTTP 500, connection errors) mark the attempt as an
     infrastructure failure. It is kept under `attempts/` and the injection is
     rerun, up to 3 attempts in total.
   - If all 3 fail, the last attempt is the run and counts as a miss.
   - The number of reruns is reported.
3. **EFCA runs on CPU.** The evidence API has no GPU: distillation took 93 s
   for 1-1 and exceeded the Orchestrator's 300 s limit for 3-2, which then
   used the raw collect-all dump. The abandoned request kept computing for
   about 4 more minutes.
   - Before each injection the harness waits until the evidence API is idle
     (under 0.5 core for 15 s). This wait is outside diagnosis time.
   - EFCA latency counts in diagnosis time. Each run records whether EFCA
     distilled or fell back to the raw dump.
   - Decided with the author: run on this hardware as-is.

Also observed: the RCA agent answered from the embedded evidence without
calling tools in both smoke runs. This is the agent's own behaviour and is
recorded per run.
