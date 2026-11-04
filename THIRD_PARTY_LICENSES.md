# Third-party licenses

This project is licensed under the Apache License 2.0 (see [LICENSE](LICENSE)). None of the components below are copied into this repository. They are installed separately (pip, uv, Go modules, container images or Hugging Face downloads) and keep their own licenses.

## Python dependencies

| Package | Used by | License |
|---|---|---|
| pydantic | rag_rca, MCP servers | MIT |
| PyYAML | rag_rca, MCP servers | MIT |
| sentence-transformers | rag_rca | Apache-2.0 |
| transformers | rag_rca (via sentence-transformers) | Apache-2.0 |
| PyTorch | rag_rca (via sentence-transformers) | BSD-3-Clause |
| qdrant-client | rag_rca | Apache-2.0 |
| FastAPI | rag_rca evidence service | MIT |
| Uvicorn | rag_rca evidence service | BSD-3-Clause |
| FastMCP | MCP servers | Apache-2.0 |
| kubernetes (Python client) | MCP servers | Apache-2.0 |
| python-dotenv | MCP servers | BSD-3-Clause |

## Go dependencies

| Module | Used by | License |
|---|---|---|
| sigs.k8s.io/controller-runtime | multi-cluster-fault-controller, remediationplan-controller | Apache-2.0 |
| k8s.io/client-go, k8s.io/api, k8s.io/apimachinery | Go controllers | Apache-2.0 |
| github.com/go-logr/logr | multi-cluster-fault-controller | Apache-2.0 |
| github.com/onsi/ginkgo/v2, github.com/onsi/gomega | Go test suites | MIT |
| Kubebuilder scaffolding | webhook-receiver, multi-cluster-fault-controller | Apache-2.0 |

## Models

| Model | Use | License |
|---|---|---|
| BAAI/bge-m3 | embeddings | MIT |
| BAAI/bge-reranker-v2-m3 | cross-encoder reranker | Apache-2.0 |

LLM backends (OpenAI models, or self-hosted open-weight models) are accessed via API and are subject to their providers' terms.

## Platforms and evaluation tools (deployed, not distributed)

| Software | Use | License |
|---|---|---|
| Kagent, KMCP | agent and MCP server runtime | Apache-2.0 |
| Kubernetes | management and workload clusters | Apache-2.0 |
| Online Boutique (GoogleCloudPlatform/microservices-demo) | workload under observation | Apache-2.0 |
| Istio | service-mesh benchmark | Apache-2.0 |
| Chaos Mesh, LitmusChaos | fault injection | Apache-2.0 |
| uv | Python packaging for the MCP servers | Apache-2.0 / MIT |
