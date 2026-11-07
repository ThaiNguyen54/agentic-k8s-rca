#!/usr/bin/env bash
# eval/fig10_live/setup_mcp_live.sh — give the two experiment agents MCP tools
# that can reach the clusters, without touching the shared MCPServer.
#
# The MCPServer `k8s-multi-cluster` has no kubeconfig, so every tool call fails
# ("kube-config appears empty or invalid"). As in E5 (e6_runs/setup_live_validator.sh):
#   1. create `k8s-multi-cluster-live`: same spec + secretRefs to the existing
#      secret `multi-cluster-kubeconfig`;
#   2. point the cluster tools of `k8s-agent` and `validator` at it.
# Undo: restore_cluster.sh (points both agents back and deletes the live server).
set -euo pipefail
export KUBECONFIG="${KUBECONFIG:-$HOME/multi-cluster.kubeconfig}"
K="kubectl --context kubernetes -n kagent"

$K get mcpserver k8s-multi-cluster -o json | python3 -c '
import json,sys
d=json.load(sys.stdin)
spec=d["spec"]; spec["deployment"]["secretRefs"]=[{"name":"multi-cluster-kubeconfig"}]
print(json.dumps({"apiVersion":d["apiVersion"],"kind":d["kind"],
  "metadata":{"name":"k8s-multi-cluster-live","namespace":"kagent"},"spec":spec}))' | $K apply -f -

for a in k8s-agent validator; do
  $K get agent "$a" -o json | python3 -c '
import json,sys
d=json.load(sys.stdin)
for t in d["spec"]["declarative"]["tools"]:
    m=t.get("mcpServer") or {}
    if m.get("name")=="k8s-multi-cluster": m["name"]="k8s-multi-cluster-live"
for k in ("resourceVersion","uid","generation","creationTimestamp","managedFields"):
    d["metadata"].pop(k,None)
d.pop("status",None)
print(json.dumps(d))' | $K apply -f -
done

sleep 10
$K rollout status deploy/k8s-multi-cluster-live --timeout=180s
for a in k8s-agent validator; do $K rollout status deploy/$a --timeout=300s; done
$K get mcpserver k8s-multi-cluster-live
$K get agent k8s-agent validator
echo "mcp live setup done"
