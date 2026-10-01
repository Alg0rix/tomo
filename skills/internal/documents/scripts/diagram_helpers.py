"""
Reusable matplotlib helpers for building clean, professional
box-and-arrow architecture / flow diagrams that get embedded into a
Word proposal document.

Design goals:
  - Consistent look across every figure in a document (same palette,
    same box style, same arrow style) without repeating boilerplate.
  - Auto-wrapping text inside boxes so nothing overflows regardless of
    box width or font size.
  - Simple enough to iterate quickly: render -> Read the PNG -> tweak
    coordinates -> re-render.

Usage pattern (see SKILL.md for the full workflow):

    import sys; sys.path.insert(0, "<this scripts dir>")
    from diagram_helpers import *

    fig, ax = canvas(10.6, 8.9, (0, 10.6), (0, 8.9),
                      "Figure N - Title", "Optional subtitle")
    a = box(ax, 0.5, 6, 3, 1.2, "Component A", ["detail line 1"])
    b = box(ax, 0.5, 4, 3, 1.2, "Component B", ["detail line 2"])
    arrow(ax, bottom(a), top(b), label="data flows down")
    save(fig, "fig1_example")   # writes img/fig1_example.png at 200 dpi

Always render at 200 dpi and Read the resulting PNG before moving on -
text overflow and overlapping arrows are only obvious visually, not
from the code.
"""

import os
import textwrap
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Rectangle

plt.rcParams["font.family"] = "DejaVu Sans"

# ---------------------------------------------------------------------
# Palette - swap these for brand colors if the deliverable needs it.
# Keep one fill/edge pair per semantic role (source, processing,
# storage, api, presentation, new/highlight, out-of-scope/muted) so a
# reader can learn the color language once and reuse it across every
# figure in the document.
# ---------------------------------------------------------------------
INK = "#1B2A38"
SRC_F, SRC_E = "#DCE9F7", "#2E6DA4"  # data sources / inputs (blue)
PROC_F, PROC_E = "#DCF0EA", "#1F8A70"  # processing / compute (teal)
DB_F, DB_E = "#FDEFD4", "#C8860D"  # storage (amber)
API_F, API_E = "#E7E2F5", "#6B54B8"  # API / integration (purple)
UI_F, UI_E = "#E3F1DC", "#4A8C2A"  # presentation / UI (green)
NEW_F, NEW_E = "#FCE1DC", "#C0442E"  # new/highlighted component (red)
OUT_F, OUT_E = "#F0F1F3", "#8A93A0"  # out of scope / external (grey)
BAND = "#F6F8FA"

OUTPUT_DIR = os.environ.get("DIAGRAM_OUTPUT_DIR", "./img")


def _wrap(txt, w_units, fontsize, pad=0.20, bold=False):
    """Word-wrap text to fit a box of width w_units (matplotlib data
    units, i.e. inches on this canvas) at the given point size."""
    usable = max(0.4, (w_units - pad)) * 72.0
    ch = max(6, int(usable / ((0.645 if bold else 0.600) * fontsize)))
    out = []
    for part in str(txt).split("\n"):
        out += textwrap.wrap(part, width=ch) or [""]
    return out


def box(
    ax,
    x,
    y,
    w,
    h,
    title,
    lines=None,
    fill=SRC_F,
    edge=SRC_E,
    dashed=False,
    tsize=10.5,
    bsize=8.6,
    badge=None,
    radius=0.10,
    badge_pos="tr",
):
    """Draw a rounded rectangle with a bold title and optional detail
    lines, auto-wrapped and vertically centered. Returns (x, y, w, h)
    so you can pass it straight into bottom()/top()/left()/right()."""
    p = FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle=f"round,pad=0,rounding_size={radius}",
        linewidth=1.6,
        edgecolor=edge,
        facecolor=fill,
        linestyle=(0, (4, 2.5)) if dashed else "solid",
        zorder=3,
    )
    ax.add_patch(p)
    tl = _wrap(title, w, tsize, bold=True)
    bl = []
    for ln in lines or []:
        bl += _wrap(ln, w, bsize)
    th = tsize / 72.0 * 1.30
    bh = bsize / 72.0 * 1.32
    total = len(tl) * th + (bh * 0.45 if bl else 0) + len(bl) * bh
    cy = y + h / 2 + total / 2
    cx = x + w / 2
    for ln in tl:
        cy -= th / 2
        ax.text(
            cx,
            cy,
            ln,
            ha="center",
            va="center",
            fontsize=tsize,
            fontweight="bold",
            color=INK,
            zorder=4,
        )
        cy -= th / 2
    if bl:
        cy -= bh * 0.45
    for ln in bl:
        cy -= bh / 2
        ax.text(
            cx,
            cy,
            ln,
            ha="center",
            va="center",
            fontsize=bsize,
            color="#41505E",
            zorder=4,
        )
        cy -= bh / 2
    if badge:
        if badge_pos == "bl":
            ax.text(
                x + 0.09,
                y + 0.07,
                badge,
                ha="left",
                va="bottom",
                fontsize=7.4,
                fontweight="bold",
                color=edge,
                zorder=5,
            )
        else:
            ax.text(
                x + w - 0.09,
                y + h - 0.09,
                badge,
                ha="right",
                va="top",
                fontsize=7.4,
                fontweight="bold",
                color=edge,
                zorder=5,
            )
    return (x, y, w, h)


def arrow(
    ax,
    p1,
    p2,
    label=None,
    color="#5A6B7B",
    dashed=False,
    rad=0.0,
    lsize=8.2,
    loff=(0, 0.16),
    lw=1.7,
):
    """Draw an arrow between two points (use bottom()/top()/left()/
    right() on a box's return value to anchor it). rad adds curvature
    for arrows that would otherwise overlap."""
    a = FancyArrowPatch(
        p1,
        p2,
        arrowstyle="-|>",
        mutation_scale=14,
        linewidth=lw,
        color=color,
        zorder=2,
        linestyle=(0, (4, 2.5)) if dashed else "solid",
        connectionstyle=f"arc3,rad={rad}",
        shrinkA=2,
        shrinkB=2,
    )
    ax.add_patch(a)
    if label:
        mx, my = (p1[0] + p2[0]) / 2 + loff[0], (p1[1] + p2[1]) / 2 + loff[1]
        ax.text(
            mx,
            my,
            label,
            ha="center",
            va="center",
            fontsize=lsize,
            color="#41505E",
            zorder=6,
            bbox=dict(boxstyle="round,pad=0.22", fc="white", ec="none", alpha=0.94),
        )


def band(ax, x, y, w, h, label, color=BAND, ec="#D4DBE2", lsize=8.6, vertical=False):
    """Draw a dashed background frame grouping several boxes, with a
    label in the corner (or rotated along the left edge)."""
    ax.add_patch(
        Rectangle(
            (x, y),
            w,
            h,
            facecolor=color,
            edgecolor=ec,
            linewidth=1.0,
            linestyle=(0, (3, 3)),
            zorder=1,
        )
    )
    if label and vertical:
        ax.text(
            x + 0.16,
            y + h / 2,
            label,
            ha="center",
            va="center",
            rotation=90,
            fontsize=lsize,
            color="#6B7885",
            fontweight="bold",
            zorder=2,
        )
    elif label:
        ax.text(
            x + 0.12,
            y + h - 0.14,
            label,
            ha="left",
            va="top",
            fontsize=lsize,
            color="#6B7885",
            fontweight="bold",
            zorder=2,
        )


def canvas(w, h, xlim, ylim, title=None, sub=None):
    """Create a figure/axes pair sized in inches with a title and
    optional subtitle in the top-left corner."""
    fig, ax = plt.subplots(figsize=(w, h))
    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    ax.axis("off")
    fig.patch.set_facecolor("white")
    if title:
        ax.text(
            xlim[0] + 0.05,
            ylim[1] - 0.28,
            title,
            ha="left",
            va="center",
            fontsize=13.5,
            fontweight="bold",
            color=INK,
        )
    if sub:
        ax.text(
            xlim[0] + 0.05,
            ylim[1] - 0.66,
            sub,
            ha="left",
            va="center",
            fontsize=9.4,
            color="#6B7885",
        )
    return fig, ax


def save(fig, name, output_dir=None):
    """Save at 200 dpi (sharp enough to embed at ~5-6in wide in a
    Word doc without looking soft) into output_dir/name.png."""
    outdir = output_dir or OUTPUT_DIR
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, f"{name}.png")
    fig.savefig(path, dpi=200, bbox_inches="tight", pad_inches=0.16, facecolor="white")
    plt.close(fig)
    print("wrote", path)
    return path


def bottom(b):
    return (b[0] + b[2] / 2, b[1])


def top(b):
    return (b[0] + b[2] / 2, b[1] + b[3])


def left(b):
    return (b[0], b[1] + b[3] / 2)


def right(b):
    return (b[0] + b[2], b[1] + b[3] / 2)
