"""Prompt construction for the Claude inference stage.

Two things matter here and drive the whole file's shape:

1. **Cache structure.** The system prompt is split into a frozen block (the
   instructions and the service-key catalog, identical across every scan) and
   an evidence block (identical across the several calls made about *one*
   repository). Both get `cache_control`, so the second and third calls of a
   scan read almost all their input from cache. Anything volatile must stay out
   of those blocks or the prefix is invalidated - see `_evidence_pack`, which
   deliberately contains no timestamps, no absolute paths and no iteration
   order that could vary between calls.

2. **Grounding.** The model is given the static baseline and told to correct it,
   not to start from a blank page. Every component it emits must cite the files
   that justify it, which makes hallucinated infrastructure obvious.
"""

from __future__ import annotations

import json

from ..models import Architecture, ComponentKind, Evidence
from ..render.icons import ICON_REGISTRY

# Keep the pack well inside the model's comfort zone while leaving room for a
# large repo's route table. Characters, not tokens - roughly 4:1.
MAX_PACK_CHARS = 90_000


def _service_catalog() -> str:
    """The exact set of icon keys the model is allowed to emit."""
    by_provider: dict[str, list[str]] = {}
    for key in ICON_REGISTRY:
        provider = key.split(".", 1)[0]
        by_provider.setdefault(provider, []).append(key)
    lines = []
    for provider in sorted(by_provider):
        keys = ", ".join(sorted(by_provider[provider]))
        lines.append(f"{provider}: {keys}")
    return "\n".join(lines)


SYSTEM_INSTRUCTIONS = f"""\
You are an experienced software architect producing documentation for an
engineering team. You are given the complete output of a static analysis pass
over one repository, and you turn it into an accurate architecture model.

# Your contract

- **Never invent infrastructure.** Every component you emit must be justified by
  something in the evidence: an IaC resource, a dependency, an import, a route,
  a container image or a connection string. Put the justifying file paths in
  `paths`. A component you cannot cite is a component you must not emit.
- **Prefer IaC over inference.** If Terraform declares an `aws_rds_cluster`, the
  database is Aurora - not "a SQL database". If the only evidence is a
  `psycopg2` dependency, it is PostgreSQL of unknown hosting; say so in the
  description rather than guessing a managed service.
- **Correct the baseline.** You are given a mechanical first draft. It is
  usually right about *what exists* and often wrong about *how things connect*
  and *what things are called*. Fix names, merge duplicates, split things that
  are really several services, and re-wire edges to match how the code actually
  calls out.
- **Name things the way the team does.** Use the names that appear in the
  repository - service directory names, IaC resource names, route prefixes -
  not generic labels like "Backend Service".
- **Model the real request path.** Edges should reflect observed calls. If a
  worker consumes from a queue, the edge runs queue -> worker, not app ->
  worker.

# Component kinds

Use exactly one of: {", ".join(k.value for k in ComponentKind)}

Guidance: `client` is the human user or calling system. `gateway` is anything
that terminates and routes inbound traffic (API Gateway, ALB, ingress, nginx).
`function` is serverless compute. `worker` is background/async compute.
`stream` is an append-only log (Kafka, Kinesis, Event Hubs); `queue` is
point-to-point or pub/sub messaging. `external` is a third-party SaaS API.
`infra` is for things that carry no traffic of their own - use it sparingly.

# Service keys (choose the icon)

`service` MUST be one of the keys below, exactly as spelled. Choose the most
specific key that the evidence supports. If nothing fits, use the generic key
for the role (`generic.service`, `generic.database`, `generic.storage`,
`generic.blank`).

{_service_catalog()}

# Clusters

Group components into clusters that a reader would recognise. Prefer real
boundaries when the evidence shows them (a VPC, a Kubernetes namespace, a
bounded context, an AWS account); otherwise fall back to tiers. Give every
cluster a `kind` of tier, vpc, subnet, namespace, account or region. A component
without a cluster renders at the top level, which is fine for a handful of
nodes but looks unstructured for many - cluster aggressively.

# Edge kinds

`sync` for request/response, `async` for events and messages, `data` for reads
and writes of persistent state, `deploy` for build-and-release relationships.

# Writing style

Descriptions are one sentence, concrete, and written for an engineer joining the
team. No marketing language, no "robust" or "scalable", no restating the
component's own name."""


FLOW_INSTRUCTIONS = """\
You now write Mermaid diagrams for the same repository.

# Rules for every diagram

- Mermaid must parse on the first try. Quote every label: `id["Label text"]`.
- Never put `[`, `]`, `{`, `}`, `|`, `(`, `)`, `"` or `#` inside a label. Replace
  them: write `GET /users/:id` not `GET /users/{id}`.
- Node ids are short, lowercase, alphanumeric with underscores. No spaces.
- Declare every node before you reference it in an edge.
- Keep each diagram under 30 nodes. If the real thing is bigger, show the
  important path and say so in the description.

# What to produce

Produce diagrams that explain *behaviour*, which the architecture diagram
cannot. Good candidates, in rough priority order:

1. **Request lifecycle** - a `sequenceDiagram` tracing one real, important
   endpoint from the evidence through every hop it actually makes. Name the
   real route and the real handler.
2. **A core domain workflow** - the business process this system exists to run
   (checkout, ingestion, provisioning, reconciliation). Use a `flowchart TD`
   with real decision points as `{"{"}diamonds{"}"}`, including the error and retry
   branches the code actually has.
3. **Data / event flow** - how a record or event moves between stores and
   topics, end to end.
4. **Startup or deployment sequence** - only if the evidence genuinely shows
   one.

Skip any of these the evidence cannot support. Three excellent diagrams beat six
generic ones. Do not reproduce the architecture diagram as a flowchart.

For styling, you may use these classes, which are predefined: `edge`, `app`,
`data`, `msg`, `ext`. Apply them with `class id1,id2 app;` as the last lines.
Do not write your own `classDef`."""


def _fmt_list(items: list[str], limit: int, prefix: str = "  - ") -> str:
    shown = items[:limit]
    out = "\n".join(f"{prefix}{item}" for item in shown)
    if len(items) > limit:
        out += f"\n{prefix}… and {len(items) - limit:,} more"
    return out


def evidence_pack(evidence: Evidence, baseline: Architecture) -> str:
    """Render the evidence + baseline as the cacheable context block.

    Ordering is fully deterministic - every list is either already sorted or
    sorted here - because a reordered list is a changed cache prefix.
    """
    parts: list[str] = []

    # -- Overview ----------------------------------------------------------
    languages = ", ".join(
        f"{lang} ({loc:,} LOC)" for lang, loc in list(evidence.languages.items())[:8] if loc
    ) or "not determined"
    parts.append(
        "# Repository\n\n"
        f"Name: {evidence.name}\n"
        f"Files analysed: {evidence.file_count:,}\n"
        f"Total lines of code: {evidence.total_loc:,}\n"
        f"Languages: {languages}\n"
        f"Dominant cloud (by signal weight): {evidence.primary_provider.value}"
    )

    if evidence.readme_excerpt.strip():
        parts.append("# README (excerpt)\n\n" + evidence.readme_excerpt[:3_000].strip())

    # -- Detected technologies ----------------------------------------------
    if evidence.signals:
        rows = [
            f"  - `{s.service}` — {s.label} [{s.kind.value}, confidence {s.confidence:.2f}]"
            f"\n      evidence: {'; '.join(s.evidence[:3])}"
            for s in evidence.signals
        ]
        parts.append("# Detected technologies\n\n" + "\n".join(rows[:90]))

    # -- Infrastructure as code ---------------------------------------------
    if evidence.iac:
        by_kind: dict[str, list[str]] = {}
        for res in evidence.iac:
            line = f"{res.resource_type} \"{res.name}\" ({res.file})"
            if res.service:
                line += f" -> {res.service}"
            if res.properties:
                interesting = {
                    k: v for k, v in res.properties.items()
                    if k in {"images", "image", "ports", "depends_on", "events",
                             "handler", "replicas", "service_type", "engine", "build"}
                }
                if interesting:
                    line += f" {json.dumps(interesting, default=str)[:200]}"
            by_kind.setdefault(res.kind, []).append(line)
        blocks = [
            f"## {kind}\n" + _fmt_list(sorted(lines), 60)
            for kind, lines in sorted(by_kind.items())
        ]
        parts.append("# Infrastructure as code\n\n" + "\n\n".join(blocks))

    # -- Deployable units ----------------------------------------------------
    if evidence.services:
        parts.append("# Candidate deployable units (directories owning a manifest "
                     "or Dockerfile)\n\n" + _fmt_list(evidence.services, 40))

    if evidence.entrypoints:
        parts.append("# Entrypoints\n\n" + _fmt_list(evidence.entrypoints, 30))

    # -- HTTP surface --------------------------------------------------------
    if evidence.routes:
        routes = sorted(
            {f"{r.method:<8} {r.path}  ->  {r.handler}  ({r.file}:{r.line}) [{r.framework}]"
             for r in evidence.routes}
        )
        parts.append(f"# HTTP routes ({len(evidence.routes)} found)\n\n"
                     + _fmt_list(routes, 120))

    # -- Internal structure ---------------------------------------------------
    if evidence.module_edges:
        edges = [f"{e.source} -> {e.target} ({e.weight} imports)" for e in evidence.module_edges]
        parts.append("# Internal module dependencies\n\n" + _fmt_list(edges, 60))

    # -- Key code entities ----------------------------------------------------
    documented = [e for e in evidence.entities if e.docstring]
    if documented:
        rows = sorted(
            f"{e.kind} `{e.name}` ({e.file}:{e.line})"
            + (f" — {e.docstring}" if e.docstring else "")
            + (f" calls: {', '.join(e.calls[:8])}" if e.calls else "")
            for e in documented
        )
        parts.append("# Documented classes and functions\n\n" + _fmt_list(rows, 100))

    # -- Dependencies ----------------------------------------------------------
    runtime_deps = sorted(
        {f"{d.name}{'==' + d.version if d.version else ''} [{d.ecosystem}]"
         for d in evidence.dependencies if not d.dev}
    )
    if runtime_deps:
        parts.append("# Runtime dependencies\n\n" + _fmt_list(runtime_deps, 150))

    # -- Baseline --------------------------------------------------------------
    baseline_payload = {
        "components": [
            {
                "id": c.id, "name": c.name, "kind": c.kind.value,
                "service": c.service, "cluster": c.cluster,
                "description": c.description, "paths": c.paths,
            }
            for c in baseline.components
        ],
        "edges": [
            {"source": e.source, "target": e.target, "label": e.label, "kind": e.kind.value}
            for e in baseline.edges
        ],
        "clusters": [{"id": c.id, "name": c.name, "kind": c.kind} for c in baseline.clusters],
    }
    parts.append(
        "# Mechanical baseline (correct this)\n\n"
        "Produced by rule-based inference. Treat it as a starting point: the set "
        "of components is usually close, the naming and the wiring usually are "
        "not.\n\n```json\n"
        + json.dumps(baseline_payload, indent=2)[:14_000]
        + "\n```"
    )

    if evidence.notes:
        parts.append("# Scanner notes\n\n" + _fmt_list(sorted(evidence.notes), 10))

    pack = "\n\n---\n\n".join(parts)
    if len(pack) > MAX_PACK_CHARS:
        pack = pack[:MAX_PACK_CHARS] + "\n\n[evidence truncated to fit the context budget]"
    return pack


ARCHITECTURE_TASK = """\
Produce the architecture model for this repository.

Work through it in this order:
1. Decide what the system actually is, and what its deployable components are.
2. Assign each component the most specific service key the evidence supports.
3. Group components into clusters a reader would recognise.
4. Wire the edges to match how the code really calls out, labelling each with
   the protocol or operation (`HTTPS`, `gRPC`, `publish`, `SQL`, `read/write`).
5. Write the summary: what this system does and how it is put together, in
   three or four sentences, for an engineer who has never seen it.

Then list the highlights: the three to six things about this architecture that a
reviewer should actually notice. Real observations only - coupling, single
points of failure, an unusual choice, a missing layer, a service used in a way
its name would not suggest. No filler."""


FLOW_TASK = """\
Produce the Mermaid diagrams for this repository, following the rules you were
given. Return three to five diagrams, ordered most important first."""
