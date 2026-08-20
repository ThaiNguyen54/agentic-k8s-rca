# eval/plot_largectx.py
"""Large-context sweep figure: accuracy + tokens vs raw context size.
Honest read: EFCA >= raw at every tier, no overflow; token saving grows with context.
  python plot_largectx.py   # -> largectx_sweep.png
"""
import json
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

RAW_C, EF_C = "#ff7f0e", "#1f77b4"
TIERS = ["~85k", "~160k", "~250k"]


def micro(rs):
    tp = sum(r["tp"] for r in rs); f = sum(r["faults"] for r in rs)
    return 100.0 * tp / f if f else 0.0


def main():
    d = json.load(open("efca_largectx_results.json"))
    sz = [r for r in d if r["kind"] == "size"]
    by = defaultdict(list)
    for r in sz:
        by[(r["size"], r["config"])].append(r)
    x = list(range(len(TIERS)))
    acc = {c: [micro(by[(t, c)]) for t in TIERS] for c in ("raw", "efca")}
    tok = {c: [sum(r["tokens"] for r in by[(t, c)]) / len(by[(t, c)]) for t in TIERS]
           for c in ("raw", "efca")}

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4.2))

    def line(ax, ys, color, label, fmt, dy):
        ax.plot(x, ys, "o-", color=color, label=label, lw=2, ms=7)
        for xi, y in zip(x, ys):
            ax.annotate(fmt.format(y), (xi, y), textcoords="offset points",
                        xytext=(0, dy), ha="center", color=color, fontsize=10, fontweight="bold")

    line(a1, acc["raw"], RAW_C, "Collect-all (raw)", "{:.0f}%", -14)
    line(a1, acc["efca"], EF_C, "Evidence-First (EFCA)", "{:.0f}%", 8)
    a1.set_title("Localization accuracy vs raw context size")
    a1.set_ylabel("% root causes found (micro-recall)")
    a1.set_ylim(0, 108); a1.legend(loc="lower left", fontsize=9)

    line(a2, tok["raw"], RAW_C, "Collect-all (raw)", "{:.0f}", 8)
    line(a2, tok["efca"], EF_C, "Evidence-First (EFCA)", "{:.0f}", -14)
    a2.set_yscale("log"); a2.set_title("Prompt context (log) — saving grows with scale")
    a2.set_ylabel("tokens (log scale)"); a2.legend(loc="center left", fontsize=9)

    for ax in (a1, a2):
        ax.set_xticks(x); ax.set_xticklabels(TIERS); ax.grid(True, alpha=0.3)
        ax.set_xlabel("raw context size")
    fig.tight_layout(); fig.savefig("largectx_sweep.png", dpi=150, bbox_inches="tight")
    print("saved largectx_sweep.png")
    print("acc raw ", [round(v, 1) for v in acc["raw"]], " efca", [round(v, 1) for v in acc["efca"]])
    print("tok raw ", [round(v) for v in tok["raw"]], " efca", [round(v) for v in tok["efca"]])


if __name__ == "__main__":
    main()
