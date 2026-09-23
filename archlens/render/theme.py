"""Visual styling for the Graphviz output.

The defaults `diagrams` ships with look like a 2010 Visio export: tight spacing,
harsh black edges, Sans-serif labels. These attributes are what make the output
look like something you would put in a design doc.

Font note: Graphviz resolves `fontname` against the *system's* fonts, not the
browser's. We pass a comma-separated stack so it falls back gracefully on
machines without Inter installed.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import EdgeKind

FONT_STACK = "Inter,Segoe UI,Helvetica Neue,Helvetica,Arial,sans-serif"

# Graph-level attributes. `splines=spline` keeps edge labels legible, which
# `ortho` does not - ortho drops labels onto the line and they collide.
GRAPH_ATTR: dict[str, str] = {
    "fontname": FONT_STACK,
    "fontsize": "15",
    "labelloc": "t",
    "pad": "0.6",
    "nodesep": "0.55",
    "ranksep": "0.95",
    "splines": "spline",
    # Merge edges that share an endpoint. With several services writing to the
    # same database this is the difference between a diagram and a hairball.
    "concentrate": "true",
    "compound": "true",
    "dpi": "192",  # retina-sharp PNG without blowing up the SVG
    # `newrank=true` reassigns ranks globally and scatters clustered nodes;
    # the per-cluster default keeps tiers visually together.
    "clusterrank": "local",
}

# Only typography here. `diagrams` sets each node's `shape`, `height` and
# `image` itself, and overriding those crops the icon or collides the label
# with it - the defaults are tuned to the icon artwork.
NODE_ATTR: dict[str, str] = {
    "fontname": FONT_STACK,
    "fontsize": "12",
}


@dataclass(frozen=True, slots=True)
class ClusterPalette:
    """One cluster's colour set, in light and dark variants."""

    bg: str
    border: str
    text: str
    dark_bg: str
    dark_border: str
    dark_text: str


# Indexed by nesting depth, so a subnet inside a VPC reads as a distinct box.
CLUSTER_PALETTE: list[ClusterPalette] = [
    ClusterPalette("#f8fafc", "#cbd5e1", "#334155", "#111a2e", "#334155", "#cbd5e1"),
    ClusterPalette("#eef2ff", "#a5b4fc", "#3730a3", "#151d3b", "#4c51bf", "#c7d2fe"),
    ClusterPalette("#ecfdf5", "#6ee7b7", "#065f46", "#0f2521", "#10b981", "#a7f3d0"),
    ClusterPalette("#fff7ed", "#fdba74", "#9a3412", "#2a1c11", "#f59e0b", "#fed7aa"),
    ClusterPalette("#fdf4ff", "#e9a5f8", "#701a75", "#26132b", "#a855f7", "#f5d0fe"),
]


def cluster_attrs(palette: ClusterPalette, *, dark: bool = False, depth: int = 0) -> dict[str, str]:
    """Graphviz attributes for one cluster box."""
    return {
        "bgcolor": palette.dark_bg if dark else palette.bg,
        "pencolor": palette.dark_border if dark else palette.border,
        "fontcolor": palette.dark_text if dark else palette.text,
        "fontname": FONT_STACK,
        "fontsize": str(max(12, 15 - depth)),
        "penwidth": "1.6",
        "style": "rounded,filled",
        "margin": "22",
        "labeljust": "l",
        "labelloc": "t",
    }


# Edge styling per semantic kind. Line style is the signal - colour alone is not
# accessible, so async is dashed and deploy is dotted regardless of palette.
EDGE_STYLES: dict[EdgeKind, dict[str, str]] = {
    EdgeKind.SYNC: {
        "color": "#475569",
        "style": "solid",
        "penwidth": "1.7",
        "fontsize": "10",
    },
    EdgeKind.ASYNC: {
        "color": "#a855f7",
        "style": "dashed",
        "penwidth": "1.7",
        "fontsize": "10",
    },
    EdgeKind.DATA: {
        "color": "#0ea5e9",
        "style": "solid",
        "penwidth": "1.5",
        "fontsize": "10",
    },
    EdgeKind.DEPLOY: {
        "color": "#94a3b8",
        "style": "dotted",
        "penwidth": "1.4",
        "fontsize": "10",
    },
}

# The same semantics, for the HTML legend.
EDGE_LEGEND: list[tuple[str, str, str]] = [
    ("Synchronous", "#475569", "solid"),
    ("Asynchronous / events", "#a855f7", "dashed"),
    ("Data read / write", "#0ea5e9", "solid"),
    ("Build & deploy", "#94a3b8", "dotted"),
]
