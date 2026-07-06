#!/usr/bin/env bash
set -euo pipefail

NS="${1:-default}"
PATTERN="${2:-video}"

mapfile -t RESOURCES < <(kubectl get checkpoints -n "$NS" -o name | grep -E "$PATTERN" || true)

if [[ ${#RESOURCES[@]} -eq 0 ]]; then
  echo "No checkpoints matched '$PATTERN' in ns '$NS'."
  exit 0
fi

for R in "${RESOURCES[@]}"; do
  echo "==> $NS/$R"

  kubectl get "$R" -n "$NS" -o jsonpath='{.metadata.finalizers}'; echo
  kubectl patch "$R" -n "$NS" --type=merge -p '{"metadata":{"finalizers":[]}}' >/dev/null
  kubectl delete "$R" -n "$NS" --wait=true
done
