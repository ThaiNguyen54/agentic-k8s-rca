---
name: node-health-validation-skill
description: Validate Kubernetes node health using node state, condition history, events, and host-level evidence
---
# Kubernetes node health validation skill

Use this skill when users want to validate whether a Kubernetes node is healthy after an incident, node failure, scheduling problem, or workload eviction.

## Instructions

- Expect the user to provide a target node name. Optionally they may also provide a suspected symptom such as `NotReady`, pod eviction, scheduling failure, memory pressure, or degraded connectivity.
- Call the tool `verify_node_state` passing the supplied node name to confirm the current Ready state and timestamps.
- Call the tool `get_node_ready_condition` passing the supplied node name to inspect the latest Ready condition details.
- Call the tool `get_node_condition_history` passing the supplied node name to review recent condition changes and instability history.
- Call the tool `get_node_events` passing the supplied node name to retrieve recent node-specific events.
- Call the tool `get_related_events` passing the supplied node name to collect additional recent events mentioning the node.
- If workload impact needs to be checked, call the tool `get_recent_pod_events` for affected namespaces or workloads to identify pod eviction, restart, or rescheduling signals.
- If the symptom suggests memory pressure, call the tool `get_memory_signals` passing the supplied node name and an appropriate time window around the incident.
- If the symptom suggests host resource exhaustion, call the tool `get_process_snapshot` passing the supplied node name and a suitable row limit.
- If the symptom suggests host-level networking or packet filtering problems, first call `check_tooling` to verify host evidence commands are available, then call `host_get_iptables_spec` and `host_get_iptables_counters` passing the supplied node name.
- Combine the collected evidence and return a clear verdict stating whether the node is `healthy`, `degraded`, or `unhealthy`, together with the main supporting evidence.

You, the **agent**, are expected to validate the current node state, review recent history and events, inspect workload impact when necessary, and use host-level evidence when cluster-level evidence is insufficient.

## Example

User: Validate whether node `worker-2` is healthy after the incident.
Agent: Invokes `verify_node_state worker-2`, `get_node_ready_condition worker-2`, `get_node_condition_history worker-2`, `get_node_events worker-2`, and `get_related_events worker-2`.

If memory pressure is suspected, the agent additionally calls `get_memory_signals worker-2 <since> <until>` and `get_process_snapshot worker-2 10`.

The skill returns a grounded node-health verdict based on current node state, recent condition history, events, and host-level evidence when needed.