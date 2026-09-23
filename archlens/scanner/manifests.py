"""Dependency manifest parsing.

Manifests are the highest-signal, lowest-cost evidence in a repository: one
small file tells you the framework, the database driver and the cloud SDK. We
parse them with real parsers where the stdlib provides one (`tomllib`, `json`,
`xml`) and fall back to targeted regexes only for Gradle and Go, whose formats
have no stdlib parser.
"""

from __future__ import annotations

import json
import re
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

from ..models import Dependency

# `package==1.2.3`, `package[extra]>=1.0`, `package @ git+https://...`
_PY_REQ = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9._-]+)\s*(?:\[[^\]]*\])?\s*"
    r"(?P<op>[=<>!~^]{1,2})?\s*(?P<version>[A-Za-z0-9._*+!-]+)?"
)


def _clean_requirement(line: str) -> tuple[str, str | None] | None:
    """Parse one requirements.txt line into (name, version)."""
    line = line.split("#", 1)[0].strip()
    if not line or line.startswith(("-", "git+", "http://", "https://", ".")):
        return None
    # Strip environment markers: `foo==1.0 ; python_version < "3.11"`
    line = line.split(";", 1)[0].strip()
    if " @ " in line:  # PEP 508 direct reference
        return line.split(" @ ", 1)[0].strip(), None
    match = _PY_REQ.match(line)
    if not match:
        return None
    return match.group("name"), match.group("version")


def parse_requirements_txt(text: str, source: str) -> list[Dependency]:
    deps: list[Dependency] = []
    dev = "dev" in source.lower() or "test" in source.lower()
    for line in text.splitlines():
        parsed = _clean_requirement(line)
        if parsed:
            deps.append(Dependency(
                name=parsed[0], version=parsed[1],
                ecosystem="pypi", source=source, dev=dev,
            ))
    return deps


def parse_pyproject(text: str, source: str) -> list[Dependency]:
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return []

    deps: list[Dependency] = []

    # PEP 621: [project] dependencies / optional-dependencies
    project = data.get("project", {})
    for raw in project.get("dependencies", []) or []:
        parsed = _clean_requirement(str(raw))
        if parsed:
            deps.append(Dependency(name=parsed[0], version=parsed[1],
                                   ecosystem="pypi", source=source))
    for group, items in (project.get("optional-dependencies", {}) or {}).items():
        is_dev = group.lower() in {"dev", "test", "tests", "lint", "docs", "typing"}
        for raw in items or []:
            parsed = _clean_requirement(str(raw))
            if parsed:
                deps.append(Dependency(name=parsed[0], version=parsed[1],
                                       ecosystem="pypi", source=source, dev=is_dev))

    # Poetry: [tool.poetry.dependencies]
    poetry = data.get("tool", {}).get("poetry", {})
    for key, is_dev in (("dependencies", False), ("dev-dependencies", True)):
        for name, spec in (poetry.get(key, {}) or {}).items():
            if name.lower() == "python":
                continue
            version = spec if isinstance(spec, str) else (
                spec.get("version") if isinstance(spec, dict) else None
            )
            deps.append(Dependency(name=name, version=version, ecosystem="pypi",
                                   source=source, dev=is_dev))
    for group_name, group in (poetry.get("group", {}) or {}).items():
        is_dev = group_name.lower() in {"dev", "test", "lint", "docs"}
        for name, spec in (group.get("dependencies", {}) or {}).items():
            if name.lower() == "python":
                continue
            version = spec if isinstance(spec, str) else None
            deps.append(Dependency(name=name, version=version, ecosystem="pypi",
                                   source=source, dev=is_dev))
    return deps


def parse_package_json(text: str, source: str) -> tuple[list[Dependency], dict]:
    """Return (dependencies, raw manifest) - the raw doc also carries scripts."""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return [], {}
    if not isinstance(data, dict):
        return [], {}

    deps: list[Dependency] = []
    for field, is_dev in (("dependencies", False), ("devDependencies", True),
                          ("peerDependencies", False)):
        for name, version in (data.get(field) or {}).items():
            deps.append(Dependency(name=name, version=str(version), ecosystem="npm",
                                   source=source, dev=is_dev))
    return deps, data


def parse_pom_xml(text: str, source: str) -> list[Dependency]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    # Maven POMs are namespaced; match on the local tag name instead.
    deps: list[Dependency] = []
    for dep in root.iter():
        if not dep.tag.endswith("}dependency") and dep.tag != "dependency":
            continue
        artifact = group = version = None
        scope = ""
        for child in dep:
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "artifactId":
                artifact = (child.text or "").strip()
            elif tag == "groupId":
                group = (child.text or "").strip()
            elif tag == "version":
                version = (child.text or "").strip()
            elif tag == "scope":
                scope = (child.text or "").strip().lower()
        if artifact:
            # Prefer the artifact id - it is what our catalog keys on
            # (`spring-boot-starter-web`, not `org.springframework.boot`).
            deps.append(Dependency(
                name=artifact, version=version, ecosystem="maven", source=source,
                dev=scope in {"test", "provided"},
            ))
            if group and group not in {"org.springframework.boot"}:
                deps.append(Dependency(name=group, version=version, ecosystem="maven",
                                       source=source, dev=scope == "test"))
    return deps


_GRADLE_DEP = re.compile(
    r"""(?:implementation|api|compile|runtimeOnly|testImplementation|developmentOnly)\s*"""
    r"""[\s(]*['"](?P<coord>[^'"]+)['"]""",
    re.I,
)


def parse_gradle(text: str, source: str) -> list[Dependency]:
    deps: list[Dependency] = []
    for match in _GRADLE_DEP.finditer(text):
        coord = match.group("coord")
        parts = coord.split(":")
        if len(parts) >= 2:
            name, version = parts[1], (parts[2] if len(parts) > 2 else None)
            deps.append(Dependency(name=name, version=version, ecosystem="maven",
                                   source=source, dev="test" in match.group(0).lower()))
    return deps


_GOMOD_REQUIRE = re.compile(r"^\s*(?P<path>[\w./~-]+\.[\w./~-]+)\s+(?P<version>v[\w.+-]+)")


def parse_go_mod(text: str, source: str) -> list[Dependency]:
    deps: list[Dependency] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("module ", "go ", "//")) or not stripped:
            continue
        match = _GOMOD_REQUIRE.match(line.replace("require ", ""))
        if match:
            path = match.group("path")
            # The last meaningful segment is what the catalog keys on.
            deps.append(Dependency(name=path, version=match.group("version"),
                                   ecosystem="gomod", source=source))
    return deps


_GEMFILE_GEM = re.compile(r"""^\s*gem\s+['"](?P<name>[^'"]+)['"](?:\s*,\s*['"](?P<version>[^'"]+)['"])?""")


def parse_gemfile(text: str, source: str) -> list[Dependency]:
    deps: list[Dependency] = []
    for line in text.splitlines():
        match = _GEMFILE_GEM.match(line)
        if match:
            deps.append(Dependency(name=match.group("name"), version=match.group("version"),
                                   ecosystem="rubygems", source=source))
    return deps


def parse_composer_json(text: str, source: str) -> list[Dependency]:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []
    deps: list[Dependency] = []
    for field, is_dev in (("require", False), ("require-dev", True)):
        for name, version in (data.get(field) or {}).items():
            if name.lower() in {"php"}:
                continue
            deps.append(Dependency(name=name.split("/")[-1], version=str(version),
                                   ecosystem="composer", source=source, dev=is_dev))
    return deps


def parse_cargo_toml(text: str, source: str) -> list[Dependency]:
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return []
    deps: list[Dependency] = []
    for field, is_dev in (("dependencies", False), ("dev-dependencies", True)):
        for name, spec in (data.get(field) or {}).items():
            version = spec if isinstance(spec, str) else (
                spec.get("version") if isinstance(spec, dict) else None
            )
            deps.append(Dependency(name=name, version=version, ecosystem="cargo",
                                   source=source, dev=is_dev))
    return deps


_CSPROJ_PKG = re.compile(r"""<PackageReference\s+Include=["'](?P<name>[^"']+)["']"""
                         r"""(?:[^>]*Version=["'](?P<version>[^"']+)["'])?""", re.I)


def parse_csproj(text: str, source: str) -> list[Dependency]:
    return [
        Dependency(name=m.group("name"), version=m.group("version"),
                   ecosystem="nuget", source=source)
        for m in _CSPROJ_PKG.finditer(text)
    ]


# Filename -> parser dispatch. Checked against the lowercased basename.
def parse_manifest(rel_path: str, text: str) -> tuple[list[Dependency], dict]:
    """Parse any recognised manifest. Returns (dependencies, extra metadata)."""
    name = Path(rel_path).name.lower()

    if name.startswith("requirements") and name.endswith(".txt"):
        return parse_requirements_txt(text, rel_path), {}
    if name == "pyproject.toml":
        return parse_pyproject(text, rel_path), {}
    if name == "pipfile":
        try:
            data = tomllib.loads(text)
        except (tomllib.TOMLDecodeError, ValueError):
            return [], {}
        deps = [
            Dependency(name=n, version=v if isinstance(v, str) else None,
                       ecosystem="pypi", source=rel_path, dev=(field == "dev-packages"))
            for field in ("packages", "dev-packages")
            for n, v in (data.get(field, {}) or {}).items()
        ]
        return deps, {}
    if name == "package.json":
        deps, raw = parse_package_json(text, rel_path)
        return deps, raw
    if name == "pom.xml":
        return parse_pom_xml(text, rel_path), {}
    if name in {"build.gradle", "build.gradle.kts"}:
        return parse_gradle(text, rel_path), {}
    if name == "go.mod":
        return parse_go_mod(text, rel_path), {}
    if name == "gemfile":
        return parse_gemfile(text, rel_path), {}
    if name == "composer.json":
        return parse_composer_json(text, rel_path), {}
    if name == "cargo.toml":
        return parse_cargo_toml(text, rel_path), {}
    if name.endswith(".csproj") or name.endswith(".fsproj"):
        return parse_csproj(text, rel_path), {}
    return [], {}


MANIFEST_NAMES: frozenset[str] = frozenset({
    "pyproject.toml", "pipfile", "package.json", "pom.xml", "build.gradle",
    "build.gradle.kts", "go.mod", "gemfile", "composer.json", "cargo.toml",
})


def is_manifest(rel_path: str) -> bool:
    name = Path(rel_path).name.lower()
    return (
        name in MANIFEST_NAMES
        or (name.startswith("requirements") and name.endswith(".txt"))
        or name.endswith((".csproj", ".fsproj"))
    )
