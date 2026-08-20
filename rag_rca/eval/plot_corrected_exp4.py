# eval/plot_corrected_exp4.py
"""Regenerate the Exp-4 three-panel figure HONESTLY from suite_results.json.

The original figure's latency + token panels reproduce this data exactly; the
accuracy panel did not (it showed a manufactured +4/+8/+14 pp gap). This script
plots all three panels straight from the run so accuracy reads as measured:
parity across difficulty, ~50x fewer tokens.

  python plot_corrected_exp4.py            # -> exp4_corrected.png
"""
import json
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

RAW_C = "#ff7f0e"
DIST_C = "#1f77b4"


def load(path="suite_results.json"):
    d = json.load(open(path))
    agg = defaultdict(lambda: {"tp": 0, "f": 0, "n": 0, "hit": 0, "lat": 0.0, "tok": 0})
    for r in d:
        a = agg[(r["mode"], r["faults"])]
        a["tp"] += r["tp"]; a["f"] += r["faults"]; a["n"] += 1
        a["hit"] += 1 if r["recall"] >= 0.999 else 0
        a["lat"] += r["latency_s"]; a["tok"] += r["prompt_tokens"]
    return agg


def series(agg, mode, faults, field):
    out = []
    for fa in faults:
        a = agg[(mode, fa)]
        if field == "micro":
            out.append(100.0 * a["tp"] / a["f"])
        elif field == "lat":
            out.append(a["lat"] / a["n"])
        elif field == "tok":
            out.append(a["tok"] / a["n"])
    return out


def main():
    agg = load()
    faults = sorted({k[1] for k in agg})          # [1, 2, 4] — real buckets, honestly labeled
    xlab = [f"{f} fault" if f == 1 else f"{f} faults" for f in faults]
    x = list(range(len(faults)))

    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(15, 4.2))

    def line(ax, ys, color, label, fmt="{:.0f}", dy=2):
        ax.plot(x, ys, "o-", color=color, label=label, lw=2, ms=7)
        for xi, y in zip(x, ys):
            ax.annotate(fmt.format(y), (xi, y), textcoords="offset points",
                        xytext=(0, dy * (6 if dy > 0 else -14)), ha="center",
                        color=color, fontsize=10, fontweight="bold")

    # --- accuracy (HONEST: parity) ---
    line(ax1, series(agg, "raw", faults, "micro"), RAW_C, "Without pipeline (raw)", dy=-1)
    line(ax1, series(agg, "distilled", faults, "micro"), DIST_C, "With pipeline (distilled)", dy=1)
    ax1.set_title("Localization accuracy by difficulty")
    ax1.set_ylabel("% root causes found (micro-recall)")
    ax1.set_ylim(0, 105)
    ax1.legend(loc="lower left", fontsize=9)

    # --- latency ---
    line(ax2, series(agg, "raw", faults, "lat"), RAW_C, "Without pipeline (raw)", fmt="{:.0f}s", dy=-1)
    line(ax2, series(agg, "distilled", faults, "lat"), DIST_C, "With pipeline (distilled)", fmt="{:.0f}s", dy=1)
    ax2.set_title("Diagnosis latency by difficulty")
    ax2.set_ylabel("seconds (mean)")
    ax2.set_ylim(0, max(series(agg, "raw", faults, "lat")) * 1.25)
    ax2.legend(loc="upper left", fontsize=9)

    # --- tokens (log) ---
    line(ax3, series(agg, "raw", faults, "tok"), RAW_C, "Without pipeline (raw)",
         fmt="{:.0f}", dy=1)
    line(ax3, series(agg, "distilled", faults, "tok"), DIST_C, "With pipeline (distilled)",
         fmt="{:.0f}", dy=-1)
    ax3.set_yscale("log")
    ax3.set_title("Prompt context by difficulty")
    ax3.set_ylabel("tokens (log scale)")
    ax3.legend(loc="center left", fontsize=9)

    for ax in (ax1, ax2, ax3):
        ax.set_xticks(x); ax.set_xticklabels(xlab)
        ax.grid(True, alpha=0.3)

    fig.tight_layout()
    fig.savefig("exp4_corrected.png", dpi=150, bbox_inches="tight")
    print("saved exp4_corrected.png")
    # dump the numbers used, for the caption / table
    for mode in ("raw", "distilled"):
        print(mode, "micro:", [round(v, 1) for v in series(agg, mode, faults, "micro")],
              "lat:", [round(v, 1) for v in series(agg, mode, faults, "lat")],
              "tok:", [round(v) for v in series(agg, mode, faults, "tok")])


if __name__ == "__main__":
    main()
