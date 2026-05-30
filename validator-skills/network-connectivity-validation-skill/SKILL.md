---
name: network-connectivity-validation-skill
description: Validate Kubernetes network connectivity using ingress, service, endpoints, logs, events, and host-level packet evidence
---
# Kubernetes network connectivity validation skill

Use this skill when users want to validate whether traffic can flow correctly between workloads, services, or ingress paths in a Kubernetes cluster.

## Instructions

- Expect the user to provide a destination service name, and optionally a namespace, ingress name, source workload, or suspected symptom such as timeout, refused connection, DNS failure, or 502 Bad Gateway.
- If the issue involves ingress traffic, call the tool `describe_ingress` passing the ingress name and namespace to inspect backend routing configuration.
- If the issue is a likely ingress 502 case, call the tool `analyze_ingress_502` passing the ingress name and namespace to correlate ingress backend mapping, service targetPort, endpoints, selected pods, and ingress controller logs.
- Call the tool `get_service_details` passing the supplied service name and namespace to inspect the service selector, ports, and targetPort.
- Call the tool `get_endpoints` passing the supplied service name and namespace to inspect the registered service endpoints.
- Call the tool `get_endpointslices` passing the supplied service name and namespace to inspect endpoint slice state and backend registration.
- Call the tool `get_pods_by_label` passing the service selector and namespace to confirm that the selected backend pods exist and are ready.
- If service port mismatch is suspected, call the tool `analyze_service_port_mismatch` passing the supplied service name and namespace.
- Call the tool `get_events` for the namespace to retrieve recent routing, readiness, or backend-related events.
- If backend or controller failure is suspected, call the tool `get_logs_by_selector` passing an appropriate label selector and namespace to inspect logs from backend pods or ingress controller pods.
- If host-level packet filtering or forwarding is suspected, first call `check_tooling` for the relevant node, then call `host_get_iptables_spec` and `host_get_iptables_counters`.
- Combine the collected evidence and return a clear verdict stating whether connectivity is `reachable`, `partially blocked`, or `blocked`, together with the main supporting evidence.

You, the **agent**, are expected to validate the ingress path, service path, backend endpoint state, and host-level packet evidence when necessary before producing the final connectivity verdict.

## Example

User: Validate connectivity to service `paymentservice` in namespace `default` for a suspected 502 issue on ingress `shop-ingress`.
Agent: Invokes `describe_ingress shop-ingress default`, `analyze_ingress_502 shop-ingress default`, `get_service_details paymentservice default`, `get_endpoints paymentservice default`, and `get_endpointslices paymentservice default`.

If backend readiness or controller behavior is unclear, the agent additionally calls `get_pods_by_label <selector> default`, `get_events default`, and `get_logs_by_selector <selector> default`.

The skill returns a grounded connectivity verdict showing whether the fault is caused by ingress routing, service configuration, missing endpoints, backend readiness, or host-level traffic filtering.