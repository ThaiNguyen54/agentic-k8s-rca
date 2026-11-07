#!/usr/bin/env bash
# eval/fig10_live/restore_cluster.sh — undo setup_cluster.sh and setup_mcp_live.sh:
# put k8s-agent and the validator back on their original model config and MCP
# server (backup/agent-*.orig.yaml, taken 2026-11-07) and delete the live MCP
# server. The validator's skills stay attached (they were missing only since E5).
set -euo pipefail
export KUBECONFIG="${KUBECONFIG:-$HOME/multi-cluster.kubeconfig}"
K="kubectl --context kubernetes -n kagent"
HERE="$(cd "$(dirname "$0")" && pwd)"
for a in k8s-agent validator; do
  $K get agent "$a" -o json | python3 -c '
import json,sys,re
d=json.load(sys.stdin)
orig=open(sys.argv[1]).read()
d["spec"]["declarative"]["modelConfig"]=re.search(r"modelConfig: (\S+)",orig).group(1)
for t in d["spec"]["declarative"]["tools"]:
    m=t.get("mcpServer") or {}
    if m.get("name")=="k8s-multi-cluster-live": m["name"]="k8s-multi-cluster"
for k in ("resourceVersion","uid","generation","creationTimestamp","managedFields"):
    d["metadata"].pop(k,None)
d.pop("status",None)
print(json.dumps(d))' "$HERE/backup/agent-$a.orig.yaml" | $K apply -f -
done
$K delete mcpserver k8s-multi-cluster-live --ignore-not-found
$K get agent k8s-agent validator
echo "restored; ModelConfig gpt-oss-120b and secret aap-gpt-oss left in place"
