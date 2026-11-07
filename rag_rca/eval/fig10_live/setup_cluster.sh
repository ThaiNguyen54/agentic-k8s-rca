#!/usr/bin/env bash
# eval/fig10_live/setup_cluster.sh — point k8s-agent and the validator at
# gpt-oss-120b on the lab vLLM server and reattach the validator's skills.
# Backups of the original resources are in backup/ (restore_cluster.sh).
#
#   AAP_BASE_URL=... AAP_API_KEY=... bash fig10_live/setup_cluster.sh
set -euo pipefail
: "${AAP_API_KEY:?set AAP_API_KEY}"
: "${AAP_BASE_URL:?set AAP_BASE_URL (OpenAI-compatible /v1 base URL)}"
export KUBECONFIG="${KUBECONFIG:-$HOME/multi-cluster.kubeconfig}"
K="kubectl --context kubernetes -n kagent"
HERE="$(cd "$(dirname "$0")" && pwd)"

$K create secret generic aap-gpt-oss --from-literal=OPENAI_API_KEY="$AAP_API_KEY" \
  --dry-run=client -o yaml | $K apply -f -

$K apply -f - <<EOF
apiVersion: kagent.dev/v1alpha2
kind: ModelConfig
metadata:
  name: gpt-oss-120b
  namespace: kagent
spec:
  provider: OpenAI
  model: gpt-oss-120b
  apiKeySecret: aap-gpt-oss
  apiKeySecretKey: OPENAI_API_KEY
  openAI:
    baseUrl: ${AAP_BASE_URL}
    temperature: "1.0"
    maxTokens: 8000
  tls:
    disableVerify: true
EOF

$K patch agent k8s-agent --type merge -p '{"spec":{"declarative":{"modelConfig":"gpt-oss-120b"}}}'
$K patch agent validator --type merge -p '{"spec":{"declarative":{"modelConfig":"gpt-oss-120b"},
  "skills":{"refs":["docker.io/thainguyen13099/node-health-validation-skill:v1",
                    "docker.io/thainguyen13099/k8s-pod-validation-skill:v3",
                    "docker.io/thainguyen13099/k8s-node-validation-skill:v4"]}}}'

for a in k8s-agent validator; do
  $K rollout status deploy/$a --timeout=300s
done
$K get agent k8s-agent validator
echo "setup done"
