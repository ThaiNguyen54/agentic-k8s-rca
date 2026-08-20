# C4 — Execution-Based Remediation Correctness

Addresses: "Correct Remediation" (Exp. 3, Table IV) lacked a defined ground truth
and scoring method (risk of subjective single-annotator judgment).

## Definition (operational, not subjective)

> A remediation is **correct** iff, when its action is applied to the live faulted
> workload, the target returns to a healthy state within a **fixed 150 s timeout**.
> Health is measured objectively per fault class (see grading table below).
> We also report **time-to-recover** (seconds from applying the action to health).

`strategy: Recreate` is used so an injected fault actually degrades the service (a
rolling update would keep the old healthy pod serving and mask the fault).

## Relationship to C1 (must state — avoids apparent contradiction)

> **This execution is an *offline evaluation* step.** To score a remediation we apply
> its action in a throwaway namespace and observe recovery. The production loop is
> unchanged and still stops at an operator-approved `RemediationPlan` (per C1) — nothing
> auto-executes in production. Execution here exists only to *grade* the plan.

Add one sentence to this effect in Exp. 3 so C1 and C4 are consistent.

## Per-fault grading paths (Table IV footnote)

| Fault class | Objective health criterion | Grading path |
|---|---|---|
| Workload (zero-replicas, bad image, probe, OOM) | `readyReplicas ≥ desired` | execution-grounded |
| Service-routing (selector, port mismatch) | service has ≥1 ready endpoint | execution-grounded |
| Node / scheduling (cordon, unschedulable) | pinned pod schedules & becomes Ready | execution-grounded |
| **Destructive infra** (CNI/Multus failure, node network isolation) | — | **annotator fallback** (see below) |

**Table IV footnote text:** *"A remediation is counted correct iff applying its action
restores the target to health (readyReplicas ≥ desired for workload faults; ≥1 ready
endpoint for routing faults; pinned pod Ready for scheduling faults) within 150 s;
destructive-infra faults not safely executable on the two-node testbed are graded by two
independent annotators (κ reported)."*

## Harness validation (`recovery_harness.py`) — 5/5

Self-contained scenarios (throwaway ns `rca-recovery-test`): create healthy → inject →
confirm broken → apply corrective action → poll recovery.

| Scenario | Class | Broke | Recovered | Time-to-recover | Correct |
|---|---|---|---|---|---|
| deployment-zero-replicas | workload | yes | yes | 5.2 s | ✓ |
| incorrect-image-reference | workload | yes | yes | 10.3 s | ✓ |
| readiness-probe-incorrect-port | workload | yes | yes | 5.2 s | ✓ |
| service-selector-mismatch | routing | yes | yes | 0.1 s | ✓ |
| node-unschedulable-cordon | node/sched | yes | yes | 46.0 s | ✓ |

**Note on the 0.1 s (pre-empt the reviewer's question):** fixing the Service selector
re-points it to pods that are *already Ready*, so endpoints repopulate on the next sync
(sub-second) — no pod restart is needed. Contrast the node case (46 s), where uncordoning
must reschedule, pull the image, and pass readiness.

## Answer to the reviewer's safety question (infra remediations)

- **Safe & reversible → executed:** node **cordon/uncordon** (does *not* evict running
  pods; only blocks new scheduling). Added and validated above.
- **Destructive on a 2-node live cluster → annotator fallback:** **CNI/Multus restart** and
  **node network isolation** can partition the cluster / break pod networking with no safe
  in-place revert on this testbed. These are graded by ≥2 annotators with κ, and the
  limitation is disclosed. Node *drain* is reversible but evicts all workloads on a 2-node
  cluster, so it is also routed to the annotator path unless run on an isolated node.

## Table IV number — from the system's own emitted plans (DONE, 5/5)

The remaining gap is now closed. `system_plan_recovery.py` runs the **actual pipeline**
(RCA agent `k8s-agent` gpt-5-mini → validator agent gpt-5-mini → `remediationPlan`) for each
live fault, then **executes the plan's proposed action** and measures recovery within the
150 s timeout. Faulted-state evidence is captured (kubectl) and fed to the agents as context,
consistent with the rest of the paper's methodology (the agents' MCP tools have no live edge1
kubeconfig — see C8 infra note). The corrective action is mapped to a bounded, reversible
kubectl op scoped to the throwaway namespace; anything outside that set is "not executable"
→ scored incorrect (never runs arbitrary commands).

| Fault | Expected action | Plan proposed it? | Executed → recovered | Time |
|---|---|---|---|---|
| deployment-zero-replicas | scale up | ✓ | ✓ | 5.2 s |
| incorrect-image-reference | fix image | ✓ | ✓ | 10.3 s |
| readiness-probe-incorrect-port | fix probe | ✓ | ✓ | 5.2 s |
| service-selector-mismatch | fix selector | ✓ | ✓ | 0.1 s |
| node-unschedulable-cordon | uncordon | ✓ | ✓ | 10.3 s |

**Table IV "Correct Remediation" = 5/5 (100%), mean time-to-recover ≈ 6.2 s.** Every
system-generated plan named the correct corrective action *and* executing it restored health.
Small N (5 fault classes) — report as such; this is the execution-grounded number, replacing
the prior manual score.

**Honest scope note:** the five faults are the safely-executable classes on the 2-node
testbed. Destructive-infra faults (CNI/Multus, node network isolation) remain annotator-graded
(κ) per the footnote above. So Table IV is "5/5 on executable fault classes; N infra faults
annotator-graded."

## Regenerate (system-plan number)
`KUBECONFIG=... python eval/baselines/system_plan_recovery.py` → `system_plan_results.json`.

## Regenerate
`KUBECONFIG=... python eval/baselines/recovery_harness.py` → `recovery_results.json`.
