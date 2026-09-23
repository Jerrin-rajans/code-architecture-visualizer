"""Mermaid flowchart generation.

Mermaid is rendered client-side in the browser, so these functions only produce
source text. Labels are escaped defensively: an unescaped quote or bracket in a
route path silently breaks the whole diagram in Mermaid, so everything goes
through :func:`_label`.
"""

from __future__ import annotations

import re
from collections import defaultdict

from ..models import (
    Architecture,
    EdgeKind,
    Evidence,
    Flow,
)
from ..models import (
    ComponentKind as K,
)
from .heuristics import slug

# Mermaid node shapes per component role - shape carries meaning at a glance.
_SHAPES: dict[K, tuple[str, str]] = {
    K.CLIENT: ("([", "])"),
    K.DATABASE: ("[(", ")]"),
    K.CACHE: ("[(", ")]"),
    K.STORAGE: ("[(", ")]"),
    K.QUEUE: (">", "]"),
    K.STREAM: (">", "]"),
    K.EXTERNAL: ("[/", "/]"),
    K.GATEWAY: ("{{", "}}"),
    K.CDN: ("{{", "}}"),
    K.AUTH: ("{{", "}}"),
    K.FUNCTION: ("([", "])"),
}
_DEFAULT_SHAPE = ("[", "]")


def _label(text: str, limit: int = 46) -> str:
    """Escape a string for use inside a Mermaid node label."""
    cleaned = re.sub(r"\s+", " ", str(text)).strip()
    cleaned = cleaned.replace('"', "'").replace("`", "'")
    # Mermaid treats these as syntax even inside quoted labels.
    cleaned = cleaned.replace("[", "(").replace("]", ")")
    cleaned = cleaned.replace("{", "(").replace("}", ")")
    cleaned = cleaned.replace("|", "/").replace("<", "&lt;").replace(">", "&gt;")
    if len(cleaned) > limit:
        cleaned = cleaned[: limit - 1].rstrip() + "…"
    return cleaned or "unnamed"


def _edge_arrow(kind: EdgeKind) -> str:
    return {
        EdgeKind.SYNC: "-->",
        EdgeKind.ASYNC: "-.->",
        EdgeKind.DATA: "-->",
        EdgeKind.DEPLOY: "-.->",
    }.get(kind, "-->")


def _node(component_id: str, name: str, kind: K) -> str:
    open_shape, close_shape = _SHAPES.get(kind, _DEFAULT_SHAPE)
    return f'{component_id}{open_shape}"{_label(name)}"{close_shape}'


# Class definitions give the flowcharts the same palette as the rest of the UI.
_CLASS_DEFS = """
    classDef edge fill:#eef2ff,stroke:#6366f1,stroke-width:1.5px,color:#1e1b4b;
    classDef app fill:#ecfdf5,stroke:#10b981,stroke-width:1.5px,color:#064e3b;
    classDef data fill:#fff7ed,stroke:#f59e0b,stroke-width:1.5px,color:#78350f;
    classDef msg fill:#fdf4ff,stroke:#a855f7,stroke-width:1.5px,color:#4a044e;
    classDef ext fill:#f1f5f9,stroke:#64748b,stroke-width:1.5px,color:#0f172a;
""".rstrip()

_KIND_CLASS: dict[K, str] = {
    K.CLIENT: "edge", K.CDN: "edge", K.GATEWAY: "edge", K.FRONTEND: "edge", K.AUTH: "edge",
    K.SERVICE: "app", K.FUNCTION: "app", K.WORKER: "app",
    K.DATABASE: "data", K.CACHE: "data", K.STORAGE: "data", K.SEARCH: "data", K.ANALYTICS: "data",
    K.QUEUE: "msg", K.STREAM: "msg",
    K.EXTERNAL: "ext", K.ML: "ext", K.MONITORING: "ext", K.CICD: "ext", K.INFRA: "ext",
}


def system_flow(arch: Architecture) -> Flow:
    """End-to-end component flow, mirroring the architecture diagram."""
    lines = ["flowchart LR", _CLASS_DEFS]
    by_class: dict[str, list[str]] = defaultdict(list)

    for component in arch.components:
        lines.append(f"    {_node(component.id, component.name, component.kind)}")
        by_class[_KIND_CLASS.get(component.kind, "ext")].append(component.id)

    lines.append("")
    for edge in arch.edges:
        arrow = _edge_arrow(edge.kind)
        if edge.label:
            lines.append(f"    {edge.source} {arrow}|{_label(edge.label, 24)}| {edge.target}")
        else:
            lines.append(f"    {edge.source} {arrow} {edge.target}")

    lines.append("")
    for class_name, ids in by_class.items():
        if ids:
            lines.append(f"    class {','.join(ids)} {class_name};")

    return Flow(
        id="system-flow",
        title="System Flow",
        description="How a request moves through the system, and which components "
                    "talk to which. Dashed arrows are asynchronous.",
        diagram_type="flowchart",
        mermaid="\n".join(lines),
    )


def request_sequence(evidence: Evidence, arch: Architecture) -> Flow | None:
    """A sequence diagram for the primary request path."""
    if not evidence.routes:
        return None

    client = "Client"
    gateway = next((c for c in arch.components if c.kind in {K.GATEWAY, K.CDN}), None)
    app = next((c for c in arch.components if c.kind in {K.SERVICE, K.FUNCTION}), None)
    auth = next((c for c in arch.components if c.kind is K.AUTH), None)
    cache = next((c for c in arch.components if c.kind is K.CACHE), None)
    db = next((c for c in arch.components if c.kind is K.DATABASE), None)
    queue = next((c for c in arch.components if c.kind in {K.QUEUE, K.STREAM}), None)

    if app is None:
        return None

    lines = ["sequenceDiagram", "    autonumber"]
    participants: list[tuple[str, str]] = [("client", client)]
    if gateway:
        participants.append(("gw", gateway.name))
    participants.append(("app", app.name))
    if auth:
        participants.append(("auth", auth.name))
    if cache:
        participants.append(("cache", cache.name))
    if db:
        participants.append(("db", db.name))
    if queue:
        participants.append(("queue", queue.name))

    for alias, name in participants:
        lines.append(f"    participant {alias} as {_label(name, 28)}")

    sample = evidence.routes[0]
    route_desc = f"{sample.method} {sample.path}"
    lines.append("")
    lines.append(f"    client->>+{'gw' if gateway else 'app'}: {_label(route_desc, 40)}")
    if gateway:
        lines.append("    gw->>+app: forward request")
    if auth:
        lines.append("    app->>+auth: validate token")
        lines.append("    auth-->>-app: claims")
    if cache:
        lines.append("    app->>+cache: lookup")
        lines.append("    cache-->>-app: hit / miss")
    if db:
        lines.append("    app->>+db: query")
        lines.append("    db-->>-app: rows")
    if queue:
        lines.append("    app-)queue: publish event")
    if gateway:
        lines.append("    app-->>-gw: response")
        lines.append("    gw-->>-client: 200 OK")
    else:
        lines.append("    app-->>-client: 200 OK")

    return Flow(
        id="request-sequence",
        title="Request Lifecycle",
        description=f"Traced from `{route_desc}` in `{sample.file}`.",
        diagram_type="sequence",
        mermaid="\n".join(lines),
    )


def api_surface_flow(evidence: Evidence) -> Flow | None:
    """Group HTTP routes by resource prefix so the API shape is visible."""
    if len(evidence.routes) < 2:
        return None

    groups: dict[str, list] = defaultdict(list)
    for route in evidence.routes:
        segments = [s for s in route.path.strip("/").split("/") if s and not s.startswith((":", "{", "<"))]
        groups[segments[0] if segments else "root"].append(route)

    lines = ["flowchart LR", _CLASS_DEFS, '    api{{"API"}}', "    class api edge;"]
    group_ids: list[str] = []
    route_ids: list[str] = []

    for group_name, routes in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:8]:
        gid = "g_" + slug(group_name)
        group_ids.append(gid)
        lines.append(f'    {gid}["/{_label(group_name, 24)} ({len(routes)})"]')
        lines.append(f"    api --> {gid}")
        for route in routes[:6]:
            rid = "r_" + slug(f"{route.method}_{route.path}")
            route_ids.append(rid)
            lines.append(f'    {rid}["{_label(route.method + " " + route.path, 40)}"]')
            lines.append(f"    {gid} --> {rid}")

    if group_ids:
        lines.append(f"    class {','.join(group_ids)} app;")
    if route_ids:
        lines.append(f"    class {','.join(route_ids)} ext;")

    return Flow(
        id="api-surface",
        title="API Surface",
        description=f"{len(evidence.routes)} HTTP routes grouped by top-level resource.",
        diagram_type="flowchart",
        mermaid="\n".join(lines),
    )


def module_flow(evidence: Evidence) -> Flow | None:
    """Internal package dependency graph."""
    if len(evidence.module_edges) < 3:
        return None

    lines = ["flowchart TD", _CLASS_DEFS]
    nodes: set[str] = set()
    for edge in evidence.module_edges[:28]:
        for name in (edge.source, edge.target):
            node_id = "m_" + slug(name)
            if node_id not in nodes:
                nodes.add(node_id)
                lines.append(f'    {node_id}["{_label(name, 28)}"]')
    lines.append("")
    for edge in evidence.module_edges[:28]:
        src, dst = "m_" + slug(edge.source), "m_" + slug(edge.target)
        weight_label = f"|{edge.weight}|" if edge.weight > 2 else ""
        lines.append(f"    {src} -->{weight_label} {dst}")
    if nodes:
        lines.append(f"    class {','.join(sorted(nodes))} app;")

    return Flow(
        id="module-graph",
        title="Internal Module Dependencies",
        description="First-party package imports. Numbers show how many files "
                    "create the dependency.",
        diagram_type="flowchart",
        mermaid="\n".join(lines),
    )


def deployment_flow(evidence: Evidence) -> Flow | None:
    """CI/CD pipeline, when build automation is present."""
    ci_signals = [s for s in evidence.signals if s.kind is K.CICD]
    if not ci_signals:
        return None

    ci_name = ci_signals[0].label
    has_docker = any(s.service == "onprem.docker" for s in evidence.signals)
    has_registry = any(s.service in {"aws.ecr", "azure.acr", "gcp.gcr"} for s in evidence.signals)
    iac_kinds = sorted({r.kind for r in evidence.iac if r.kind in {"terraform", "bicep", "cloudformation"}})
    target = next(
        (s.label for s in evidence.signals
         if s.service in {"aws.ecs", "aws.eks", "aws.lambda", "azure.appservice",
                          "azure.aks", "azure.functions", "gcp.run", "gcp.gke",
                          "k8s.deployment"}),
        "Runtime environment",
    )

    lines = [
        "flowchart LR", _CLASS_DEFS,
        '    dev(["Developer"])',
        '    repo["Git repository"]',
        f'    ci["{_label(ci_name)}"]',
        '    test["Build &amp; test"]',
        "    dev --> repo",
        "    repo -->|push / PR| ci",
        "    ci --> test",
    ]
    last = "test"
    if has_docker:
        lines.append('    image["Container image"]')
        lines.append(f"    {last} --> image")
        last = "image"
    if has_registry:
        lines.append('    registry[("Container registry")]')
        lines.append(f"    {last} --> registry")
        last = "registry"
    if iac_kinds:
        lines.append(f'    iac["Provision infra ({_label(", ".join(iac_kinds), 28)})"]')
        lines.append(f"    {last} --> iac")
        last = "iac"
    lines.append(f'    deploy["Deploy to {_label(target, 30)}"]')
    lines.append(f"    {last} --> deploy")
    lines.append("    class dev,repo edge;")
    lines.append("    class ci,test,deploy app;")

    return Flow(
        id="deployment-flow",
        title="Build & Deployment Pipeline",
        description=f"Inferred from {ci_name} configuration and IaC in the repository.",
        diagram_type="flowchart",
        mermaid="\n".join(lines),
    )


def build_flows(evidence: Evidence, arch: Architecture) -> list[Flow]:
    """Every flowchart the static layer can justify from the evidence."""
    candidates = [
        system_flow(arch),
        request_sequence(evidence, arch),
        api_surface_flow(evidence),
        module_flow(evidence),
        deployment_flow(evidence),
    ]
    return [flow for flow in candidates if flow is not None]
