# eval/baselines/aggregate.py
"""Aggregate the four comparison result files into one summary for charting:

  mesh_bench.json        - MicroRCA (faithful) on the 6 mesh faults
  mesh_llm_results.json  - LLM systems on the same 6 mesh faults
  results_20_llm.json    - LLM systems on the 20 real incidents
  results_20_microrca.json - degraded MicroRCA (coverage) on the 20

Prints a summary and writes eval/comparison_summary.json.
"""
import json
import os

EVAL = os.path.dirname(os.path.dirname(__file__))


def _load(name):
    p = os.path.join(EVAL, name)
    return json.load(open(p)) if os.path.isfile(p) else None


def _acc(rows, key="hit"):
    rows = [r for r in rows if r.get("recall") is not None] if any("recall" in r for r in rows) else rows
    ok = [r for r in rows if r.get(key)]
    return (100.0 * len(ok) / len(rows)) if rows else float("nan"), len(rows)


def summarize():
    S = {}

    # ---- mesh bench ----
    mrca = _load("mesh_bench.json")
    if mrca:
        n = len(mrca)
        S["mesh_microrca"] = {
            "top1": round(100 * sum(r["hit@1"] for r in mrca) / n, 1),
            "top3": round(100 * sum(r["hit@3"] for r in mrca) / n, 1), "n": n}
    mll = _load("mesh_llm_results.json")
    if mll:
        S["mesh_llm"] = {}
        for sysn in sorted(set(r["system"] for r in mll)):
            rows = [r for r in mll if r["system"] == sysn and r.get("recall") is not None]
            if rows:
                S["mesh_llm"][sysn] = {
                    "acc": round(100 * sum(r["hit"] for r in rows) / len(rows), 1),
                    "tok": round(sum(r["prompt_tokens"] for r in rows) / len(rows)),
                    "lat": round(sum(r["latency_s"] for r in rows) / len(rows), 1),
                    "n": len(rows)}

    # ---- 20 real incidents (LLM systems) ----
    r20 = _load("results_20_llm.json")
    if r20:
        S["real20_llm"] = {}
        for sysn in sorted(set(r["system"] for r in r20)):
            rows = [r for r in r20 if r["system"] == sysn and r.get("recall") is not None]
            if rows:
                S["real20_llm"][sysn] = {
                    "acc": round(100 * sum(r["hit"] for r in rows) / len(rows), 1),
                    "prec": round(100 * sum(r["precision"] for r in rows) / len(rows), 1),
                    "tok": round(sum(r["prompt_tokens"] for r in rows) / len(rows)),
                    "lat": round(sum(r["latency_s"] for r in rows) / len(rows), 1),
                    "n": len(rows), "fails": sum(1 for r in r20 if r["system"] == sysn and r.get("recall") is None)}
        # by-category accuracy for yours_distilled
        cats = sorted(set(r["category"] for r in r20))
        S["real20_by_category"] = {}
        for c in cats:
            S["real20_by_category"][c] = {}
            for sysn in sorted(set(r["system"] for r in r20)):
                rows = [r for r in r20 if r["system"] == sysn and r["category"] == c and r.get("recall") is not None]
                if rows:
                    S["real20_by_category"][c][sysn] = round(100 * sum(r["hit"] for r in rows) / len(rows), 1)

    # ---- 20 real incidents (degraded MicroRCA coverage) ----
    m20 = _load("results_20_microrca.json")
    if m20:
        n = len(m20)
        S["real20_microrca"] = {
            "top1": round(100 * sum(bool(r.get("hit@1")) for r in m20) / n, 1),
            "top3": round(100 * sum(bool(r.get("hit@3")) for r in m20) / n, 1),
            "live": sum(1 for r in m20 if r.get("live")),
            "structural_miss": sum(1 for r in m20 if not r.get("live")),
            "with_signal": sum(1 for r in m20 if r.get("signal")), "n": n}

    json.dump(S, open(os.path.join(EVAL, "comparison_summary.json"), "w"), indent=2)
    print(json.dumps(S, indent=2))
    return S


if __name__ == "__main__":
    summarize()
