"""Claude-powered semantic inference.

The static layer knows *what* is in a repository. This layer decides what it
*means*: which pieces are really one service, what the team calls them, and how
requests actually flow. It refines the static baseline rather than replacing it,
and every failure mode degrades to that baseline instead of breaking the scan.

Cost control is structural, not incidental: the frozen instruction block and the
per-repository evidence block are both cached, so the second call in a scan
(flows) reads nearly all of its input from cache.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from ..models import (
    Architecture,
    Cluster,
    Component,
    ComponentKind,
    Edge,
    EdgeKind,
    Evidence,
    Flow,
)
from ..render.icons import ICON_REGISTRY
from .heuristics import build_tech_stack, slug
from .prompts import (
    ARCHITECTURE_TASK,
    FLOW_INSTRUCTIONS,
    FLOW_TASK,
    SYSTEM_INSTRUCTIONS,
    evidence_pack,
)

log = logging.getLogger(__name__)

MODEL = "claude-opus-5"
MAX_TOKENS = 16_000


# --------------------------------------------------------------------------- #
# Response schemas
#
# Deliberately loose (`str` rather than enums) so a near-miss from the model is
# coerced in `_coerce_*` instead of failing schema validation and losing the
# whole response.
# --------------------------------------------------------------------------- #


class LlmComponent(BaseModel):
    id: str = Field(description="Short stable identifier, lowercase with underscores")
    name: str = Field(description="Display name, as the team would say it")
    kind: str = Field(description="One of the listed component kinds")
    service: str = Field(description="A service key from the catalog, spelled exactly")
    cluster: str | None = Field(default=None, description="Id of the cluster this belongs to")
    description: str = Field(default="", description="One concrete sentence")
    technologies: list[str] = Field(default_factory=list)
    paths: list[str] = Field(default_factory=list,
                             description="Files that justify this component's existence")


class LlmEdge(BaseModel):
    source: str
    target: str
    label: str = Field(default="", description="Protocol or operation, e.g. HTTPS, publish, SQL")
    kind: str = Field(default="sync", description="sync, async, data or deploy")


class LlmCluster(BaseModel):
    id: str
    name: str
    kind: str = Field(default="tier", description="tier, vpc, subnet, namespace, account or region")
    parent: str | None = None


class LlmArchitecture(BaseModel):
    """The architecture model Claude returns."""

    summary: str = Field(description="Three to four sentences on what this system is")
    clusters: list[LlmCluster]
    components: list[LlmComponent]
    edges: list[LlmEdge]
    highlights: list[str] = Field(description="Three to six real observations for a reviewer")


class LlmFlow(BaseModel):
    id: str = Field(description="Short slug, lowercase with hyphens")
    title: str
    description: str = Field(description="One or two sentences on what this diagram shows")
    diagram_type: str = Field(description="flowchart, sequence, state or er")
    mermaid: str = Field(description="Complete, valid Mermaid source")


class LlmFlows(BaseModel):
    flows: list[LlmFlow]


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #


@dataclass
class LlmResult:
    architecture: Architecture | None = None
    flows: list[Flow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0


def api_key_available() -> bool:
    """Whether the Anthropic SDK will find a credential.

    An unset `ANTHROPIC_API_KEY` does not imply "no credentials" - the SDK also
    reads `ANTHROPIC_AUTH_TOKEN` and an `ant auth login` profile - so we only
    treat it as unavailable when none of those are present.
    """
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    profile_dir = os.path.join(os.path.expanduser("~"), ".config", "anthropic")
    return os.path.isdir(profile_dir)


def sdk_available() -> bool:
    try:
        import anthropic  # noqa: F401, PLC0415
    except ImportError:
        return False
    return True


def enrich(evidence: Evidence, baseline: Architecture, *,
           progress=None, want_flows: bool = True) -> LlmResult:
    """Run the Claude passes. Never raises - failures come back as warnings."""
    result = LlmResult()

    try:
        import anthropic
    except ImportError:
        result.warnings.append("The `anthropic` package is not installed; used static analysis only.")
        return result

    def report(message: str, fraction: float) -> None:
        if progress:
            progress(message, fraction)

    try:
        client = anthropic.Anthropic(max_retries=3)
    except Exception as exc:  # pragma: no cover - misconfiguration
        result.warnings.append(f"Could not create the Anthropic client: {exc}")
        return result

    pack = evidence_pack(evidence, baseline)

    # Two cache breakpoints: the frozen instructions (shared across every scan
    # this machine ever runs) and the evidence (shared across this scan's calls).
    def system_blocks(extra_instructions: str | None = None) -> list[dict]:
        blocks: list[dict] = [
            {
                "type": "text",
                "text": SYSTEM_INSTRUCTIONS,
                "cache_control": {"type": "ephemeral"},
            },
            {
                "type": "text",
                "text": pack,
                "cache_control": {"type": "ephemeral"},
            },
        ]
        if extra_instructions:
            blocks.append({"type": "text", "text": extra_instructions})
        return blocks

    def record_usage(response) -> None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        result.input_tokens += getattr(usage, "input_tokens", 0) or 0
        result.output_tokens += getattr(usage, "output_tokens", 0) or 0
        result.cached_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0

    # ---- Pass 1: architecture ---------------------------------------------
    report("Claude is modelling the architecture", 0.84)
    try:
        response = client.messages.parse(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},
            system=system_blocks(),
            messages=[{"role": "user", "content": ARCHITECTURE_TASK}],
            output_format=LlmArchitecture,
        )
        record_usage(response)
        if response.parsed_output is not None:
            result.architecture = _to_architecture(
                response.parsed_output, evidence, baseline
            )
        else:
            result.warnings.append("Claude returned no parsable architecture; kept the static model.")
    except Exception as exc:
        log.warning("Architecture pass failed", exc_info=True)
        result.warnings.append(f"Claude architecture pass failed ({_describe(exc)}); "
                               "kept the static model.")

    # ---- Pass 2: flows ------------------------------------------------------
    if want_flows:
        report("Claude is drawing the flowcharts", 0.92)
        try:
            response = client.messages.parse(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                thinking={"type": "adaptive"},
                system=system_blocks(FLOW_INSTRUCTIONS),
                messages=[{"role": "user", "content": FLOW_TASK}],
                output_format=LlmFlows,
            )
            record_usage(response)
            if response.parsed_output is not None:
                result.flows = [
                    Flow(
                        id=slug(flow.id or flow.title, "flow") or f"flow-{index}",
                        title=flow.title.strip() or f"Flow {index + 1}",
                        description=flow.description.strip(),
                        diagram_type=_coerce_diagram_type(flow.diagram_type, flow.mermaid),
                        mermaid=_clean_mermaid(flow.mermaid),
                    )
                    for index, flow in enumerate(response.parsed_output.flows)
                    if flow.mermaid.strip()
                ]
        except Exception as exc:
            log.warning("Flow pass failed", exc_info=True)
            result.warnings.append(f"Claude flowchart pass failed ({_describe(exc)}); "
                                   "used generated flowcharts instead.")

    return result


def _describe(exc: Exception) -> str:
    """A short, user-facing reason for a failure."""
    try:
        import anthropic
    except ImportError:
        return type(exc).__name__

    if isinstance(exc, anthropic.AuthenticationError):
        return "invalid or missing API key"
    if isinstance(exc, anthropic.PermissionDeniedError):
        return "API key lacks permission"
    if isinstance(exc, anthropic.RateLimitError):
        return "rate limited"
    if isinstance(exc, anthropic.BadRequestError):
        return f"bad request: {getattr(exc, 'message', str(exc))[:160]}"
    if isinstance(exc, anthropic.APIConnectionError):
        return "network error"
    if isinstance(exc, anthropic.APIStatusError):
        return f"API error {exc.status_code}"
    return f"{type(exc).__name__}: {str(exc)[:120]}"


# --------------------------------------------------------------------------- #
# Coercion - turn a loose LLM response into strict domain objects
# --------------------------------------------------------------------------- #

_KIND_ALIASES: dict[str, ComponentKind] = {
    "api": ComponentKind.GATEWAY,
    "api_gateway": ComponentKind.GATEWAY,
    "loadbalancer": ComponentKind.GATEWAY,
    "load_balancer": ComponentKind.GATEWAY,
    "ingress": ComponentKind.GATEWAY,
    "ui": ComponentKind.FRONTEND,
    "web": ComponentKind.FRONTEND,
    "spa": ComponentKind.FRONTEND,
    "user": ComponentKind.CLIENT,
    "users": ComponentKind.CLIENT,
    "serverless": ComponentKind.FUNCTION,
    "lambda": ComponentKind.FUNCTION,
    "job": ComponentKind.WORKER,
    "batch": ComponentKind.WORKER,
    "consumer": ComponentKind.WORKER,
    "topic": ComponentKind.QUEUE,
    "messaging": ComponentKind.QUEUE,
    "eventbus": ComponentKind.QUEUE,
    "db": ComponentKind.DATABASE,
    "datastore": ComponentKind.DATABASE,
    "blob": ComponentKind.STORAGE,
    "objectstore": ComponentKind.STORAGE,
    "bucket": ComponentKind.STORAGE,
    "thirdparty": ComponentKind.EXTERNAL,
    "third_party": ComponentKind.EXTERNAL,
    "saas": ComponentKind.EXTERNAL,
    "observability": ComponentKind.MONITORING,
    "logging": ComponentKind.MONITORING,
    "pipeline": ComponentKind.CICD,
    "network": ComponentKind.INFRA,
}


def _coerce_kind(raw: str) -> ComponentKind:
    value = (raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    try:
        return ComponentKind(value)
    except ValueError:
        return _KIND_ALIASES.get(value, ComponentKind.SERVICE)


def _coerce_edge_kind(raw: str) -> EdgeKind:
    value = (raw or "").strip().lower()
    aliases = {
        "asynchronous": "async", "event": "async", "events": "async",
        "synchronous": "sync", "http": "sync", "https": "sync", "rpc": "sync",
        "read": "data", "write": "data", "persistence": "data", "storage": "data",
        "build": "deploy", "ci": "deploy",
    }
    value = aliases.get(value, value)
    try:
        return EdgeKind(value)
    except ValueError:
        return EdgeKind.SYNC


def _coerce_service(raw: str, kind: ComponentKind) -> str:
    """Snap the model's service key onto the registry.

    Exact match wins. Otherwise we try a light normalisation, then give up and
    let the icon resolver apply its kind-based fallback.
    """
    key = (raw or "").strip()
    if key in ICON_REGISTRY:
        return key
    normalised = key.lower().replace("-", "").replace(" ", "").replace("_", "")
    for candidate in ICON_REGISTRY:
        if candidate.lower().replace("-", "").replace("_", "") == normalised:
            return candidate
    # `aws.dynamo` -> `aws.dynamodb`: same provider, prefix match.
    if "." in key:
        provider, _, tail = key.lower().partition(".")
        best = ""
        for candidate in ICON_REGISTRY:
            cand_provider, _, cand_tail = candidate.partition(".")
            matches = cand_provider == provider and tail and cand_tail.startswith(tail)
            if matches and (not best or len(candidate) < len(best)):
                best = candidate
        if best:
            return best
    return f"generic.{kind.value}" if f"generic.{kind.value}" in ICON_REGISTRY else "generic.service"


def _coerce_diagram_type(raw: str, mermaid: str) -> str:
    value = (raw or "").strip().lower()
    head = mermaid.strip().split("\n", 1)[0].lower()
    # The source is more trustworthy than the label.
    if head.startswith("sequencediagram"):
        return "sequence"
    if head.startswith("statediagram"):
        return "state"
    if head.startswith("erdiagram"):
        return "er"
    if head.startswith(("flowchart", "graph")):
        return "flowchart"
    return value if value in {"flowchart", "sequence", "state", "er"} else "flowchart"


def _clean_mermaid(source: str) -> str:
    """Strip markdown fencing the model sometimes adds around the diagram."""
    text = source.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _to_architecture(draft: LlmArchitecture, evidence: Evidence,
                     baseline: Architecture) -> Architecture:
    """Validate and normalise the model's output into an `Architecture`."""
    arch = Architecture(
        name=evidence.name,
        summary=draft.summary.strip(),
        provider=evidence.primary_provider,
        generated_by="claude",
        highlights=[h.strip() for h in draft.highlights if h.strip()][:8],
        tech_stack=build_tech_stack(evidence),
    )

    cluster_ids: set[str] = set()
    for raw in draft.clusters:
        cid = slug(raw.id or raw.name, "cluster")
        if not cid or cid in cluster_ids:
            continue
        cluster_ids.add(cid)
        arch.clusters.append(Cluster(
            id=cid,
            name=raw.name.strip() or cid,
            kind=raw.kind.strip().lower() or "tier",
            parent=slug(raw.parent, "cluster") if raw.parent else None,
        ))
    # A parent pointing at a cluster that was dropped would orphan the subgraph.
    for cluster in arch.clusters:
        if cluster.parent and cluster.parent not in cluster_ids:
            cluster.parent = None

    id_map: dict[str, str] = {}  # model's id -> our sanitised id
    taken: set[str] = set()
    for raw in draft.components:
        base_id = slug(raw.id or raw.name, "c")
        component_id = base_id
        suffix = 2
        while component_id in taken:
            component_id = f"{base_id}_{suffix}"
            suffix += 1
        taken.add(component_id)
        id_map[raw.id] = component_id
        id_map.setdefault(base_id, component_id)

        kind = _coerce_kind(raw.kind)
        cluster = slug(raw.cluster, "cluster") if raw.cluster else None
        arch.components.append(Component(
            id=component_id,
            name=raw.name.strip()[:60] or component_id,
            kind=kind,
            service=_coerce_service(raw.service, kind),
            cluster=cluster if cluster in cluster_ids else None,
            description=raw.description.strip()[:280],
            technologies=[t.strip() for t in raw.technologies if t.strip()][:8],
            paths=[p.strip() for p in raw.paths if p.strip()][:8],
            external=kind is ComponentKind.EXTERNAL,
        ))

    for raw in draft.edges:
        source = id_map.get(raw.source) or id_map.get(slug(raw.source, "c"))
        target = id_map.get(raw.target) or id_map.get(slug(raw.target, "c"))
        if not source or not target or source == target:
            continue
        arch.edges.append(Edge(
            source=source, target=target,
            label=raw.label.strip()[:40],
            kind=_coerce_edge_kind(raw.kind),
        ))

    arch.prune_dangling_edges()

    # A response with nothing usable in it is worse than the baseline.
    if len(arch.components) < 2:
        baseline.summary = arch.summary or baseline.summary
        baseline.highlights = arch.highlights or baseline.highlights
        return baseline

    # Drop clusters that ended up empty - Graphviz renders them as odd blanks.
    used_clusters = {c.cluster for c in arch.components if c.cluster}
    arch.clusters = [c for c in arch.clusters if c.id in used_clusters]

    return arch
