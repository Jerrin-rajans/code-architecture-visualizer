"""Render an :class:`Architecture` to PNG/SVG using `diagrams` + Graphviz.

Two constraints shape this module:

* **`diagrams` is context-global.** `Diagram` and `Cluster` push onto module-level
  state, so two concurrent renders corrupt each other. Every render therefore
  takes `_RENDER_LOCK`. The web server renders in a worker thread, so this is
  not theoretical.
* **Graphviz is an external binary.** When `dot` is missing the render raises
  rather than producing a broken image, and the caller turns that into a
  readable message instead of a stack trace.
"""

from __future__ import annotations

import base64
import contextlib
import logging
import os
import re
import shutil
import textwrap
import threading
from pathlib import Path

from ..models import (
    Architecture,
    Cluster,
    Component,
    Edge,
    EdgeKind,
    RenderedDiagram,
)
from ..models import (
    ComponentKind as K,
)
from .icons import resolve
from .theme import (
    CLUSTER_PALETTE,
    EDGE_STYLES,
    GRAPH_ATTR,
    NODE_ATTR,
    cluster_attrs,
)

log = logging.getLogger(__name__)

_RENDER_LOCK = threading.Lock()

# Above this many nodes Graphviz's `dot` layout stops being readable; we switch
# to a wider aspect ratio and tighter spacing rather than producing a mural.
_DENSE_THRESHOLD = 22


class GraphvizMissingError(RuntimeError):
    """Raised when the Graphviz `dot` executable cannot be found."""


# Places a working `dot` commonly lives when it is not already on PATH.
# Checked in order; the first hit is prepended to PATH for this process.
def _candidate_graphviz_dirs() -> list[Path]:
    candidates: list[Path] = []

    override = os.environ.get("ARCHLENS_GRAPHVIZ_BIN")
    if override:
        candidates.append(Path(override))

    # A copy vendored next to the installed package or the repo checkout.
    here = Path(__file__).resolve()
    for parent in list(here.parents)[:4]:
        candidates.append(parent / ".tools" / "graphviz" / "bin")

    if os.name == "nt":
        for program_files in (os.environ.get("ProgramFiles"),
                              os.environ.get("ProgramFiles(x86)")):
            if program_files:
                base = Path(program_files)
                candidates.append(base / "Graphviz" / "bin")
                candidates.extend(sorted(base.glob("Graphviz*/bin"), reverse=True))
        # Conda environments keep it under Library\bin.
        conda_prefix = os.environ.get("CONDA_PREFIX")
        if conda_prefix:
            candidates.append(Path(conda_prefix) / "Library" / "bin")
    else:
        candidates.extend([Path("/usr/local/bin"), Path("/opt/homebrew/bin"), Path("/usr/bin")])

    return candidates


_graphviz_resolved = False


def ensure_graphviz_on_path() -> str | None:
    """Locate `dot` and make it callable, returning its path.

    Graphviz is a native binary that `pip install` cannot provide, so a working
    install frequently exists on disk without being on PATH - a conda env, a
    user-scope install, or a copy vendored beside the project. Finding it
    ourselves turns a hard failure into a non-event.
    """
    global _graphviz_resolved

    found = shutil.which("dot")
    if found:
        return found
    if _graphviz_resolved:
        return None

    for directory in _candidate_graphviz_dirs():
        executable = directory / ("dot.exe" if os.name == "nt" else "dot")
        if executable.is_file():
            os.environ["PATH"] = f"{directory}{os.pathsep}{os.environ.get('PATH', '')}"
            log.info("Using Graphviz from %s", directory)
            _graphviz_resolved = True
            return str(executable)

    _graphviz_resolved = True
    return None


def graphviz_available() -> bool:
    return ensure_graphviz_on_path() is not None


def _wrap(text: str, width: int = 18) -> str:
    """Wrap a node label so wide names do not stretch the whole graph."""
    words = textwrap.wrap(text.strip(), width=width, break_long_words=False)
    return "\n".join(words[:3]) if words else text


def _edge_style(edge: Edge) -> dict:
    return EDGE_STYLES.get(edge.kind, EDGE_STYLES[EdgeKind.SYNC])


def _cluster_tree(arch: Architecture) -> tuple[dict[str | None, list[Cluster]], dict[str, list[Component]]]:
    """Index clusters by parent and components by cluster."""
    children: dict[str | None, list[Cluster]] = {}
    known = {c.id for c in arch.clusters}
    for cluster in arch.clusters:
        parent = cluster.parent if cluster.parent in known else None
        children.setdefault(parent, []).append(cluster)

    by_cluster: dict[str, list[Component]] = {}
    for component in arch.components:
        key = component.cluster if component.cluster in known else ""
        by_cluster.setdefault(key, []).append(component)
    return children, by_cluster


def _sort_components(components: list[Component]) -> list[Component]:
    """Order nodes so related roles sit together inside a cluster."""
    from ..models import KIND_TIER

    return sorted(components, key=lambda c: (KIND_TIER.get(c.kind, 5), c.name.lower()))


# Graphviz writes `<image xlink:href="C:\...\resources/aws/compute\lambda.png">`
# - an absolute path on the machine that rendered it.
_SVG_IMAGE_HREF = re.compile(r'(?P<attr>(?:xlink:)?href)="(?P<path>[^"]+\.(?:png|jpg|jpeg|svg))"',
                             re.IGNORECASE)

_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
}


def inline_svg_images(svg_path: Path) -> int:
    """Rewrite external image references in an SVG as base64 data URIs.

    Graphviz emits icon references as absolute filesystem paths. That works
    when you open the SVG beside the `diagrams` package, and nowhere else: a
    browser loading the SVG over HTTP cannot read `C:\\...`, so every icon
    silently disappears. Inlining makes the file self-contained, which is what
    you want for a web UI, a download, and anything committed to a repo.

    Returns the number of images embedded.
    """
    try:
        svg = svg_path.read_text(encoding="utf-8")
    except OSError:
        return 0

    cache: dict[str, str | None] = {}
    embedded = 0

    def encode(raw_path: str) -> str | None:
        if raw_path in cache:
            return cache[raw_path]
        result: str | None = None
        # Graphviz mixes separators on Windows; normalise before resolving.
        candidate = Path(raw_path.replace("\\", "/"))
        if candidate.is_file():
            try:
                data = candidate.read_bytes()
                mime = _MIME_BY_SUFFIX.get(candidate.suffix.lower(), "image/png")
                result = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
            except OSError:
                result = None
        cache[raw_path] = result
        return result

    def replace(match: re.Match[str]) -> str:
        nonlocal embedded
        raw = match.group("path")
        if raw.startswith(("data:", "http:", "https:", "#")):
            return match.group(0)
        encoded = encode(raw)
        if encoded is None:
            return match.group(0)
        embedded += 1
        return f'{match.group("attr")}="{encoded}"'

    updated = _SVG_IMAGE_HREF.sub(replace, svg)
    if embedded:
        try:
            svg_path.write_text(updated, encoding="utf-8")
        except OSError:
            return 0
    return embedded


def _without_isolated(arch: Architecture) -> Architecture:
    """Drop components nothing connects to.

    A CI system or metrics backend detected from a config file but never wired
    to anything contributes a floating box in its own cluster - pure noise that
    also distorts the layout. It stays in the component list and the evidence
    tab; it just does not earn space on the canvas.
    """
    connected: set[str] = set()
    for edge in arch.edges:
        connected.add(edge.source)
        connected.add(edge.target)
    if not connected:
        return arch

    kept = [c for c in arch.components if c.id in connected]
    if len(kept) < 2:
        return arch

    trimmed = arch.model_copy(deep=True)
    trimmed.components = kept
    used_clusters = {c.cluster for c in kept if c.cluster}
    trimmed.clusters = [c for c in trimmed.clusters if c.id in used_clusters]
    return trimmed


def render_architecture(
    arch: Architecture,
    out_dir: str | Path,
    *,
    basename: str = "architecture",
    formats: tuple[str, ...] = ("png", "svg"),
    direction: str = "LR",
    dark: bool = False,
    title: str | None = None,
) -> RenderedDiagram:
    """Render the architecture diagram. Returns paths relative to `out_dir`."""
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    title = title or f"{arch.name} — Architecture"

    if not arch.components:
        return RenderedDiagram(title=title, error="No components were identified.")
    arch = _without_isolated(arch)
    if not graphviz_available():
        raise GraphvizMissingError(
            "Graphviz is not installed or `dot` is not on PATH. "
            "Install it with `conda install -c conda-forge graphviz` "
            "or from https://graphviz.org/download/."
        )

    try:
        from diagrams import Cluster as DiagramCluster
        from diagrams import Diagram
        from diagrams import Edge as DiagramEdge
    except ImportError as exc:
        raise GraphvizMissingError("The `diagrams` package is not installed.") from exc

    dense = len(arch.components) > _DENSE_THRESHOLD
    graph_attr = dict(GRAPH_ATTR)
    graph_attr.update({
        "rankdir": direction,
        "bgcolor": "#0b1020" if dark else "white",
        "fontcolor": "#e2e8f0" if dark else "#0f172a",
    })
    if dense:
        graph_attr.update({"nodesep": "0.45", "ranksep": "1.0", "ratio": "compress"})

    node_attr = dict(NODE_ATTR)
    node_attr["fontcolor"] = "#cbd5e1" if dark else "#1e293b"

    children, by_cluster = _cluster_tree(arch)
    nodes: dict[str, object] = {}

    # `diagrams` resolves relative filenames against the process CWD, so build an
    # absolute stem and let it append the extension.
    stem = str((out_path / basename).resolve())

    with _RENDER_LOCK:
        previous_cwd = os.getcwd()
        try:
            # Graphviz drops its intermediate .gv source next to the CWD;
            # keeping the CWD inside the output dir keeps the repo clean.
            os.chdir(out_path)

            with Diagram(
                name="",  # the UI supplies the heading; a Graphviz title crowds the canvas
                filename=stem,
                outformat=list(formats),
                show=False,
                direction=direction,
                graph_attr=graph_attr,
                node_attr=node_attr,
                edge_attr={"fontname": GRAPH_ATTR["fontname"],
                           "fontcolor": "#94a3b8" if dark else "#475569"},
            ):
                def emit_components(components: list[Component]) -> None:
                    for component in _sort_components(components):
                        icon = resolve(component.service, component.kind)
                        nodes[component.id] = icon(_wrap(component.name))

                # Colour by position, not nesting depth: tiers are all depth 0,
                # so a depth-keyed palette paints every box the same grey.
                palette_index = 0

                def emit_cluster(cluster: Cluster, depth: int) -> None:
                    nonlocal palette_index
                    palette = CLUSTER_PALETTE[palette_index % len(CLUSTER_PALETTE)]
                    palette_index += 1
                    with DiagramCluster(cluster.name,
                                        graph_attr=cluster_attrs(palette, dark=dark, depth=depth)):
                        emit_components(by_cluster.get(cluster.id, []))
                        for child in children.get(cluster.id, []):
                            emit_cluster(child, depth + 1)

                # Top-level (unclustered) components first, then the cluster tree.
                emit_components(by_cluster.get("", []))
                for cluster in children.get(None, []):
                    emit_cluster(cluster, 0)

                for edge in arch.edges:
                    source = nodes.get(edge.source)
                    target = nodes.get(edge.target)
                    if source is None or target is None:
                        continue
                    style = _edge_style(edge)
                    source >> DiagramEdge(
                        label=_wrap(edge.label, 16) if edge.label else "",
                        **style,
                    ) >> target
        finally:
            os.chdir(previous_cwd)

    # Graphviz leaves the DOT source behind; it is noise in the output folder.
    dot_source = out_path / basename
    if dot_source.is_file():
        with contextlib.suppress(OSError):
            dot_source.unlink()

    rendered = RenderedDiagram(title=title)
    for fmt in formats:
        candidate = out_path / f"{basename}.{fmt}"
        if candidate.is_file():
            setattr(rendered, fmt, candidate.name)
            if fmt == "svg":
                inline_svg_images(candidate)
    if not rendered.png and not rendered.svg:
        rendered.error = "Graphviz produced no output file."
    return rendered


def render_context_diagram(
    arch: Architecture,
    out_dir: str | Path,
    *,
    basename: str = "context",
    dark: bool = False,
) -> RenderedDiagram:
    """A C4-style context view: the system as one box, plus what it talks to.

    Useful as the opening slide when the detailed diagram is dense. Built by
    collapsing everything that is not a client or an external dependency.
    """
    internal = [c for c in arch.components if not c.external and c.kind is not K.CLIENT]
    if len(internal) < 2:
        return RenderedDiagram(title=f"{arch.name} — Context", error="Not enough components.")

    clients = [c for c in arch.components if c.kind is K.CLIENT]
    externals = [c for c in arch.components if c.external or c.kind is K.EXTERNAL]
    internal_ids = {c.id for c in internal}

    system_id = "the_system"
    collapsed = Architecture(
        name=arch.name,
        provider=arch.provider,
        components=[
            *clients,
            Component(
                id=system_id,
                name=arch.name,
                kind=K.SERVICE,
                service="generic.service",
                description=f"{len(internal)} internal components",
            ),
            *externals,
        ],
    )

    edges: list[Edge] = []
    seen: set[tuple[str, str]] = set()
    for edge in arch.edges:
        source_internal = edge.source in internal_ids
        target_internal = edge.target in internal_ids
        if source_internal and target_internal:
            continue  # collapsed away
        source = system_id if source_internal else edge.source
        target = system_id if target_internal else edge.target
        if source == target or (source, target) in seen:
            continue
        seen.add((source, target))
        edges.append(Edge(source=source, target=target, label=edge.label, kind=edge.kind))
    collapsed.edges = edges
    collapsed.prune_dangling_edges()

    return render_architecture(collapsed, out_dir, basename=basename,
                               direction="LR", dark=dark,
                               title=f"{arch.name} — System Context")
