#!/usr/bin/env bash
# eval/fig10_live/restore_cluster.sh — put k8s-agent and the validator back on
# their original model config (backup/agent-*.orig.yaml, taken 2026-11-07).
# The validator's skills stay attached (they were missing only since E5).
set -euo pipefail
export KUBECONFIG="${KUBECONFIG:-$HOME/multi-cluster.kubeconfig}"
K="kubectl --context kubernetes -n kagent"
HERE="$(cd "$(dirname "$0")" && pwd)"
for a in k8s-agent validator; do
  mc=$(grep -m1 'modelConfig:' "$HERE/backup/agent-$a.orig.yaml" | awk '{print $2}')
  $K patch agent "$a" --type merge -p "{\"spec\":{\"declarative\":{\"modelConfig\":\"$mc\"}}}"
done
$K get agent k8s-agent validator
echo "restored model configs; ModelConfig gpt-oss-120b and secret aap-gpt-oss left in place"
