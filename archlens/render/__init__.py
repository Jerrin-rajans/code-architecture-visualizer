"""Rendering: architecture diagrams (Graphviz) and their visual theme.

`architecture` imports eagerly - the heavyweight `diagrams` import lives inside
:func:`archlens.render.architecture.render_architecture`, so nothing expensive
happens at import time.
"""

from __future__ import annotations

from .architecture import (
    GraphvizMissingError,
    ensure_graphviz_on_path,
    graphviz_available,
    render_architecture,
    render_context_diagram,
)

__all__ = [
    "GraphvizMissingError",
    "ensure_graphviz_on_path",
    "graphviz_available",
    "render_architecture",
    "render_context_diagram",
]
