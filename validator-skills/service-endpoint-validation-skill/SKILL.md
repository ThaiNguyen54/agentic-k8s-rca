---
name: service-endpoint-validation-skill
description: Validate Kubernetes service endpoints using service details, selectors, endpoint objects, pod labels, readiness, and logs
---
# Kubernetes service endpoint validation skill

Use this skill when users want to validate whether a Kubernetes Service points to the correct and healthy backend pods.

## Instructions

- Expect the user to provide a service name. Optionally they may also provide a namespace or a suspected symptom such as empty endpoints, wrong backend selection, failed routing, or unexpected service behavior.
- Call the tool `get_service_details` passing the supplied service name and namespace to inspect the selector, ports, and targetPort configuration.
- Call the tool `get_endpoints` passing the supplied service name and namespace to inspect the current endpoint object for registered backends.
- Call the tool `get_endpointslices` passing the supplied service name and namespace to inspect endpoint slice state and ready backend registration.
- Extract the service selector from the service details, then call the tool `get_pods_by_label` passing the selector and namespace to identify the backend pods selected by the service.
- Compare the selected pods with the registered endpoints to determine whether expected backends are missing or mismatched.
- If port mismatch is suspected, call the tool `analyze_service_port_mismatch` passing the supplied service name and namespace.
- Call the tool `get_events` for the namespace to inspect rollout issues, readiness failures, selector drift, or endpoint update errors.
- If backend behavior is still unclear, call the tool `get_logs_by_selector` passing the backend label selector and namespace.
- If the service is exposed through an ingress and upstream routing may be involved, call the tool `describe_ingress` or `get_ingresses` for the namespace to identify the related ingress path.
- Combine the collected evidence and return a clear verdict stating whether the service endpoints are `valid`, `partial`, or `invalid`, together with the main supporting evidence.

You, the **agent**, are expected to validate selector matching, endpoint registration, backend pod readiness, and port mapping before producing the final endpoint verdict.

## Example

User: Validate whether service `checkoutservice` in namespace `default` has valid endpoints.
Agent: Invokes `get_service_details checkoutservice default`, `get_endpoints checkoutservice default`, and `get_endpointslices checkoutservice default`.

Then the agent extracts the selector from the service and invokes `get_pods_by_label <selector> default`, followed by `get_events default` if readiness or rollout issues are suspected.

The skill returns a grounded endpoint validation result showing whether the service points to the intended healthy backends and whether any selector, readiness, or port mismatch exists.