"""Scanner orchestration: walk a repo once and produce an :class:`Evidence`.

Everything here is factual. No interpretation happens at this layer - that is
the inference layer's job. The separation matters because it lets the UI show
"here is what we found and where" independently of "here is what we think it
means".
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from pathlib import Path

from ..catalog import (
    CONTENT_PROBES,
    FILENAME_RULES,
    match_dependency,
    match_docker_image,
    match_import,
)
from ..models import (
    CodeEntity,
    Dependency,
    Evidence,
    FileInfo,
    IacResource,
    ModuleEdge,
    Route,
    Signal,
)
from . import code as code_analysis
from . import iac as iac_analysis
from . import manifests as manifest_analysis
from .text import strip_comments
from .walker import CODE_LANGUAGES, count_loc, is_test_file, read_text, walk

ProgressFn = Callable[[str, float], None]

# Content probes are expensive on large repos; cap how much of each file we scan.
_PROBE_BYTES = 60_000

# Documentation describes technologies rather than using them - a README saying
# "we store files in s3://" is not evidence that this code talks to S3.
_DOC_LANGUAGES = frozenset({"Markdown", "reStructuredText", "Text"})

# Ceiling applied to every signal originating in test code, chosen to sit just
# below `MIN_COMPONENT_CONFIDENCE` so fixtures corroborate but never originate.
_TEST_FILE_CONFIDENCE = 0.65

# Probe confidence is per-pattern, defined alongside the patterns in
# `catalog.CONTENT_PROBES`. See the comment there: call shapes score above
# `MIN_COMPONENT_CONFIDENCE` and may originate a component; bare URL and
# hostname mentions score below it and can only corroborate.

# Source files we fully analyse. Beyond this the marginal diagram value is nil
# but the runtime cost is not.
_MAX_ANALYSED_SOURCES = 4_000


class SignalBag:
    """Accumulates signals, merging duplicates and tracking evidence."""

    def __init__(self) -> None:
        self._by_service: dict[str, Signal] = {}

    def add(self, service: str, label: str, kind, *, confidence: float,
            evidence: str, path: str | None = None) -> None:
        existing = self._by_service.get(service)
        incoming = Signal(
            service=service, label=label, kind=kind, confidence=confidence,
            evidence=[evidence], paths=[path] if path else [],
        )
        if existing is None:
            self._by_service[service] = incoming
        else:
            existing.merge(incoming)
            # Keep the highest-confidence label - IaC beats a loose import.
            if incoming.confidence > existing.confidence:
                existing.label = label
                existing.kind = kind

    def to_list(self) -> list[Signal]:
        # Trim evidence lists so the payload stays readable in the UI and cheap
        # to send to the model.
        out = sorted(self._by_service.values(), key=lambda s: (-s.confidence, s.service))
        for sig in out:
            sig.evidence = sig.evidence[:6]
            sig.paths = sig.paths[:10]
        return out


def _module_name(rel_path: str) -> str:
    """Repo-relative path -> dotted module-ish name, for the internal graph."""
    path = Path(rel_path)
    parts = list(path.parts[:-1]) + [path.stem]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _top_package(rel_path: str) -> str:
    """First meaningful path segment - used to group a monorepo into services."""
    parts = Path(rel_path).parts
    skip = {"src", "lib", "app", "apps", "packages", "services", "cmd", "internal", "pkg"}
    for part in parts[:-1]:
        if part.lower() not in skip:
            return part
    # `services/payments/...` -> payments
    if len(parts) > 2 and parts[0].lower() in {"services", "apps", "packages"}:
        return parts[1]
    return parts[0] if len(parts) > 1 else "root"


def scan(root: str | Path, *, progress: ProgressFn | None = None,
         max_files: int = 20_000) -> Evidence:
    """Walk `root` and return everything static analysis can prove about it."""
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise NotADirectoryError(f"Not a directory: {root_path}")

    def report(message: str, fraction: float) -> None:
        if progress:
            progress(message, fraction)

    report("Enumerating files", 0.02)
    files: list[FileInfo] = walk(root_path, max_files=max_files)

    evidence = Evidence(root=str(root_path), name=root_path.name, files=files)
    if len(files) >= max_files:
        evidence.notes.append(
            f"File limit reached ({max_files:,}); analysis covers a subset of the tree."
        )
    if not iac_analysis.yaml_available():
        evidence.notes.append("PyYAML not installed - Kubernetes/Compose/SAM parsing was skipped.")

    bag = SignalBag()
    languages: Counter[str] = Counter()
    dependencies: list[Dependency] = []
    routes: list[Route] = []
    entities: list[CodeEntity] = []
    iac_resources: list[IacResource] = []
    module_edges: Counter[tuple[str, str]] = Counter()
    entrypoints: list[str] = []
    npm_scripts: dict[str, str] = {}

    # Local module names, so we can tell first-party imports from third-party.
    local_modules = {_module_name(f.path) for f in files}
    local_roots = {m.split(".", 1)[0] for m in local_modules if m}

    report("Reading files", 0.08)
    analysed_sources = 0
    total = max(len(files), 1)

    for index, finfo in enumerate(files):
        if index % 200 == 0:
            report(f"Reading files ({index:,}/{len(files):,})", 0.08 + 0.52 * index / total)

        abs_path = root_path / finfo.path
        text = read_text(abs_path)
        if not text:
            continue

        # Evidence from test code is recorded but capped below the component
        # threshold, exactly as dev dependencies are skipped: a fixture is not
        # production architecture.
        confidence_cap = _TEST_FILE_CONFIDENCE if is_test_file(finfo.path) else 1.0

        # -- filename-based signals -------------------------------------
        for pattern, (service, label, kind) in FILENAME_RULES:
            if pattern.search(finfo.path):
                bag.add(service, label, kind, confidence=0.8,
                        evidence=f"file `{finfo.path}`", path=finfo.path)

        # -- manifests ----------------------------------------------------
        if manifest_analysis.is_manifest(finfo.path):
            deps, extra = manifest_analysis.parse_manifest(finfo.path, text)
            dependencies.extend(deps)
            if extra.get("scripts"):
                npm_scripts.update({k: str(v)[:120] for k, v in extra["scripts"].items()})

        # -- infrastructure as code ---------------------------------------
        resources = iac_analysis.parse_iac(finfo.path, text)
        iac_resources.extend(resources)
        for res in resources:
            if res.service:
                rule = _rule_for_iac(res)
                bag.add(res.service,
                        rule[1] if rule else _prettify_service(res.service),
                        rule[2] if rule else _kind_for_iac(res),
                        confidence=0.95,
                        evidence=f"{res.kind}: `{res.resource_type}` in `{res.file}`",
                        path=res.file)
            # Compose/K8s container images name real infrastructure too.
            for image in res.properties.get("images", []) or ([res.properties["image"]]
                                                              if res.properties.get("image") else []):
                rule = match_docker_image(str(image))
                if rule:
                    bag.add(rule[0], rule[1], rule[2], confidence=0.9,
                            evidence=f"container image `{image}` in `{res.file}`",
                            path=res.file)

        # -- source code ---------------------------------------------------
        if finfo.language in CODE_LANGUAGES:
            loc = count_loc(text)
            finfo.loc = loc
            languages[finfo.language] += loc
            evidence.total_loc += loc

            if analysed_sources < _MAX_ANALYSED_SOURCES:
                analysed_sources += 1
                imports, file_entities, file_routes = code_analysis.analyse(
                    finfo.path, finfo.language, text
                )
                routes.extend(file_routes)
                # Only keep documented or decorated entities - undocumented
                # private helpers add noise to flowcharts without adding meaning.
                entities.extend([
                    e for e in file_entities
                    if e.docstring or e.decorators or e.kind == "class" or e.calls
                ][:40])

                for module in imports:
                    rule = match_import(module)
                    if rule:
                        bag.add(rule[0], rule[1], rule[2],
                                confidence=min(0.75, confidence_cap),
                                evidence=f"`import {module}` in `{finfo.path}`",
                                path=finfo.path)
                    root_pkg = module.lstrip(".").split(".", 1)[0].split("/", 1)[0]
                    if root_pkg and root_pkg in local_roots:
                        module_edges[(_top_package(finfo.path), root_pkg)] += 1

                if code_analysis.is_entrypoint(finfo.path, text, finfo.language):
                    entrypoints.append(finfo.path)
        elif finfo.language in {"YAML", "JSON", "TOML", "Terraform", "Bicep", "Dockerfile"}:
            languages[finfo.language] += 0  # tracked for display, not LOC

        # -- content probes -------------------------------------------------
        # Prose that *mentions* a technology is not a system that *uses* it,
        # so documentation is excluded from probing entirely.
        if finfo.language not in _DOC_LANGUAGES:
            # Comments are prose: a commented-out call or a usage example in a
            # docstring describes the technology rather than using it.
            probe_text = strip_comments(text[:_PROBE_BYTES], finfo.language)
            for pattern, (service, label, kind), weight in CONTENT_PROBES:
                if pattern.search(probe_text):
                    bag.add(service, label, kind,
                            confidence=min(weight, confidence_cap),
                            evidence=f"matched in `{finfo.path}`", path=finfo.path)

        # -- README ----------------------------------------------------------
        lower_name = Path(finfo.path).name.lower()
        if lower_name.startswith("readme") and not evidence.readme_excerpt:
            evidence.readme_excerpt = text[:4_000]

    report("Resolving dependencies", 0.66)
    for dep in dependencies:
        if dep.dev:
            continue
        rule = match_dependency(dep.name)
        if rule:
            bag.add(rule[0], rule[1], rule[2], confidence=0.85,
                    evidence=f"dependency `{dep.name}` in `{dep.source}`", path=dep.source)

    report("Assembling evidence", 0.74)
    evidence.file_count = len(files)
    evidence.languages = dict(languages.most_common())
    evidence.dependencies = _dedupe_dependencies(dependencies)
    evidence.signals = bag.to_list()
    evidence.routes = routes[:400]
    evidence.entities = entities[:1_500]
    evidence.iac = iac_resources[:600]
    evidence.entrypoints = sorted(set(entrypoints))[:60]
    evidence.module_edges = [
        ModuleEdge(source=src, target=dst, weight=weight)
        for (src, dst), weight in module_edges.most_common(120)
        if src != dst
    ]
    evidence.services = _detect_subservices(files, iac_resources)

    if npm_scripts:
        evidence.notes.append("npm scripts: " + ", ".join(sorted(npm_scripts)[:12]))

    report("Scan complete", 0.78)
    return evidence


def _rule_for_iac(res: IacResource):
    """Recover the full catalog rule (service, label, kind) for an IaC resource.

    Using the catalog's own label matters: deriving one from the service key
    yields "Sqs" and "Alb" where the catalog says "Amazon SQS" and "Application
    Load Balancer".
    """
    from ..catalog import match_arm, match_cloudformation, match_k8s, match_terraform

    lookup = {
        "terraform": match_terraform, "cloudformation": match_cloudformation,
        "arm": match_arm, "bicep": match_arm, "k8s": match_k8s,
    }.get(res.kind)
    return lookup(res.resource_type) if lookup else None


def _prettify_service(service: str) -> str:
    """Fallback label for a service key with no catalog rule behind it."""
    tail = service.split(".", 1)[-1]
    return tail.replace("_", " ").title()


def _kind_for_iac(res: IacResource):
    """ComponentKind for an IaC resource, when no full rule is available."""
    from ..models import ComponentKind

    rule = _rule_for_iac(res)
    if rule:
        return rule[2]
    if res.kind == "serverless":
        return ComponentKind.FUNCTION
    return ComponentKind.SERVICE


def _dedupe_dependencies(deps: list[Dependency]) -> list[Dependency]:
    """Collapse the same package declared in several manifests."""
    seen: dict[tuple[str, str], Dependency] = {}
    for dep in deps:
        key = (dep.ecosystem, dep.name.lower())
        current = seen.get(key)
        if current is None or (current.dev and not dep.dev):
            seen[key] = dep
    return sorted(seen.values(), key=lambda d: (d.ecosystem, d.name.lower()))[:600]


def _detect_subservices(files: list[FileInfo], resources: list[IacResource]) -> list[str]:
    """Identify deployable units in a monorepo.

    A directory that owns its own manifest or Dockerfile is a separate
    deployable, which is exactly what should become its own diagram node.
    """
    candidates: set[str] = set()
    for finfo in files:
        path = Path(finfo.path)
        name = path.name.lower()
        is_unit_marker = (
            name in {"package.json", "pyproject.toml", "go.mod", "pom.xml", "cargo.toml"}
            or name.startswith("dockerfile")
        )
        if is_unit_marker and len(path.parts) > 1:
            candidates.add(path.parent.as_posix())

    # Compose services are deployables by definition.
    candidates.update(
        res.name for res in resources if res.kind == "compose"
    )
    return sorted(candidates)[:40]
