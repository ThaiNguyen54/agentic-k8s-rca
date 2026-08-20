# eval/baselines/fix_mesh_rcacopilot.py
"""Re-run only the RCACopilot-style system on the 6 mesh incidents (the first
mesh_llm run predated the gt['category'] fix) and merge into mesh_llm_results.json.
"""
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from baselines.run_mesh_llm import to_bundle, KWS, MECH_TEXT, EVID_DIR, OUT
from baselines import llm_baselines as LB
import judge as judgemod

incidents = []
for f in sorted(os.listdir(EVID_DIR)):
    if not f.endswith(".json"):
        continue
    ev = json.load(open(os.path.join(EVID_DIR, f)))
    b = to_bundle(ev)
    gt = {"primary": [(ev["culprit"], KWS.get(ev["mechanism"], []))],
          "distractors": ["frontend", "loadgenerator"], "category": "Performance",
          "fault_type": ev["mechanism"], "gold_text": MECH_TEXT[ev["mechanism"]].format(c=ev["culprit"])}
    incidents.append((ev["name"], b, gt))
corpus = [(n, b, gt) for n, b, gt in incidents]

results = json.load(open(OUT))
for name, bundle, gt in incidents:
    print(f"rcacopilot {name} ...", flush=True)
    try:
        r = LB.run_rcacopilot(name, bundle, corpus, k=2)
        jr = judgemod.judge(r["text"], gt)
        newrow = {"incident": name, "system": "rcacopilot", "culprit": gt["primary"][0][0],
                  "latency_s": r["latency_s"], "prompt_tokens": r["prompt_tokens"],
                  "recall": jr["judge_recall"], "precision": jr["judge_precision"],
                  "tp": jr["judge_tp"], "hit": jr["judge_recall"] >= 0.999, "response": r["text"]}
        print(f"   hit={newrow['hit']} recall={jr['judge_recall']} tok={r['prompt_tokens']}")
    except Exception as e:
        newrow = {"incident": name, "system": "rcacopilot", "culprit": gt["primary"][0][0],
                  "error": str(e)[:200], "recall": None}
        print(f"   ! FAILED: {str(e)[:120]}")
    # replace any existing rcacopilot row for this incident
    results = [r for r in results if not (r["incident"] == name and r["system"] == "rcacopilot")]
    results.append(newrow)
    json.dump(results, open(OUT, "w"), indent=2)
print("merged rcacopilot rows into", OUT)
