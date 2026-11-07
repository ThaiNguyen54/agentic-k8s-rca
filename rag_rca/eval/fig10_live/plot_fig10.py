# eval/fig10_live/plot_fig10.py
"""Fig. 10 replacement: localization accuracy (top) and diagnosis time (bottom)
for one, two and three concurrent faults, proposed vs plain. Bars are over
runs; dots are the three configurations of each level. IEEE single column.

    python fig10_live/plot_fig10.py
"""
import json
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
STYLE = {"plain": {"label": "Plain LLM (alert only)", "color": "#eb6834", "hatch": "////"},
         "proposed": {"label": "Proposed (live context + validation)", "color": "#2a78d6", "hatch": ""}}
LEVELS = [("1", "1 fault"), ("2", "2 faults"), ("3", "3 faults")]
CFG_LEVEL = {"1": ["1-1", "1-2", "1-3"], "2": ["2-1", "2-2", "2-3"], "3": ["3-1", "3-2", "3-3"]}


def main():
    s = json.load(open(os.path.join(HERE, "summary.json")))
    plt.rcParams.update({"font.family": "serif", "font.size": 8, "axes.linewidth": 0.6,
                         "hatch.linewidth": 0.6, "pdf.fonttype": 42})
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(3.5, 3.6), sharex=True)
    w = 0.36
    for j, cond in enumerate(("plain", "proposed")):
        st = STYLE[cond]
        for i, (lvl, _) in enumerate(LEVELS):
            a = s["levels"].get(lvl, {}).get(cond)
            if not a:
                continue
            x = i + (j - 0.5) * (w + 0.02)
            cfgs = [s["configs"][c][cond] for c in CFG_LEVEL[lvl] if cond in s["configs"].get(c, {})]
            for ax, val, key, ci in ((ax1, a["localization_pct"], "localization_pct", a["localization_wilson95"]),
                                     (ax2, a["mean_time_s"], "mean_time_s", None)):
                ax.bar(x, val, w, color=st["color"], hatch=st["hatch"], edgecolor="white", linewidth=0.4,
                       label=st["label"] if i == 0 else None, zorder=2)
                if ci:
                    ax.errorbar(x, val, yerr=[[val - ci[0]], [ci[1] - val]], fmt="none", ecolor="#333333",
                                elinewidth=0.7, capsize=2, zorder=3)
                pts = sorted(c[key] for c in cfgs)
                xs = [x + 0.07 * (pts[:k].count(p) - (pts.count(p) - 1) / 2) for k, p in enumerate(pts)]
                ax.scatter(xs, pts, s=7, facecolor="white", edgecolor="#333333", linewidth=0.6, zorder=4,
                           label="Per configuration" if (i == 0 and j == 1 and ax is ax1) else None)
                peak = max([val] + pts + ([ci[1]] if ci else []))
                ax.text(x, peak * 1.02 + (2 if ax is ax1 else 0), f"{val:.0f}", ha="center", va="bottom",
                        fontsize=6.5, color="#333333")
    ax1.set_ylabel("Localization (%)")
    ax1.set_ylim(0, 118)
    ax1.set_yticks(range(0, 101, 20))
    ax2.set_ylabel("Diagnosis time (s)")
    tmax = max(max(c.get(k, {}).get("mean_time_s", 0) for c in s["configs"].values() for k in ("plain", "proposed")),
               1)
    ax2.set_ylim(0, tmax * 1.25)
    for ax in (ax1, ax2):
        ax.grid(axis="y", color="#dddddd", linewidth=0.5, zorder=0)
        ax.spines[["top", "right"]].set_visible(False)
    ax2.set_xticks(range(len(LEVELS)))
    ax2.set_xticklabels([g for _, g in LEVELS])
    ax1.legend(loc="upper center", bbox_to_anchor=(0.5, 1.28), ncol=2, frameon=False, fontsize=6.3,
               handlelength=1.4, columnspacing=0.8)
    fig.tight_layout(pad=0.3, h_pad=0.6)
    for ext in ("pdf", "png"):
        fig.savefig(os.path.join(HERE, f"fig10_live.{ext}"), dpi=300)
    print("saved", os.path.join(HERE, "fig10_live.pdf"))


if __name__ == "__main__":
    main()
