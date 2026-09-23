"""Core data model shared by the scanner, the inference layer and the renderers.

The whole pipeline is a funnel:

    Evidence   (raw, factual, produced by static analysis - never invented)
       |
       v
    Architecture  (interpreted: components, clusters, edges)
       |
       v
    Diagrams / Flows  (rendered artifacts)

Keeping `Evidence` strictly factual is what lets the LLM stage be audited: every
component in the final architecture should be traceable back to a file on disk.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #


class Provider(str, Enum):
    """Which icon family a component is drawn from."""

    AWS = "aws"
    AZURE = "azure"
    GCP = "gcp"
    K8S = "k8s"
    ONPREM = "onprem"
    SAAS = "saas"
    PROGRAMMING = "programming"
    GENERIC = "generic"


class ComponentKind(str, Enum):
    """Coarse role of a component. Drives styling and layout rank."""

    CLIENT = "client"
    CDN = "cdn"
    GATEWAY = "gateway"
    FRONTEND = "frontend"
    SERVICE = "service"
    FUNCTION = "function"
    WORKER = "worker"
    QUEUE = "queue"
    STREAM = "stream"
    DATABASE = "database"
    CACHE = "cache"
    STORAGE = "storage"
    SEARCH = "search"
    ANALYTICS = "analytics"
    ML = "ml"
    AUTH = "auth"
    MONITORING = "monitoring"
    CICD = "cicd"
    EXTERNAL = "external"
    INFRA = "infra"


class EdgeKind(str, Enum):
    """How two components talk. Drives line style."""

    SYNC = "sync"  # request/response - solid line
    ASYNC = "async"  # events/messages - dashed line
    DATA = "data"  # reads/writes persistent state - solid, muted
    DEPLOY = "deploy"  # build/deploy relationship - dotted


# Rendering hints per kind: (rank tier, accent colour).
# Lower tier = closer to the user, drawn further left/top.
KIND_TIER: dict[ComponentKind, int] = {
    ComponentKind.CLIENT: 0,
    ComponentKind.CDN: 1,
    ComponentKind.GATEWAY: 2,
    ComponentKind.AUTH: 2,
    ComponentKind.FRONTEND: 3,
    ComponentKind.SERVICE: 4,
    ComponentKind.FUNCTION: 4,
    ComponentKind.WORKER: 5,
    ComponentKind.QUEUE: 5,
    ComponentKind.STREAM: 5,
    ComponentKind.CACHE: 6,
    ComponentKind.DATABASE: 7,
    ComponentKind.SEARCH: 7,
    ComponentKind.STORAGE: 7,
    ComponentKind.ANALYTICS: 8,
    ComponentKind.ML: 8,
    ComponentKind.EXTERNAL: 9,
    ComponentKind.MONITORING: 9,
    ComponentKind.CICD: 9,
    ComponentKind.INFRA: 9,
}


# --------------------------------------------------------------------------- #
# Evidence - the factual layer
# --------------------------------------------------------------------------- #


class FileInfo(BaseModel):
    path: str  # repo-relative, forward slashes
    language: str
    loc: int = 0
    size: int = 0


class Dependency(BaseModel):
    name: str
    version: str | None = None
    ecosystem: str  # pypi | npm | maven | gomod | nuget | rubygems | cargo
    source: str  # the manifest it came from
    dev: bool = False


# The confidence a signal must reach before it becomes a diagram component.
# Manifest, IaC and import evidence all score above it; raw content probes
# score below, so they can corroborate a finding but never originate one.
MIN_COMPONENT_CONFIDENCE = 0.7


class Signal(BaseModel):
    """A detected technology, with the evidence that produced it.

    `service` is a canonical key (``aws.lambda``, ``onprem.postgresql``) that the
    icon registry understands. `evidence` is a human-readable reason so the UI
    can explain *why* something appears in the diagram.
    """

    service: str
    label: str
    kind: ComponentKind
    confidence: float = Field(ge=0.0, le=1.0, default=0.7)
    evidence: list[str] = Field(default_factory=list)
    paths: list[str] = Field(default_factory=list)

    def merge(self, other: Signal) -> None:
        self.confidence = max(self.confidence, other.confidence)
        for e in other.evidence:
            if e not in self.evidence:
                self.evidence.append(e)
        for p in other.paths:
            if p not in self.paths:
                self.paths.append(p)


class Route(BaseModel):
    """An HTTP entrypoint found in the code."""

    method: str
    path: str
    handler: str
    file: str
    line: int = 0
    framework: str = ""


class CodeEntity(BaseModel):
    """A class or function worth putting on a flowchart."""

    name: str
    kind: str  # class | function | method
    file: str
    line: int = 0
    calls: list[str] = Field(default_factory=list)
    decorators: list[str] = Field(default_factory=list)
    docstring: str | None = None
    is_async: bool = False


class IacResource(BaseModel):
    """A resource declared in infrastructure-as-code."""

    kind: str  # terraform | cloudformation | k8s | compose | serverless | bicep
    resource_type: str  # e.g. aws_lambda_function, Microsoft.Web/sites
    name: str
    file: str
    service: str | None = None  # canonical key, when we recognise it
    properties: dict[str, Any] = Field(default_factory=dict)


class ModuleEdge(BaseModel):
    """Internal import edge between two first-party modules."""

    source: str
    target: str
    weight: int = 1


class Evidence(BaseModel):
    """Everything static analysis could prove about a repository."""

    root: str
    name: str
    file_count: int = 0
    total_loc: int = 0
    languages: dict[str, int] = Field(default_factory=dict)  # language -> LOC
    files: list[FileInfo] = Field(default_factory=list)
    dependencies: list[Dependency] = Field(default_factory=list)
    signals: list[Signal] = Field(default_factory=list)
    routes: list[Route] = Field(default_factory=list)
    entities: list[CodeEntity] = Field(default_factory=list)
    iac: list[IacResource] = Field(default_factory=list)
    module_edges: list[ModuleEdge] = Field(default_factory=list)
    entrypoints: list[str] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)  # monorepo sub-services
    readme_excerpt: str = ""
    notes: list[str] = Field(default_factory=list)

    @property
    def primary_language(self) -> str:
        if not self.languages:
            return "unknown"
        return max(self.languages.items(), key=lambda kv: kv[1])[0]

    @property
    def primary_provider(self) -> Provider:
        """Whichever cloud has the most signal wins the diagram's icon family.

        Only signals at or above :data:`MIN_COMPONENT_CONFIDENCE` count - a
        stray regex hit should never decide that a project "runs on AWS".
        """
        tally: dict[str, float] = {}
        for sig in self.signals:
            if sig.confidence < MIN_COMPONENT_CONFIDENCE:
                continue
            provider = sig.service.split(".", 1)[0]
            if provider in {"aws", "azure", "gcp"}:
                tally[provider] = tally.get(provider, 0.0) + sig.confidence
        if not tally:
            return Provider.ONPREM
        return Provider(max(tally.items(), key=lambda kv: kv[1])[0])


# --------------------------------------------------------------------------- #
# Architecture - the interpreted layer
# --------------------------------------------------------------------------- #


class Component(BaseModel):
    id: str
    name: str
    kind: ComponentKind = ComponentKind.SERVICE
    service: str = "generic.service"  # canonical key -> icon
    cluster: str | None = None
    description: str = ""
    technologies: list[str] = Field(default_factory=list)
    paths: list[str] = Field(default_factory=list)
    external: bool = False


class Edge(BaseModel):
    source: str
    target: str
    label: str = ""
    kind: EdgeKind = EdgeKind.SYNC


class Cluster(BaseModel):
    """A visual grouping box (VPC, subnet, tier, bounded context)."""

    id: str
    name: str
    parent: str | None = None
    kind: str = "tier"  # tier | vpc | subnet | account | region | namespace


class Flow(BaseModel):
    """A flowchart or sequence diagram, expressed as Mermaid source."""

    id: str
    title: str
    description: str = ""
    diagram_type: str = "flowchart"  # flowchart | sequence | state | er
    mermaid: str = ""


class Architecture(BaseModel):
    name: str
    summary: str = ""
    provider: Provider = Provider.ONPREM
    clusters: list[Cluster] = Field(default_factory=list)
    components: list[Component] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    flows: list[Flow] = Field(default_factory=list)
    tech_stack: dict[str, list[str]] = Field(default_factory=dict)
    highlights: list[str] = Field(default_factory=list)
    generated_by: str = "static"  # static | claude

    def component_ids(self) -> set[str]:
        return {c.id for c in self.components}

    def prune_dangling_edges(self) -> None:
        """Drop edges pointing at components that do not exist.

        The LLM occasionally references an id it did not declare; silently
        dropping those is better than crashing the renderer.
        """
        ids = self.component_ids()
        self.edges = [e for e in self.edges if e.source in ids and e.target in ids]


# --------------------------------------------------------------------------- #
# Pipeline result
# --------------------------------------------------------------------------- #


class RenderedDiagram(BaseModel):
    title: str
    png: str | None = None  # path relative to the job output dir
    svg: str | None = None
    error: str | None = None


class ScanResult(BaseModel):
    job_id: str
    root: str
    architecture: Architecture
    evidence: Evidence
    diagrams: list[RenderedDiagram] = Field(default_factory=list)
    duration_seconds: float = 0.0
    used_llm: bool = False
    warnings: list[str] = Field(default_factory=list)
