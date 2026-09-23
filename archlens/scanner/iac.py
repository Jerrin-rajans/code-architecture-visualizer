"""Infrastructure-as-code parsing.

IaC is the single best source of architecture truth: unlike application code, it
names the managed services explicitly. Terraform is parsed with regexes (HCL has
no stdlib parser and the full grammar is not needed to read resource headers);
everything YAML/JSON-shaped goes through a real parser.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..catalog import (
    match_arm,
    match_cloudformation,
    match_docker_image,
    match_k8s,
    match_terraform,
)
from ..models import IacResource

try:  # PyYAML ships with Anaconda but is not guaranteed elsewhere.
    import yaml

    _HAS_YAML = True
except ImportError:  # pragma: no cover - degraded mode
    yaml = None  # type: ignore[assignment]
    _HAS_YAML = False


def _safe_yaml_all(text: str) -> list[Any]:
    """Load every document in a YAML file, tolerating templating syntax.

    Helm charts and GitHub Actions embed `{{ }}` and `!Ref`-style tags that break
    the safe loader; we neutralise the common cases rather than skipping the file.
    """
    if not _HAS_YAML:
        return []
    cleaned = re.sub(r"\{\{[^}]*\}\}", "PLACEHOLDER", text)
    # CloudFormation short tags (!Ref, !GetAtt, !Sub, ...) -> plain strings.
    cleaned = re.sub(r"(^|[\s\[{,])!(\w+)\s", r"\1", cleaned)
    try:
        return [d for d in yaml.safe_load_all(cleaned) if d is not None]
    except Exception:
        return []


# --------------------------------------------------------------------------- #
# Terraform
# --------------------------------------------------------------------------- #

# `resource "aws_lambda_function" "api" {`
_TF_RESOURCE = re.compile(
    r'resource\s+"(?P<type>[A-Za-z0-9_-]+)"\s+"(?P<name>[A-Za-z0-9_-]+)"\s*\{',
)
_TF_MODULE = re.compile(r'module\s+"(?P<name>[A-Za-z0-9_-]+)"\s*\{')
# Attributes we surface so the LLM can wire edges (e.g. which queue a lambda reads).
_TF_ATTR = re.compile(r'^\s*(?P<key>[a-z0-9_]+)\s*=\s*(?P<value>.+?)\s*$', re.M)
_TF_INTERESTING_ATTRS = {
    "name", "bucket", "function_name", "engine", "image_uri", "runtime",
    "identifier", "table_name", "cluster_name", "sku_name", "kind",
    "topic_arn", "queue_name", "namespace_name", "account_name", "instance_class",
}


def _tf_block_body(text: str, start: int) -> str:
    """Return the `{...}` body beginning at `start`, balancing braces."""
    depth = 0
    for i in range(start, min(len(text), start + 12_000)):
        char = text[i]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return text[start : start + 2_000]


def parse_terraform(rel_path: str, text: str) -> list[IacResource]:
    resources: list[IacResource] = []
    for match in _TF_RESOURCE.finditer(text):
        rtype, rname = match.group("type"), match.group("name")
        body = _tf_block_body(text, match.end() - 1)
        props: dict[str, Any] = {}
        for attr in _TF_ATTR.finditer(body):
            key = attr.group("key")
            if key in _TF_INTERESTING_ATTRS:
                props[key] = attr.group("value").strip().strip('"')[:120]
        rule = match_terraform(rtype)
        resources.append(IacResource(
            kind="terraform", resource_type=rtype, name=rname, file=rel_path,
            service=rule[0] if rule else None, properties=props,
        ))
    for match in _TF_MODULE.finditer(text):
        resources.append(IacResource(
            kind="terraform", resource_type="module", name=match.group("name"),
            file=rel_path,
        ))
    return resources


# --------------------------------------------------------------------------- #
# CloudFormation / SAM
# --------------------------------------------------------------------------- #

def parse_cloudformation(rel_path: str, doc: dict) -> list[IacResource]:
    resources: list[IacResource] = []
    for name, spec in (doc.get("Resources") or {}).items():
        if not isinstance(spec, dict):
            continue
        rtype = str(spec.get("Type", ""))
        rule = match_cloudformation(rtype)
        props = spec.get("Properties") or {}
        keep = {
            k: str(v)[:120]
            for k, v in props.items()
            if k in {"FunctionName", "BucketName", "TableName", "QueueName",
                     "TopicName", "Runtime", "Handler", "Engine", "Events"}
        }
        resources.append(IacResource(
            kind="cloudformation", resource_type=rtype, name=str(name),
            file=rel_path, service=rule[0] if rule else None, properties=keep,
        ))
    return resources


def is_cloudformation(doc: Any) -> bool:
    return (
        isinstance(doc, dict)
        and isinstance(doc.get("Resources"), dict)
        and (
            "AWSTemplateFormatVersion" in doc
            or "Transform" in doc
            or any(
                isinstance(v, dict) and str(v.get("Type", "")).startswith("AWS::")
                for v in doc["Resources"].values()
            )
        )
    )


# --------------------------------------------------------------------------- #
# Kubernetes
# --------------------------------------------------------------------------- #

def parse_k8s(rel_path: str, doc: dict) -> list[IacResource]:
    kind = str(doc.get("kind", ""))
    rule = match_k8s(kind)
    if not rule:
        return []
    meta = doc.get("metadata") or {}
    name = str(meta.get("name", kind.lower()))

    props: dict[str, Any] = {"namespace": meta.get("namespace", "default")}

    # Pull container images - they tell us what actually runs in the pod.
    spec = doc.get("spec") or {}
    pod_spec = ((spec.get("template") or {}).get("spec")) or spec
    containers = pod_spec.get("containers") if isinstance(pod_spec, dict) else None
    if isinstance(containers, list):
        images = [str(c.get("image", "")) for c in containers if isinstance(c, dict)]
        props["images"] = [i for i in images if i][:6]
    if isinstance(spec.get("replicas"), int):
        props["replicas"] = spec["replicas"]
    if kind.lower() == "service":
        props["service_type"] = spec.get("type", "ClusterIP")

    return [IacResource(
        kind="k8s", resource_type=kind, name=name, file=rel_path,
        service=rule[0], properties=props,
    )]


def is_k8s(doc: Any) -> bool:
    return isinstance(doc, dict) and "apiVersion" in doc and "kind" in doc


# --------------------------------------------------------------------------- #
# docker-compose
# --------------------------------------------------------------------------- #

def parse_compose(rel_path: str, doc: dict) -> list[IacResource]:
    resources: list[IacResource] = []
    for name, spec in (doc.get("services") or {}).items():
        if not isinstance(spec, dict):
            continue
        image = str(spec.get("image", "") or "")
        rule = match_docker_image(image) if image else None
        props: dict[str, Any] = {}
        if image:
            props["image"] = image
        if spec.get("build"):
            props["build"] = str(spec["build"])[:120]
        if spec.get("ports"):
            props["ports"] = [str(p) for p in spec["ports"]][:6]
        if spec.get("depends_on"):
            depends = spec["depends_on"]
            props["depends_on"] = list(depends) if isinstance(depends, list | dict) else [str(depends)]
        resources.append(IacResource(
            kind="compose", resource_type=image or "build", name=str(name),
            file=rel_path,
            # A service built from a local Dockerfile is first-party app code,
            # not a known managed service - leave `service` unset so the
            # heuristic layer treats it as an application container.
            service=rule[0] if rule else None,
            properties=props,
        ))
    return resources


def is_compose(rel_path: str, doc: Any) -> bool:
    name = Path(rel_path).name.lower()
    return (
        isinstance(doc, dict)
        and isinstance(doc.get("services"), dict)
        and ("compose" in name or "version" in doc or "services" in doc)
        and "kind" not in doc
    )


# --------------------------------------------------------------------------- #
# Serverless Framework
# --------------------------------------------------------------------------- #

def parse_serverless(rel_path: str, doc: dict) -> list[IacResource]:
    resources: list[IacResource] = []
    provider = doc.get("provider") or {}
    provider_name = str(provider.get("name", "aws")).lower() if isinstance(provider, dict) else "aws"

    for name, raw_spec in (doc.get("functions") or {}).items():
        spec = raw_spec if isinstance(raw_spec, dict) else {}
        events = spec.get("events") or []
        event_types = [
            key
            for ev in events if isinstance(ev, dict)
            for key in ev
        ]
        service_key = {
            "aws": "aws.lambda", "azure": "azure.functions", "google": "gcp.functions",
        }.get(provider_name, "aws.lambda")
        resources.append(IacResource(
            kind="serverless", resource_type="function", name=str(name), file=rel_path,
            service=service_key,
            properties={"handler": str(spec.get("handler", ""))[:120], "events": event_types[:8]},
        ))

    # Nested CloudFormation under `resources:`
    raw = doc.get("resources")
    if isinstance(raw, dict) and isinstance(raw.get("Resources"), dict):
        resources.extend(parse_cloudformation(rel_path, raw))
    return resources


def is_serverless(rel_path: str, doc: Any) -> bool:
    return (
        Path(rel_path).name.lower().startswith("serverless.")
        and isinstance(doc, dict)
        and ("functions" in doc or "provider" in doc)
    )


# --------------------------------------------------------------------------- #
# Bicep / ARM
# --------------------------------------------------------------------------- #

_BICEP_RESOURCE = re.compile(
    r"resource\s+(?P<name>\w+)\s+'(?P<type>[^@']+)@[^']*'",
)


def parse_bicep(rel_path: str, text: str) -> list[IacResource]:
    resources: list[IacResource] = []
    for match in _BICEP_RESOURCE.finditer(text):
        rtype = match.group("type")
        rule = match_arm(rtype)
        resources.append(IacResource(
            kind="bicep", resource_type=rtype, name=match.group("name"),
            file=rel_path, service=rule[0] if rule else None,
        ))
    return resources


def parse_arm(rel_path: str, doc: dict) -> list[IacResource]:
    resources: list[IacResource] = []

    def walk(items: Any) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            rtype = str(item.get("type", ""))
            if rtype:
                rule = match_arm(rtype)
                resources.append(IacResource(
                    kind="arm", resource_type=rtype,
                    name=str(item.get("name", rtype.rsplit("/", 1)[-1])),
                    file=rel_path, service=rule[0] if rule else None,
                ))
            walk(item.get("resources"))

    walk(doc.get("resources"))
    return resources


def is_arm(doc: Any) -> bool:
    return (
        isinstance(doc, dict)
        and "$schema" in doc
        and "deploymentTemplate" in str(doc.get("$schema", ""))
    )


# --------------------------------------------------------------------------- #
# Dockerfile
# --------------------------------------------------------------------------- #

_DOCKER_FROM = re.compile(r"^\s*FROM\s+(?P<image>\S+)", re.M | re.I)
_DOCKER_EXPOSE = re.compile(r"^\s*EXPOSE\s+(?P<ports>.+)$", re.M | re.I)


def parse_dockerfile(rel_path: str, text: str) -> list[IacResource]:
    images = [m.group("image") for m in _DOCKER_FROM.finditer(text)]
    ports = [p for m in _DOCKER_EXPOSE.finditer(text) for p in m.group("ports").split()]
    if not images:
        return []
    return [IacResource(
        kind="dockerfile", resource_type=images[-1], name=Path(rel_path).parent.name or "app",
        file=rel_path,
        properties={"base_images": images[:4], "ports": ports[:6]},
    )]


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #

def parse_iac(rel_path: str, text: str) -> list[IacResource]:
    """Identify and parse any IaC file. Returns [] for non-IaC input."""
    name = Path(rel_path).name.lower()
    suffix = Path(rel_path).suffix.lower()

    if suffix in {".tf", ".tfvars", ".hcl"}:
        return parse_terraform(rel_path, text)
    if suffix == ".bicep":
        return parse_bicep(rel_path, text)
    if name.startswith("dockerfile"):
        return parse_dockerfile(rel_path, text)

    if suffix == ".json":
        try:
            doc = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return []
        if is_arm(doc):
            return parse_arm(rel_path, doc)
        if is_cloudformation(doc):
            return parse_cloudformation(rel_path, doc)
        return []

    if suffix in {".yml", ".yaml"}:
        out: list[IacResource] = []
        for doc in _safe_yaml_all(text):
            if is_serverless(rel_path, doc):
                out.extend(parse_serverless(rel_path, doc))
            elif is_k8s(doc):
                out.extend(parse_k8s(rel_path, doc))
            elif is_compose(rel_path, doc):
                out.extend(parse_compose(rel_path, doc))
            elif is_cloudformation(doc):
                out.extend(parse_cloudformation(rel_path, doc))
        return out

    return []


def yaml_available() -> bool:
    return _HAS_YAML
