"""Static inference: evidence -> a defensible architecture, with no LLM.

This runs on every scan. When Claude is enabled it also becomes the *baseline*
handed to the model, which keeps the LLM anchored to things that actually exist
on disk instead of inventing a plausible-looking system.
"""

from __future__ import annotations

import re
from collections import defaultdict

from ..models import (
    MIN_COMPONENT_CONFIDENCE,
    Architecture,
    Cluster,
    Component,
    Edge,
    EdgeKind,
    Evidence,
    Provider,
    Signal,
)
from ..models import (
    ComponentKind as K,
)

# Tier clusters, in the order they should appear left-to-right.
TIER_EDGE = "edge"
TIER_APP = "app"
TIER_MESSAGING = "messaging"
TIER_DATA = "data"
TIER_EXTERNAL = "external"
TIER_OPS = "ops"

TIER_NAMES: dict[str, str] = {
    TIER_EDGE: "Edge & Delivery",
    TIER_APP: "Application Tier",
    TIER_MESSAGING: "Messaging & Events",
    TIER_DATA: "Data Tier",
    TIER_EXTERNAL: "External Services",
    TIER_OPS: "Platform & Operations",
}

KIND_TO_TIER: dict[K, str] = {
    K.CLIENT: TIER_EDGE,
    K.CDN: TIER_EDGE,
    K.GATEWAY: TIER_EDGE,
    K.AUTH: TIER_EDGE,
    K.FRONTEND: TIER_EDGE,
    K.SERVICE: TIER_APP,
    K.FUNCTION: TIER_APP,
    K.WORKER: TIER_APP,
    K.QUEUE: TIER_MESSAGING,
    K.STREAM: TIER_MESSAGING,
    K.DATABASE: TIER_DATA,
    K.CACHE: TIER_DATA,
    K.STORAGE: TIER_DATA,
    K.SEARCH: TIER_DATA,
    K.ANALYTICS: TIER_DATA,
    K.ML: TIER_EXTERNAL,
    K.EXTERNAL: TIER_EXTERNAL,
    K.MONITORING: TIER_OPS,
    K.CICD: TIER_OPS,
    K.INFRA: TIER_OPS,
}

# Signals that describe *how* code is built rather than a runtime component.
# They belong in the tech stack panel, not as diagram nodes.
NON_COMPONENT_SERVICES = {
    "aws.sdk", "generic.http", "generic.dataframe", "generic.bundler",
    "onprem.terraform", "onprem.helm", "azure.arm", "aws.cloudformation",
    "generic.ml", "generic.llmframework", "aws.iam", "generic.otel",
}

# Compute services - a signal here means "the app runs on this", so it should
# decorate the application node rather than sit beside it.
COMPUTE_SERVICES = {
    "aws.ec2", "aws.ecs", "aws.eks", "aws.fargate", "aws.beanstalk", "aws.apprunner",
    "azure.vm", "azure.vmss", "azure.appservice", "azure.aks", "azure.containerapps",
    "azure.containerinstances", "gcp.computeengine", "gcp.run", "gcp.gke", "gcp.appengine",
    "k8s.deployment", "k8s.pod", "k8s.statefulset", "k8s.replicaset", "k8s.daemonset",
}

# Container *hosts* rather than workloads. An ECS cluster is where services run;
# drawing it beside its own services says nothing and doubles the node count.
HOST_RESOURCE_TYPES = {
    "aws_ecs_cluster", "aws_eks_cluster", "aws_autoscaling_group", "aws_launch_template",
    "azurerm_kubernetes_cluster", "azurerm_service_plan", "azurerm_app_service_plan",
    "google_container_cluster", "aws_batch_compute_environment",
}

# A managed service and the engine it runs are the same box on a diagram.
# When the left-hand key is present, the right-hand keys are redundant.
REDUNDANT_WHEN_PRESENT: dict[str, set[str]] = {
    "aws.aurora": {"onprem.postgresql", "onprem.mysql", "onprem.sql", "onprem.mariadb"},
    "aws.rds": {"onprem.postgresql", "onprem.mysql", "onprem.sql", "onprem.mariadb",
                "onprem.mssql", "onprem.oracle"},
    "aws.elasticache": {"onprem.redis", "onprem.memcached"},
    "aws.documentdb": {"onprem.mongodb"},
    "aws.opensearch": {"onprem.elasticsearch"},
    "aws.msk": {"onprem.kafka"},
    "aws.mq": {"onprem.rabbitmq", "onprem.activemq"},
    "azure.postgresql": {"onprem.postgresql", "onprem.sql"},
    "azure.mysql": {"onprem.mysql", "onprem.sql"},
    "azure.sqldatabase": {"onprem.mssql", "onprem.sql"},
    "azure.sqlserver": {"onprem.mssql", "onprem.sql"},
    "azure.redis": {"onprem.redis"},
    "azure.cosmosdb": {"onprem.mongodb"},
    "azure.eventhubs": {"onprem.kafka"},
    "azure.search": {"onprem.elasticsearch"},
    "gcp.sql": {"onprem.postgresql", "onprem.mysql", "onprem.sql"},
    "gcp.memorystore": {"onprem.redis"},
    "gcp.firestore": {"onprem.mongodb"},
    # A concrete engine beats the ORM-derived generic.
    "onprem.postgresql": {"onprem.sql"},
    "onprem.mysql": {"onprem.sql"},
    "onprem.mssql": {"onprem.sql"},
    "onprem.oracle": {"onprem.sql"},
    "onprem.mariadb": {"onprem.sql"},
}

# Beyond this, connecting every app to every store produces an unreadable mesh.
# We wire the primary services fully and leave the rest to the LLM pass, which
# can tell which service actually touches which store.
MAX_FULLY_WIRED_APPS = 3

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slug(text: str, prefix: str = "n") -> str:
    """Graphviz- and Mermaid-safe identifier."""
    cleaned = _SLUG_RE.sub("_", text.strip().lower()).strip("_")
    if not cleaned:
        cleaned = "node"
    if cleaned[0].isdigit():
        cleaned = f"{prefix}_{cleaned}"
    return cleaned[:48]


def _unique(base: str, taken: set[str]) -> str:
    candidate = base
    suffix = 2
    while candidate in taken:
        candidate = f"{base}_{suffix}"
        suffix += 1
    taken.add(candidate)
    return candidate


def build_architecture(evidence: Evidence) -> Architecture:
    """Derive a component/edge model from static evidence alone."""
    provider = evidence.primary_provider
    arch = Architecture(
        name=evidence.name,
        provider=provider,
        generated_by="static",
    )

    taken_ids: set[str] = set()
    components: dict[str, Component] = {}
    # Weak signals stay in the evidence trail but do not become boxes.
    signals_by_service = _drop_redundant({
        s.service: s for s in evidence.signals
        if s.confidence >= MIN_COMPONENT_CONFIDENCE
    })

    def add(component: Component) -> Component:
        component.id = _unique(component.id, taken_ids)
        components[component.id] = component
        return component

    # ---- 1. The user -------------------------------------------------------
    has_http = bool(evidence.routes) or any(
        s.kind in {K.GATEWAY, K.FRONTEND, K.CDN} for s in evidence.signals
    )
    users = add(Component(
        id="users", name="Users", kind=K.CLIENT, service="generic.user",
        cluster=TIER_EDGE, description="End users of the system",
    )) if has_http else None

    # ---- 2. Edge tier ------------------------------------------------------
    edge_nodes: list[Component] = []
    for service, signal in signals_by_service.items():
        if signal.kind in {K.CDN, K.GATEWAY} and service not in NON_COMPONENT_SERVICES:
            edge_nodes.append(add(Component(
                id=slug(signal.label), name=signal.label, kind=signal.kind,
                service=service, cluster=TIER_EDGE,
                description=signal.evidence[0] if signal.evidence else "",
                paths=signal.paths[:5],
            )))

    frontend_nodes: list[Component] = []
    for service, signal in signals_by_service.items():
        if signal.kind is K.FRONTEND:
            frontend_nodes.append(add(Component(
                id=slug(signal.label), name=signal.label, kind=K.FRONTEND,
                service=service, cluster=TIER_EDGE,
                description="Client application",
                paths=signal.paths[:5],
            )))

    auth_nodes = [
        add(Component(
            id=slug(sig.label), name=sig.label, kind=K.AUTH, service=svc,
            cluster=TIER_EDGE, description="Authentication / secrets",
            paths=sig.paths[:5],
        ))
        for svc, sig in signals_by_service.items()
        if sig.kind is K.AUTH and svc not in NON_COMPONENT_SERVICES
    ]

    # ---- 3. Application tier ----------------------------------------------
    app_nodes = _build_app_nodes(evidence, signals_by_service, add)

    # ---- 4. Messaging ------------------------------------------------------
    messaging_nodes = [
        add(Component(
            id=slug(sig.label), name=sig.label, kind=sig.kind, service=svc,
            cluster=TIER_MESSAGING, description=_first_evidence(sig),
            paths=sig.paths[:5],
        ))
        for svc, sig in signals_by_service.items()
        if sig.kind in {K.QUEUE, K.STREAM}
    ]

    # ---- 5. Data tier ------------------------------------------------------
    data_nodes = [
        add(Component(
            id=slug(sig.label), name=sig.label, kind=sig.kind, service=svc,
            cluster=TIER_DATA, description=_first_evidence(sig),
            paths=sig.paths[:5],
        ))
        for svc, sig in signals_by_service.items()
        if sig.kind in {K.DATABASE, K.CACHE, K.STORAGE, K.SEARCH, K.ANALYTICS}
        and svc not in NON_COMPONENT_SERVICES
    ]

    # ---- 6. External -------------------------------------------------------
    external_nodes = [
        add(Component(
            id=slug(sig.label), name=sig.label, kind=K.EXTERNAL, service=svc,
            cluster=TIER_EXTERNAL, description=_first_evidence(sig),
            paths=sig.paths[:5], external=True,
        ))
        for svc, sig in signals_by_service.items()
        if sig.kind in {K.EXTERNAL, K.ML} and svc not in NON_COMPONENT_SERVICES
    ]

    # ---- 7. Ops (only when there is real signal) ---------------------------
    ops_nodes = [
        add(Component(
            id=slug(sig.label), name=sig.label, kind=sig.kind, service=svc,
            cluster=TIER_OPS, description=_first_evidence(sig), paths=sig.paths[:5],
        ))
        for svc, sig in signals_by_service.items()
        if sig.kind in {K.MONITORING, K.CICD} and svc not in NON_COMPONENT_SERVICES
    ]

    # ---- 8. Wire it together -----------------------------------------------
    edges: list[Edge] = []

    def link(src: Component | None, dst: Component | None, label: str = "",
             kind: EdgeKind = EdgeKind.SYNC) -> None:
        if src is None or dst is None or src.id == dst.id:
            return
        edges.append(Edge(source=src.id, target=dst.id, label=label, kind=kind))

    # The request path: users -> [cdn/frontend] -> gateway -> app
    entry_points = edge_nodes or frontend_nodes
    cdns = [n for n in edge_nodes if n.kind is K.CDN]
    gateways = [n for n in edge_nodes if n.kind is K.GATEWAY]

    if users:
        for node in (cdns or gateways or frontend_nodes or app_nodes[:1]):
            link(users, node, "HTTPS")

    for cdn in cdns:
        for frontend in frontend_nodes:
            link(cdn, frontend, "serves")
        if not frontend_nodes:
            for gateway in gateways:
                link(cdn, gateway)

    for frontend in frontend_nodes:
        for gateway in (gateways or app_nodes[:1]):
            link(frontend, gateway, "API calls")

    # Workers are reached through a queue, never routed to by a load balancer.
    workers = [n for n in app_nodes if n.kind is K.WORKER]
    request_apps = [n for n in app_nodes if n.kind is not K.WORKER]

    for gateway in gateways:
        for app in request_apps[:6]:
            link(gateway, app, "routes")

    # If nothing sits in front, users hit the app directly.
    if users and not entry_points:
        for app in request_apps[:2]:
            link(users, app, "HTTPS")

    for auth in auth_nodes:
        for app in request_apps[:2]:
            link(app, auth, "verify", EdgeKind.SYNC)

    # Wiring every service to every store is technically defensible and
    # visually useless. Without per-service evidence of which store it touches,
    # only the primary services get fully wired.
    for app in request_apps[:MAX_FULLY_WIRED_APPS]:
        for store in data_nodes:
            verb = {
                K.CACHE: "cache", K.STORAGE: "read/write objects",
                K.SEARCH: "query", K.ANALYTICS: "load",
            }.get(store.kind, "read/write")
            link(app, store, verb, EdgeKind.DATA)
        for queue in messaging_nodes:
            link(app, queue, "publish", EdgeKind.ASYNC)
        for ext in external_nodes:
            link(app, ext, "API", EdgeKind.SYNC)

    # Remaining services still need to reach persistence, or they float
    # disconnected - give them the single most likely store.
    primary_store = next((n for n in data_nodes if n.kind is K.DATABASE), None)
    for app in request_apps[MAX_FULLY_WIRED_APPS:]:
        link(app, primary_store, "read/write", EdgeKind.DATA)

    for queue in messaging_nodes:
        for worker in workers:
            link(queue, worker, "consume", EdgeKind.ASYNC)
    for worker in workers:
        link(worker, primary_store, "persist", EdgeKind.DATA)
        for ext in external_nodes:
            link(worker, ext, "API", EdgeKind.SYNC)

    for app in request_apps[:2]:
        for ops in ops_nodes:
            if ops.kind is K.MONITORING:
                link(app, ops, "metrics/logs", EdgeKind.ASYNC)

    # ---- 9. Assemble -------------------------------------------------------
    used_tiers = {c.cluster for c in components.values() if c.cluster}
    arch.clusters = [
        Cluster(id=tier, name=TIER_NAMES[tier], kind="tier")
        for tier in (TIER_EDGE, TIER_APP, TIER_MESSAGING, TIER_DATA, TIER_EXTERNAL, TIER_OPS)
        if tier in used_tiers
    ]
    arch.components = list(components.values())
    arch.edges = _dedupe_edges(edges)
    # The icon family must match what is actually on the canvas. Deriving it
    # from the evidence alone can name a cloud that survived filtering and so
    # appears nowhere in the diagram.
    arch.provider = _provider_from_components(arch.components, provider)
    arch.tech_stack = build_tech_stack(evidence)
    arch.summary = _build_summary(evidence, arch)
    arch.highlights = _build_highlights(evidence, arch)
    arch.prune_dangling_edges()
    return arch


def _first_evidence(signal: Signal) -> str:
    return signal.evidence[0] if signal.evidence else ""


def _provider_from_components(components: list[Component], fallback: Provider) -> Provider:
    """Pick the cloud that the drawn components actually belong to."""
    tally: dict[str, int] = {}
    for component in components:
        provider = component.service.split(".", 1)[0]
        if provider in {"aws", "azure", "gcp"}:
            tally[provider] = tally.get(provider, 0) + 1
    if not tally:
        return Provider.ONPREM
    winner = Provider(max(tally.items(), key=lambda kv: kv[1])[0])
    return winner if winner else fallback


def _drop_redundant(signals: dict[str, Signal]) -> dict[str, Signal]:
    """Collapse a managed service and the engine behind it into one component.

    Aurora PostgreSQL shows up three times in the raw signals - as
    `aws.aurora` from Terraform, `onprem.postgresql` from the psycopg2
    dependency and `onprem.sql` from SQLAlchemy. They are one database. The
    signals stay in the evidence (they are all true); only the component model
    is deduplicated.
    """
    drop: set[str] = set()
    for present, redundant in REDUNDANT_WHEN_PRESENT.items():
        if present in signals:
            drop |= redundant & signals.keys()
    # Never drop something that is itself the winner of another rule, or a
    # chain like postgresql -> sql would remove the wrong end.
    drop -= {key for key in drop if key in REDUNDANT_WHEN_PRESENT and key not in drop}
    return {key: value for key, value in signals.items() if key not in drop}


def _build_app_nodes(evidence: Evidence, signals_by_service: dict[str, Signal],
                     add) -> list[Component]:
    """Decide what the application tier looks like.

    Preference order, most specific first:
      1. IaC compute resources (Lambda functions, K8s Deployments, ...)
      2. Monorepo sub-services (directories owning a manifest/Dockerfile)
      3. A single node named after the detected web framework
    """
    nodes: list[Component] = []

    # -- 1. IaC-declared compute --------------------------------------------
    compute_resources = [
        res for res in evidence.iac
        if (res.service in COMPUTE_SERVICES
            or (res.service or "").endswith((".lambda", ".functions")))
        and res.resource_type not in HOST_RESOURCE_TYPES
    ]
    seen_names: set[str] = set()
    for res in compute_resources[:12]:
        label = _humanise(_iac_display_name(res))
        if label.lower() in seen_names:
            continue
        seen_names.add(label.lower())
        kind = K.FUNCTION if (res.service or "").endswith((".lambda", ".functions")) else K.SERVICE
        nodes.append(add(Component(
            id=slug(label), name=label, kind=kind,
            service=res.service or "generic.service", cluster=TIER_APP,
            description=f"{res.kind} `{res.resource_type}`",
            paths=[res.file],
        )))

    # -- 2. Compose services built from local Dockerfiles --------------------
    if not nodes:
        for res in evidence.iac:
            if res.kind == "compose" and res.service is None and res.properties.get("build"):
                label = _humanise(res.name)
                nodes.append(add(Component(
                    id=slug(label), name=label, kind=K.SERVICE,
                    service=_framework_service(evidence), cluster=TIER_APP,
                    description="Container defined in docker-compose",
                    paths=[res.file],
                )))

    # -- 3. Monorepo sub-services --------------------------------------------
    if not nodes and len(evidence.services) > 1:
        for path in evidence.services[:8]:
            label = _humanise(path.rsplit("/", 1)[-1])
            nodes.append(add(Component(
                id=slug(label), name=label, kind=K.SERVICE,
                service=_framework_service(evidence), cluster=TIER_APP,
                description=f"Deployable unit at `{path}`", paths=[path],
            )))

    # -- 4. Single application node -------------------------------------------
    if not nodes:
        framework = next(
            (s for s in evidence.signals
             if s.kind is K.SERVICE and s.service.startswith("programming.")),
            None,
        )
        name = framework.label if framework else f"{evidence.name} service"
        nodes.append(add(Component(
            id=slug(name) or "application", name=name, kind=K.SERVICE,
            service=framework.service if framework else "generic.service",
            cluster=TIER_APP,
            description=f"{evidence.primary_language} application"
                        + (f" ({len(evidence.routes)} HTTP routes)" if evidence.routes else ""),
            technologies=[framework.label] if framework else [],
            paths=evidence.entrypoints[:4],
        )))

    # -- 5. Background workers ------------------------------------------------
    worker_signals = [s for s in signals_by_service.values() if s.kind is K.WORKER]
    for signal in worker_signals:
        nodes.append(add(Component(
            id=slug(signal.label), name=signal.label, kind=K.WORKER,
            service=signal.service, cluster=TIER_APP,
            description="Background processing", paths=signal.paths[:5],
        )))

    return nodes


def _framework_service(evidence: Evidence) -> str:
    for signal in evidence.signals:
        if signal.service.startswith("programming.") and signal.kind is K.SERVICE:
            return signal.service
    language_icon = {
        "Python": "programming.python", "TypeScript": "programming.typescript",
        "JavaScript": "programming.nodejs", "Java": "programming.java",
        "Go": "programming.go", "C#": "programming.csharp", "Ruby": "programming.ruby",
        "PHP": "programming.php", "Rust": "programming.rust",
    }
    return language_icon.get(evidence.primary_language, "generic.service")


def _iac_display_name(res) -> str:
    """The name a human would use for an IaC resource.

    Terraform block labels are often terse (`api`, `main`); the resource's own
    `name`/`function_name` attribute is usually the deployed name and reads far
    better on a diagram.
    """
    for key in ("function_name", "name", "cluster_name", "identifier", "bucket"):
        value = res.properties.get(key)
        if isinstance(value, str) and value and "${" not in value and "." not in value:
            return value
    return res.name


# Words that should stay upper-case rather than being title-cased into
# "Api", "Sqs", "Db" - which reads as a typo on a diagram.
_ACRONYMS = {
    "api", "sqs", "sns", "db", "ui", "cdn", "id", "ml", "ai", "io",
    "s3", "ec2", "rds", "vpc", "iam", "jwt", "http", "https", "grpc", "cpu",
    "gpu", "sdk", "cli", "dns", "ssl", "tls", "acl", "kms", "eks", "ecs",
    "aks", "gke", "etl", "iot", "crm", "erp", "sso", "mfa", "pdf", "csv",
}


def _humanise(name: str) -> str:
    """`payment-service_v2` -> `Payment Service V2`, `shop-api` -> `Shop API`."""
    cleaned = re.sub(r"[_\-.]+", " ", name).strip()
    cleaned = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", cleaned)
    words = []
    for word in cleaned.split():
        if word.lower() in _ACRONYMS:
            words.append(word.upper())
        elif word.islower():
            words.append(word.capitalize())
        else:
            words.append(word)
    return " ".join(words)[:40] or name


def _dedupe_edges(edges: list[Edge]) -> list[Edge]:
    seen: dict[tuple[str, str], Edge] = {}
    for edge in edges:
        key = (edge.source, edge.target)
        if key not in seen:
            seen[key] = edge
    return list(seen.values())


def build_tech_stack(evidence: Evidence) -> dict[str, list[str]]:
    """Group detected technologies for the report's stack panel."""
    groups: dict[str, list[str]] = defaultdict(list)
    group_names = {
        K.SERVICE: "Frameworks", K.FRONTEND: "Frontend", K.FUNCTION: "Serverless",
        K.DATABASE: "Databases", K.CACHE: "Caching", K.STORAGE: "Storage",
        K.QUEUE: "Messaging", K.STREAM: "Streaming", K.SEARCH: "Search",
        K.ANALYTICS: "Analytics", K.ML: "AI / ML", K.AUTH: "Security & Identity",
        K.MONITORING: "Observability", K.CICD: "CI/CD & IaC",
        K.GATEWAY: "Networking", K.CDN: "Networking", K.EXTERNAL: "Third-party",
        K.WORKER: "Background jobs", K.INFRA: "Platform",
    }
    for signal in evidence.signals:
        label = group_names.get(signal.kind, "Other")
        if signal.label not in groups[label]:
            groups[label].append(signal.label)

    languages = [lang for lang, loc in evidence.languages.items() if loc > 0]
    if languages:
        groups["Languages"] = languages[:8]

    # Stable, readable ordering for the UI.
    order = ["Languages", "Frameworks", "Frontend", "Serverless", "Networking",
             "Databases", "Caching", "Storage", "Messaging", "Streaming", "Search",
             "Analytics", "AI / ML", "Background jobs", "Security & Identity",
             "Observability", "CI/CD & IaC", "Third-party", "Platform", "Other"]
    return {name: groups[name] for name in order if groups.get(name)}


def _build_summary(evidence: Evidence, arch: Architecture) -> str:
    bits: list[str] = []
    provider_names = {
        Provider.AWS: "AWS", Provider.AZURE: "Azure", Provider.GCP: "Google Cloud",
    }
    style = _architecture_style(evidence, arch)
    bits.append(
        f"{evidence.name} is a {style} written primarily in "
        f"{evidence.primary_language} ({evidence.total_loc:,} lines across "
        f"{evidence.file_count:,} files)."
    )
    if arch.provider in provider_names:
        cloud_services = sorted({
            c.name for c in arch.components
            if c.service.startswith(arch.provider.value)
        })
        if cloud_services:
            bits.append(
                f"It runs on {provider_names[arch.provider]}, using "
                + ", ".join(cloud_services[:6])
                + ("." if len(cloud_services) <= 6 else f", and {len(cloud_services) - 6} more.")
            )
    if evidence.routes:
        bits.append(f"The HTTP surface exposes {len(evidence.routes)} routes.")
    datastores = [c.name for c in arch.components if c.kind in {K.DATABASE, K.CACHE, K.STORAGE}]
    if datastores:
        bits.append("Persistence is handled by " + ", ".join(sorted(set(datastores))[:5]) + ".")
    return " ".join(bits)


def _architecture_style(evidence: Evidence, arch: Architecture) -> str:
    services = [c for c in arch.components if c.kind in {K.SERVICE, K.FUNCTION}]
    functions = [c for c in services if c.kind is K.FUNCTION]
    if functions and len(functions) >= len(services) / 2:
        return "serverless application"
    if len(services) >= 4:
        return "microservice system"
    if any(s.service.startswith("k8s.") for s in evidence.signals):
        return "containerised application"
    if evidence.routes:
        return "web service"
    return "software project"


def _build_highlights(evidence: Evidence, arch: Architecture) -> list[str]:
    out: list[str] = []
    if evidence.iac:
        kinds = sorted({r.kind for r in evidence.iac})
        out.append(f"Infrastructure defined as code via {', '.join(kinds)} "
                   f"({len(evidence.iac)} resources).")
    if evidence.entrypoints:
        out.append(f"Entrypoints: {', '.join(evidence.entrypoints[:4])}")
    async_edges = [e for e in arch.edges if e.kind is EdgeKind.ASYNC]
    if async_edges:
        out.append(f"{len(async_edges)} asynchronous (event-driven) interactions detected.")
    if not evidence.iac and arch.provider is not Provider.ONPREM:
        out.append("Cloud services are referenced in code but not declared in IaC — "
                   "the deployment topology is inferred from SDK usage.")
    return out
