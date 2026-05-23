---
name: k8s-node-validation-skill
description: Validate node-level Kubernetes RCA hypotheses using live evidence from node state, events, condition history, and host networking tools. Never trust the RCA packet as proof.
---

# Kubernetes node validation skill

Use this skill when:
- the fault object is a node,
- the incident is infrastructure-related,
- the RCA packet proposes a node-level or host-network cause,
- the system must decide whether remediation or failover is justified.

## Core rule

The RCA packet is a claim, not evidence.

- Never trust `hypothesis` as proof.
- Never trust `evidenceSummary` as proof.
- You must collect fresh live evidence with tools before making a decision.
- If required live evidence cannot be collected, return `reject`.

## Available tools

- verify_node_state
- get_node_events
- get_related_events
- get_node_condition_history
- host_get_iptables_spec
- host_get_iptables_counters
- search_rca_chunks

## Required validation workflow

1. Read the task context and identify:
   - cluster
   - node name
   - incident type
   - RCA packet fields

2. If cluster or node name is missing:
   - return `reject`
   - reason must say `missing cluster or node context for live validation`

3. Before any decision, you MUST call these tools:
   - `verify_node_state`
   - `get_node_condition_history`
   - one of `get_node_events` or `get_related_events`

4. If the hypothesis mentions any of the following:
   - API server connectivity loss
   - network blocking
   - firewall
   - iptables
   - dropped packets
   - control-plane traffic blocked

   then you MUST also call:
   - `host_get_iptables_spec`
   - `host_get_iptables_counters`

5. You may call `search_rca_chunks` only as supporting context after live checks.
   - Never approve based on historical similarity alone.

## Decision rules

Approve only when:
- live node state supports the claim,
- live events or condition history support the claim,
- and host networking evidence supports the claim when the RCA mentions network blocking or iptables.

Reject when:
- any required tool call was not performed,
- live evidence is missing,
- live evidence does not support the hypothesis,
- the RCA mixes multiple causes without narrowing,
- confidence is too high for the live evidence,
- or `safeToFailover=true` is not justified by live evidence.

## Remediation output rules

After making the decision:
- If the hypothesis is approved, return a remediation plan based only on validated evidence.
- Recommend actions in priority order, starting with the least risky action that could resolve or contain the issue.
- Prefer reversible, scoped, and well-understood actions before disruptive ones.
- Include verification steps to confirm the expected recovery signal after each important action.
- If the evidence is insufficient for a safe fix, recommend further diagnostics or containment instead of speculative remediation.
- Set `requiresHumanApproval=true` for actions with meaningful operational risk, blast radius, or user impact.

Remediation guidance principles:
- Be specific about the intended outcome of each action.
- Keep actions minimal and relevant to the validated condition.
- Distinguish between corrective steps, containment steps, and verification steps.
- Do not recommend destructive or high-blast-radius actions unless clearly justified by evidence.
- Favor plans that allow rollback or controlled escalation.

## Output rules

Return ONLY valid JSON in this exact structure:
{
  "decision": "approve|reject",
  "confidence": 0.0,
  "reason": "<short explanation that mentions which live tools were used>",
  "safeToFailover": true
}