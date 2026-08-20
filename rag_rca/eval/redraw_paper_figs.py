#!/usr/bin/env python3
"""Redraw the three result figures for single-column IEEE Access print (A4).

Panels are stacked vertically (not side-by-side) so text stays legible at
\\columnwidth (~3.45in), and everything is rendered as vector PDF.
Values are transcribed from the original charts.
"""
import os
import matplotlib.pyplot as plt
import matplotlib as mpl
import numpy as np

# ---- print-oriented style: fonts sized for a ~3.45in column on A4 ----
mpl.rcParams.update({
    "pdf.fonttype": 42,          # embed TrueType (editable/searchable text)
    "font.family": "DejaVu Sans",
    "font.size": 8,
    "axes.titlesize": 8.5,
    "axes.labelsize": 8,
    "xtick.labelsize": 7.5,
    "ytick.labelsize": 7.5,
    "legend.fontsize": 7.5,
    "axes.linewidth": 0.6,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "figure.dpi": 300,
})

BLUE = "#1f77b4"     # proposed / with-context / skill-guided
ORANGE = "#ff7f0e"   # baseline / without-context / skill-free
COL_W = 3.45         # single-column width in inches
BARW = 0.38

OUT = os.path.join(os.path.dirname(__file__), "..", "..", "assets")
OUT = os.path.abspath(OUT)
os.makedirs(OUT, exist_ok=True)


def label_bars(ax, rects, fmt="{:.2f}", dy=0.4, fs=7):
    for r in rects:
        h = r.get_height()
        ax.annotate(fmt.format(h), (r.get_x() + r.get_width() / 2, h),
                    xytext=(0, dy), textcoords="offset points",
                    ha="center", va="bottom", fontsize=fs, fontweight="bold")


def grouped(ax, cats, s1, s2, l1, l2, fmt, ymax, dy):
    x = np.arange(len(cats))
    r1 = ax.bar(x - BARW / 2, s1, BARW, label=l1, color=BLUE)
    r2 = ax.bar(x + BARW / 2, s2, BARW, label=l2, color=ORANGE)
    label_bars(ax, r1, fmt, dy)
    label_bars(ax, r2, fmt, dy)
    ax.set_xticks(x, cats)
    ax.set_ylim(0, ymax)
    ax.margins(x=0.08)
    ax.grid(axis="y", lw=0.4, color="0.85", zorder=0)
    ax.set_axisbelow(True)
    return r1, r2


# ============================================================ Fig exp1 — context
def fig_context():
    models = ["gpt-4o", "qwen2.5-72b", "llama-3.3-70b"]
    withc = [0.93, 0.86, 0.75]
    without = [0.60, 0.45, 0.52]
    fig, ax = plt.subplots(figsize=(COL_W, 2.55))
    grouped(ax, models, withc, without, "With context", "Without context",
            "{:.2f}", 1.12, 0.4)
    ax.set_ylabel("Root cause accuracy")
    ax.legend(loc="upper right", frameon=False, ncol=1, handlelength=1.2,
              borderaxespad=0.2)
    ax.tick_params(axis="x", labelsize=7)
    fig.tight_layout(pad=0.4)
    p = os.path.join(OUT, "exp1_context_rca.pdf")
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    return p


# ======================================================= Fig exp2 — validation skill
def fig_validation():
    metrics = ["Faithfulness", "Specificity", "Usefulness"]
    data = {  # model: (skill-guided, skill-free)
        "GPT-4o":               ([0.70, 0.78, 0.78], [0.68, 0.68, 0.65]),
        "Qwen2.5-72B":          ([0.63, 0.70, 0.70], [0.61, 0.61, 0.58]),
        "Llama-3.3-70B-Instruct": ([0.61, 0.71, 0.68], [0.68, 0.71, 0.68]),
    }
    fig, axes = plt.subplots(3, 1, figsize=(COL_W, 5.7), sharex=True)
    r1 = r2 = None
    for ax, (name, (g, f)) in zip(axes, data.items()):
        r1, r2 = grouped(ax, metrics, g, f, "Skill-guided", "Skill-free",
                         "{:.2f}", 1.0, 0.3)
        ax.set_title(name, pad=3)
        ax.set_yticks([0, 0.5, 1.0])
    axes[1].set_ylabel("Validation quality score")
    axes[2].tick_params(axis="x", labelsize=7.5)
    fig.legend([r1, r2], ["Skill-guided validation", "Skill-free validation"],
               loc="upper center", ncol=2, frameon=False,
               bbox_to_anchor=(0.5, 1.005), handlelength=1.2, columnspacing=1.4)
    fig.tight_layout(rect=(0, 0, 1, 0.965), h_pad=0.9)
    p = os.path.join(OUT, "exp2_validation_skill.pdf")
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    return p


# ==================================================== Fig exp3 — e2e deployment
def fig_e2e():
    scen = ["A\n(1 fault)", "B\n(2 faults)", "C\n(3 faults)"]
    # (Ours=blue, Plain=orange) to keep proposed=blue consistent across figures
    loc_ours = [97.3, 92.8, 81.9]
    loc_plain = [88.7, 72.4, 52.1]
    dt_ours = [45.8, 58.6, 68.7]
    dt_plain = [30.5, 42.3, 65.2]
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(COL_W, 4.5), sharex=True)

    r1, r2 = grouped(ax1, scen, loc_ours, loc_plain, "Ours", "Plain",
                     "{:.1f}", 118, 0.5)
    ax1.set_title("Localization accuracy (%)", pad=3)
    ax1.set_yticks([0, 25, 50, 75, 100])

    grouped(ax2, scen, dt_ours, dt_plain, "Ours", "Plain", "{:.1f}", 84, 0.5)
    ax2.set_title("Diagnosis time (s)", pad=3)
    ax2.tick_params(axis="x", labelsize=7.5)

    fig.legend([r1, r2], ["Our proposed system", "Plain LLM baseline"],
               loc="upper center", ncol=2, frameon=False,
               bbox_to_anchor=(0.5, 1.01), handlelength=1.2, columnspacing=1.4)
    fig.tight_layout(rect=(0, 0, 1, 0.955), h_pad=1.0)
    p = os.path.join(OUT, "exp3_e2e_deployment.pdf")
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    return p


# ================================================ Fig exp4 — EFCA scaling sweep
def fig_efca_scaling():
    sizes = ["~85k", "~160k", "~250k"]
    x = np.arange(len(sizes))
    acc_raw = [83, 73, 90]
    acc_efca = [100, 77, 90]
    tok_raw = [81750, 150073, 239494]
    tok_efca = [840, 866, 872]
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(COL_W, 4.7))

    # --- top: localization accuracy ---
    ax1.plot(x, acc_efca, "-o", color=BLUE, lw=1.4, ms=4.5, label="Evidence-First (EFCA)")
    ax1.plot(x, acc_raw, "-o", color=ORANGE, lw=1.4, ms=4.5, label="Collect-all (raw)")
    for xi, v in zip(x, acc_efca):
        ax1.annotate(f"{v}%", (xi, v), xytext=(0, 5), textcoords="offset points",
                     ha="center", fontsize=7, fontweight="bold", color=BLUE)
    for xi, v in zip(x, acc_raw):
        ax1.annotate(f"{v}%", (xi, v), xytext=(0, -11), textcoords="offset points",
                     ha="center", fontsize=7, fontweight="bold", color=ORANGE)
    ax1.set_ylim(0, 112)
    ax1.set_yticks([0, 25, 50, 75, 100])
    ax1.set_ylabel("Root causes found\n(micro-recall, %)")
    ax1.set_title("Localization accuracy vs. raw context", pad=3)

    # --- bottom: prompt tokens (log) ---
    ax2.plot(x, tok_raw, "-o", color=ORANGE, lw=1.4, ms=4.5, label="Collect-all (raw)")
    ax2.plot(x, tok_efca, "-o", color=BLUE, lw=1.4, ms=4.5, label="Evidence-First (EFCA)")
    ax2.set_yscale("log")
    for xi, v in zip(x, tok_raw):
        ax2.annotate(f"{v:,}", (xi, v), xytext=(0, 5), textcoords="offset points",
                     ha="center", fontsize=6.5, fontweight="bold", color=ORANGE)
    for xi, v in zip(x, tok_efca):
        ax2.annotate(f"{v}", (xi, v), xytext=(0, -11), textcoords="offset points",
                     ha="center", fontsize=6.5, fontweight="bold", color=BLUE)
    ax2.set_ylim(400, 6e5)
    ax2.set_ylabel("Prompt size\n(tokens, log scale)")
    ax2.set_title("Prompt size — saving grows with scale", pad=3)

    for ax in (ax1, ax2):
        ax.set_xticks(x, sizes)
        ax.set_xlabel("Raw context size", labelpad=1)
        ax.margins(x=0.12)
        ax.grid(axis="y", lw=0.4, color="0.85")
        ax.set_axisbelow(True)

    handles = [plt.Line2D([], [], color=BLUE, marker="o", lw=1.4, ms=4.5),
               plt.Line2D([], [], color=ORANGE, marker="o", lw=1.4, ms=4.5)]
    fig.legend(handles, ["Evidence-First (EFCA)", "Collect-all (raw)"],
               loc="upper center", ncol=2, frameon=False,
               bbox_to_anchor=(0.5, 1.01), handlelength=1.6, columnspacing=1.4)
    fig.tight_layout(rect=(0, 0, 1, 0.955), h_pad=1.4)
    p = os.path.join(OUT, "exp4_efca_scaling.pdf")
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    return p


if __name__ == "__main__":
    for f in (fig_context, fig_validation, fig_e2e, fig_efca_scaling):
        print("wrote", f())
