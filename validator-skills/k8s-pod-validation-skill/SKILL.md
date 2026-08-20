---
name: k8s-pod-validation-skill
description: Validate pod-level Kubernetes RCA hypotheses using live workload, event, log, service, and traffic-path evidence. Never trust the RCA packet as proof.
---

# Kubernetes pod validation skill

Use this skill when:
- the fault object is a pod, deployment, replica set, or workload,
- the incident is namespace-scoped or workload-scoped,
- the RCA packet proposes a pod-level, service-level, or ingress-path cause,
- the system must decide whether remediation is justified.

## Core rule

The RCA packet is a claim, not evidence.

- Never trust `hypothesis` as proof.
- Never trust `evidenceSummary` as proof.
- You must collect fresh live evidence with tools before making a decision.
- If required live evidence cannot be collected, return `reject`.

## Available tools

- get_pods_by_label
- get_recent_pod_events
- get_events
- get_related_events
- get_logs_by_selector
- get_service_details
- get_endpoints
- get_endpointslices
- get_ingresses
- describe_ingress
- search_rca_chunks

## Required validation workflow

1. Read the task context and identify:
   - cluster
   - namespace
   - workload or label selector
   - incident type
   - RCA packet fields

2. If namespace or workload context is missing:
   - return `reject`
   - reason must say `missing namespace or workload context for live validation`

3. Before any decision, you MUST call these tools:
   - `get_pods_by_label`
   - one of `get_recent_pod_events` or `get_events`

4. If the hypothesis mentions:
   - crash
   - CrashLoopBackOff
   - readiness
   - liveness
   - probe
   - connectivity
   - runtime error
   - application failure
   - image pull failure, ImagePullBackOff, ErrImagePull, or an invalid image tag
   - OOMKilled, out-of-memory termination, exit code 137, or an exceeded memory limit

   then you MUST also call:
   - `get_logs_by_selector`

   For image-pull and OOMKilled hypotheses the decisive evidence is the container
   state and termination reason, so also confirm the reported reason in the pod
   events or pod status before deciding; never approve an image-pull or OOM claim
   from the hypothesis text alone.

5. If the hypothesis mentions service routing, backend failure, or service connectivity:
   - you MUST call `get_service_details`
   - you MUST call `get_endpoints`
   - you MUST call `get_endpointslices`

6. If the hypothesis mentions ingress, external access, or north-south traffic:
   - you MUST call `get_ingresses`
   - you MUST call `describe_ingress`

7. You may call `search_rca_chunks` only as supporting context after live checks.
   - Never approve based on historical similarity alone.

## Decision rules

Approve only when:
- live pod/workload evidence supports the claim,
- logs and events align with the claim when runtime behavior is involved,
- service or ingress evidence supports the claim when traffic path is involved.

Reject when:
- any required tool call was not performed,
- live evidence is missing,
- live evidence does not support the hypothesis,
- the RCA does not distinguish pod failure from service or ingress failure,
- confidence is too high for the live evidence,
- or `safeToFailover=true` is not justified by live evidence.

## Output rules

Return ONLY valid JSON in this exact structure:
{
  "decision": "approve|reject",
  "confidence": 0.0,
  "reason": "<short explanation that mentions which live tools were used>",
  "safeToFailover": true
}