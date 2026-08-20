# C8 — Validation-Skill Library (extensibility evidence)

Addresses: "extensible expert-authored skills" claimed but only node-health shown.
The artifact already ships **five** skills spanning node, pod, service, endpoint,
and network faults (`thesis-master/validator-skills/`). Document the library and
demonstrate a second skill end-to-end.

## Skill-library table (LaTeX)

```latex
\begin{table}[!t]
\caption{Validation-Skill Library}
\label{tab:skills}
\centering
\small
\begin{tabular}{p{2.9cm} p{2.0cm} p{2.6cm}}
\hline
\textbf{Skill} & \textbf{Fault scope} & \textbf{Representative tools} \\
\hline
node-health & node NotReady / degraded & get\_node\_ready\_condition, get\_memory\_signals, get\_process\_snapshot, host iptables \\
k8s-node & node / host-network & verify\_node\_state, get\_node\_condition\_history, get\_node\_events \\
k8s-pod & pod / workload & get\_pods\_by\_label, get\_logs\_by\_selector, get\_events, describe\_ingress \\
service-endpoint & service routing & get\_service\_details, get\_endpoints, analyze\_service\_port\_mismatch \\
network-connectivity & ingress / L4--L7 path & analyze\_ingress\_502, get\_endpointslices, host\_get\_iptables\_spec \\
\hline
\end{tabular}
\end{table}
```

## Prose (append to §III-F)

> The validation-skill layer is not limited to node health. The current library
> comprises five expert-authored skills covering distinct fault scopes: **node-health**
> and **k8s-node** for infrastructure and host-network faults, **k8s-pod** for
> workload-scoped faults, and **service-endpoint** and **network-connectivity** for
> service-routing and ingress-path faults (Table~\ref{tab:skills}). Each skill follows
> the same contract — treat the RCA packet as a claim, gather fresh live evidence via
> MCP tools, and return a grounded verdict — but selects a different tool set and
> branching logic appropriate to its scope. New skills are added by authoring a single
> \texttt{SKILL.md} with a scope description and tool sequence, without changing the
> agent, which is what makes the layer extensible.

## Second-skill demonstration (eval — run after C6, needs kagent)

Mirror the node-health trace (Fig. 10) with the **network-connectivity** skill on a
service-routing fault. Candidates already in hand:
- `edge1-service-selector-mismatch-001` / `edge1-service-port-mapping-mismatch-001`
  (20-incident dataset) — endpoints empty / wrong targetPort → the skill's
  `analyze_service_port_mismatch` + `get_endpoints` path.
- The mesh-bench 502 path (`ingress-502.log`, `analyze_ingress_502`).

Plan: feed the routing incident to the Validation Agent with the
network-connectivity skill, capture the tool-call trace + verdict, and present it as a
second worked example (Listing + workflow figure) alongside node-health — demonstrating
the framework generalizes across fault classes.

Status: skills exist and are documented here; the end-to-end eval run is queued behind
the C6 ablation (both need the kagent Validation Agent; running them serially avoids
gpt-5-mini rate-limit contention).
```

## ⚠️ Infra blocker found (2026-07): live skill tools have no kubeconfig

Attempting the live second-skill trace surfaced a regression: the `k8s-multi-cluster`
MCPServer pod (which backs all skill tools — `get_endpoints`, `get_endpointslices`,
`analyze_service_port_mismatch`, `describe_ingress`, …) has **no kubeconfig mounted**.
Every skill-tool call returns `Invalid kube-config file. No configuration found.`
(verified via `list_contexts` → `ok:false`). The pod mounts only a configMap
(`k8s-multi-cluster` → `/config/local.yaml`); the kubeconfig Secrets exist on the cluster
(`edge1-kubeconfig`, `multicluster-kubeconfig`) but are not wired into the pod.

Consequence: **a live tool-call trace cannot be captured right now** for node-health *or*
network-connectivity — the agents currently reason over fed evidence only. Two paths:

1. **Fix the mount (enables a real live trace, strongest evidence).** Wire a kubeconfig
   Secret into the `k8s-multi-cluster` deployment at the path agentgateway expects, restart,
   re-test `list_contexts`. This also upgrades C4 to a *live-investigation* demo.
   **Security note:** this mounts multi-cluster credentials into an in-cluster pod — the
   shared-credential concern flagged earlier; get explicit sign-off first.
2. **Evidence-fed worked example (no live trace).** Feed the captured routing-fault evidence
   already in the repo (`endpoints-live.yaml`, `endpointslices-live.yaml`, `ingress-502.log`)
   to the validator running the network-connectivity skill's logic, and present its grounded
   verdict + the tool sequence it would run. Weaker (no live tool output) but honest and
   consistent with how the rest of the eval feeds evidence.

**Decision needed.** Until then C8's second-skill *demo* is not produced; the skill-library
table + prose above stand on the shipped `SKILL.md` files regardless.
