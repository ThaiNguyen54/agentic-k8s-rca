# eval/baselines/mesh_faults.py
"""MicroRCA-appropriate fault set for the Online Boutique mesh bench.

Each fault is injected INTO a Boutique service via Chaos-Mesh and manifests as a
service-graph response-time anomaly whose true culprit service is known. This is
MicroRCA's home turf (performance/latency faults propagating through a call
graph) - unlike the 20 admission/config faults, which it structurally can't see.

Culprits span different call-graph depths (leaf high-fan-in -> deep single-caller)
and two mechanisms (network latency, CPU stress) matching the paper's taxonomy.
"""

NS = "boutique"

# (name, culprit_service, chaos_kind, chaos_spec_body, mechanism)
FAULTS = [
    # --- network-latency faults (guaranteed response-time anomaly) ---
    dict(name="net-delay-productcatalog", culprit="productcatalogservice",
         mechanism="network_latency", kind="NetworkChaos",
         spec=dict(action="delay", mode="all",
                   selector={"namespaces": [NS], "labelSelectors": {"app": "productcatalogservice"}},
                   delay={"latency": "250ms", "correlation": "50", "jitter": "20ms"})),
    dict(name="net-delay-currency", culprit="currencyservice",
         mechanism="network_latency", kind="NetworkChaos",
         spec=dict(action="delay", mode="all",
                   selector={"namespaces": [NS], "labelSelectors": {"app": "currencyservice"}},
                   delay={"latency": "250ms", "correlation": "50", "jitter": "20ms"})),
    dict(name="net-delay-shipping", culprit="shippingservice",
         mechanism="network_latency", kind="NetworkChaos",
         spec=dict(action="delay", mode="all",
                   selector={"namespaces": [NS], "labelSelectors": {"app": "shippingservice"}},
                   delay={"latency": "200ms", "correlation": "50", "jitter": "20ms"})),
    dict(name="net-delay-payment", culprit="paymentservice",
         mechanism="network_latency", kind="NetworkChaos",
         spec=dict(action="delay", mode="all",
                   selector={"namespaces": [NS], "labelSelectors": {"app": "paymentservice"}},
                   delay={"latency": "200ms", "correlation": "50", "jitter": "20ms"})),
    # --- CPU-stress faults (resource anomaly; paper's CPU-hog class) ---
    dict(name="cpu-stress-recommendation", culprit="recommendationservice",
         mechanism="cpu_stress", kind="StressChaos",
         spec=dict(mode="all",
                   selector={"namespaces": [NS], "labelSelectors": {"app": "recommendationservice"}},
                   stressors={"cpu": {"workers": 2, "load": 100}})),
    dict(name="cpu-stress-cartservice", culprit="cartservice",
         mechanism="cpu_stress", kind="StressChaos",
         spec=dict(mode="all",
                   selector={"namespaces": [NS], "labelSelectors": {"app": "cartservice"}},
                   stressors={"cpu": {"workers": 2, "load": 100}})),
]


def manifest(fault):
    """Build the Chaos-Mesh CR dict for a fault."""
    return {
        "apiVersion": "chaos-mesh.org/v1alpha1",
        "kind": fault["kind"],
        "metadata": {"name": fault["name"], "namespace": NS},
        "spec": fault["spec"],
    }
