"""Regression tests for bugs found by dogfooding.

Each test names the bug it pins down. These are the failures that were silent -
they produced a plausible-looking diagram that was quietly wrong - so they are
exactly the ones worth guarding.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from archlens.inference import heuristics
from archlens.models import MIN_COMPONENT_CONFIDENCE, Provider
from archlens.render.architecture import inline_svg_images
from archlens.render.icons import resolve
from archlens.scanner import scan
from archlens.scanner.manifests import parse_manifest
from archlens.scanner.walker import read_text

# --------------------------------------------------------------------------- #
# Bug 1: a UTF-8 BOM silently emptied every manifest parser.
#
# PowerShell's `Out-File -Encoding utf8` and most Windows editors prepend
# U+FEFF. `json.loads` raises on it and the anchored requirements regex fails to
# match the first line, so `package.json` yielded zero dependencies and the
# frontend vanished from the diagram - with no error anywhere.
# --------------------------------------------------------------------------- #


def test_bom_prefixed_package_json_still_parses(tmp_path: Path) -> None:
    manifest = tmp_path / "package.json"
    payload = {"name": "web", "dependencies": {"react": "^18.2.0", "next": "^14.1.0"}}
    manifest.write_bytes(b"\xef\xbb\xbf" + json.dumps(payload).encode("utf-8"))

    deps, _ = parse_manifest("package.json", read_text(manifest))

    assert {d.name for d in deps} == {"react", "next"}


def test_bom_prefixed_requirements_still_parses(tmp_path: Path) -> None:
    manifest = tmp_path / "requirements.txt"
    manifest.write_bytes("\ufefffastapi==0.110.0\nredis==5.0.3\n".encode())

    deps, _ = parse_manifest("requirements.txt", read_text(manifest))

    # The BOM sits on the first line, so a regression drops `fastapi` only.
    assert {d.name for d in deps} == {"fastapi", "redis"}


# --------------------------------------------------------------------------- #
# Bug 2: content probes invented infrastructure.
#
# Scanning ArchLens itself reported "runs on AWS, using Amazon S3" - because
# its own catalog.py contains the `s3://` detection pattern. A regex hit in
# source is weak evidence: it fires on comments, fixtures and code that merely
# names the string. Probes must corroborate, never originate.
# --------------------------------------------------------------------------- #


def test_content_probe_alone_does_not_invent_a_component(tmp_path: Path) -> None:
    (tmp_path / "patterns.py").write_text(
        'CONNECTION_HINTS = ["s3://", "postgres://", "amqp://"]\n',
        encoding="utf-8",
    )

    evidence = scan(tmp_path)
    architecture = heuristics.build_architecture(evidence)

    probe_services = {s.service for s in evidence.signals}
    assert "aws.s3" in probe_services, "the probe should still fire and be recorded"
    assert all(
        s.confidence < MIN_COMPONENT_CONFIDENCE
        for s in evidence.signals
        if s.service in {"aws.s3", "onprem.postgresql", "onprem.rabbitmq"}
    ), "probe-only signals must stay below the component threshold"

    assert evidence.primary_provider is Provider.ONPREM
    assert not any(c.service.startswith("aws.") for c in architecture.components)


def test_corroborated_probe_does_become_a_component(tmp_path: Path) -> None:
    """The flip side: real usage must still be detected."""
    (tmp_path / "requirements.txt").write_text("boto3==1.34.0\n", encoding="utf-8")
    (tmp_path / "store.py").write_text(
        'import boto3\ns3 = boto3.client("s3")\n', encoding="utf-8"
    )

    evidence = scan(tmp_path)

    s3 = next(s for s in evidence.signals if s.service == "aws.s3")
    assert s3.confidence >= MIN_COMPONENT_CONFIDENCE
    assert evidence.primary_provider is Provider.AWS


def test_test_code_does_not_originate_components(tmp_path: Path) -> None:
    """Fixtures are not production architecture.

    This suite embeds `boto3.client("s3")` as a string literal, which made
    ArchLens report itself as running on AWS.
    """
    (tmp_path / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_storage.py").write_text(
        'FIXTURE = \'s3 = boto3.client("s3")\'\n', encoding="utf-8"
    )

    evidence = scan(tmp_path)

    s3 = next((s for s in evidence.signals if s.service == "aws.s3"), None)
    assert s3 is not None, "the probe should still fire and be visible as evidence"
    assert s3.confidence < MIN_COMPONENT_CONFIDENCE
    assert evidence.primary_provider is Provider.ONPREM


def test_production_code_still_originates_components(tmp_path: Path) -> None:
    """The same call outside a test directory must still count."""
    (tmp_path / "storage.py").write_text(
        'import boto3\ns3 = boto3.client("s3")\n', encoding="utf-8"
    )

    evidence = scan(tmp_path)

    s3 = next(s for s in evidence.signals if s.service == "aws.s3")
    assert s3.confidence >= MIN_COMPONENT_CONFIDENCE
    assert evidence.primary_provider is Provider.AWS


@pytest.mark.parametrize(
    "path",
    [
        "tests/test_api.py", "test/helpers.py", "src/__tests__/app.test.ts",
        "pkg/service_test.go", "app/foo.spec.ts", "conftest.py",
        "spec/models_spec.rb", "testdata/seed.json",
    ],
)
def test_test_paths_are_recognised(path: str) -> None:
    from archlens.scanner.walker import is_test_file

    assert is_test_file(path)


@pytest.mark.parametrize(
    "path",
    ["src/app.py", "archlens/scanner/code.py", "latest_news.py", "contest.py"],
)
def test_production_paths_are_not_mistaken_for_tests(path: str) -> None:
    from archlens.scanner.walker import is_test_file

    assert not is_test_file(path)


# --------------------------------------------------------------------------- #
# Comments are prose, not code.
#
# ArchLens reported itself as running on S3 because catalog.py contains a
# comment *explaining* the S3 probe: `boto3.client("s3")`. Commented-out code
# and docstring examples are the same class of false positive.
# --------------------------------------------------------------------------- #


def test_commented_out_code_is_not_evidence(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(
        '# We used to do: s3 = boto3.client("s3")\n'
        '"""Docs: call boto3.client("dynamodb") to reach the table."""\n'
        "def main():\n    return 1\n",
        encoding="utf-8",
    )

    evidence = scan(tmp_path)

    strong = {s.service for s in evidence.signals if s.confidence >= MIN_COMPONENT_CONFIDENCE}
    assert "aws.s3" not in strong
    assert "aws.dynamodb" not in strong


def test_real_calls_survive_comment_stripping(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(
        '# Storage layer.\nimport boto3\ns3 = boto3.client("s3")  # cached client\n',
        encoding="utf-8",
    )

    evidence = scan(tmp_path)

    s3 = next(s for s in evidence.signals if s.service == "aws.s3")
    assert s3.confidence >= MIN_COMPONENT_CONFIDENCE


def test_connection_strings_inside_literals_survive(tmp_path: Path) -> None:
    """String literals are preserved - only comments are stripped."""
    (tmp_path / "settings.py").write_text(
        'DATABASE_URL = "postgres://user:pw@db:5432/app"\n', encoding="utf-8"
    )

    evidence = scan(tmp_path)

    assert any(s.service == "onprem.postgresql" for s in evidence.signals)


@pytest.mark.parametrize(
    ("language", "source", "must_keep", "must_drop"),
    [
        # A `#` inside a JS string is a colour, not a comment.
        ("JavaScript", 'const c = "#fff"; // boto3.client("s3")', "#fff", 's3"'),
        # A `//` inside a URL must not start a comment.
        ("JavaScript", 'const u = "https://api.example.com/x";', "https://api", None),
        ("Python", 'URL = "http://h#frag"  # boto3.client("sqs")', "#frag", "sqs"),
        ("Go", 'v := "x" /* boto3.client("sns") */', '"x"', "sns"),
    ],
)
def test_comment_stripping_is_quote_aware(
    language: str, source: str, must_keep: str, must_drop: str | None
) -> None:
    from archlens.scanner.text import strip_comments

    result = strip_comments(source, language)

    assert must_keep in result
    if must_drop is not None:
        assert must_drop not in result
    assert len(result) == len(source), "offsets must be preserved"


def test_provider_matches_the_components_actually_drawn(tmp_path: Path) -> None:
    """The icon family must reflect the diagram, not filtered-out evidence."""
    (tmp_path / "requirements.txt").write_text("fastapi==0.110.0\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("import fastapi\n", encoding="utf-8")

    architecture = heuristics.build_architecture(scan(tmp_path))

    drawn = {c.service.split(".", 1)[0] for c in architecture.components}
    assert architecture.provider.value not in {"aws", "azure", "gcp"} or (
        architecture.provider.value in drawn
    )


def test_documentation_is_not_probed(tmp_path: Path) -> None:
    """Prose mentioning a technology is not a system using it."""
    (tmp_path / "README.md").write_text(
        "We store uploads at s3:// and cache in redis://\n", encoding="utf-8"
    )

    evidence = scan(tmp_path)

    assert not any(s.service in {"aws.s3", "onprem.redis"} for s in evidence.signals)


# --------------------------------------------------------------------------- #
# Bug 3: SVG icons were unreachable outside the rendering machine.
#
# Graphviz emits `<image xlink:href="C:\...\resources\aws\compute\lambda.png">`.
# That resolves only on the box that rendered it - a browser loading the SVG
# over HTTP silently drops every icon, which is the entire point of the tool.
# --------------------------------------------------------------------------- #


def test_svg_image_references_are_inlined(tmp_path: Path) -> None:
    icon = tmp_path / "icon.png"
    # A one-pixel PNG is enough; we assert on the encoding, not the artwork.
    icon.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d494844520000000100000001080600000"
            "01f15c4890000000a49444154789c6300010000050001"
            "0d0a2db40000000049454e44ae426082"
        )
    )
    svg = tmp_path / "diagram.svg"
    # Graphviz mixes separators on Windows, so exercise a mixed path.
    mixed = str(icon).replace("\\", "/", 1)
    svg.write_text(
        f'<svg xmlns:xlink="http://www.w3.org/1999/xlink">'
        f'<image xlink:href="{mixed}" width="10" height="10"/></svg>',
        encoding="utf-8",
    )

    embedded = inline_svg_images(svg)
    result = svg.read_text(encoding="utf-8")

    assert embedded == 1
    assert "data:image/png;base64," in result
    # No filesystem path may survive. Assert on the href values specifically -
    # the SVG's own xmlns declaration legitimately contains "http://".
    hrefs = re.findall(r'(?:xlink:)?href="([^"]*)"', result)
    assert hrefs and all(h.startswith("data:") for h in hrefs)
    assert str(tmp_path) not in result


def test_inlining_leaves_remote_and_data_urls_alone(tmp_path: Path) -> None:
    svg = tmp_path / "d.svg"
    original = (
        '<svg><image href="https://example.com/a.png"/>'
        '<image href="data:image/png;base64,AAAA"/></svg>'
    )
    svg.write_text(original, encoding="utf-8")

    assert inline_svg_images(svg) == 0
    assert svg.read_text(encoding="utf-8") == original


# --------------------------------------------------------------------------- #
# Bug 4: icon resolution fell back to the wrong vendor's logo.
#
# `saas.stripe` was mapped to the Facebook icon as a stand-in. Showing one
# company's logo for another is worse than showing no logo at all.
# --------------------------------------------------------------------------- #


# Expected class names come from the installed `diagrams` release. Several
# differ from the obvious spelling (`S3` is `SimpleStorageServiceS3`,
# `Deployment` is `Deploy`), which is exactly why the registry stores candidate
# lists rather than a single name - the second candidate is what resolves here.
@pytest.mark.parametrize(
    ("service", "expected_module", "expected_names"),
    [
        ("aws.lambda", "diagrams.aws.compute", {"Lambda"}),
        ("aws.s3", "diagrams.aws.storage", {"S3", "SimpleStorageServiceS3"}),
        ("aws.dynamodb", "diagrams.aws.database", {"Dynamodb", "DynamodbTable"}),
        ("azure.functions", "diagrams.azure.compute", {"FunctionApps", "FunctionApp"}),
        ("gcp.run", "diagrams.gcp.compute", {"Run"}),
        ("onprem.postgresql", "diagrams.onprem.database", {"PostgreSQL", "Postgresql"}),
        ("onprem.kafka", "diagrams.onprem.queue", {"Kafka"}),
        ("k8s.deployment", "diagrams.k8s.compute", {"Deployment", "Deploy"}),
    ],
)
def test_core_services_resolve_to_their_real_icon(
    service: str, expected_module: str, expected_names: set[str]
) -> None:
    node = resolve(service)
    assert node.__module__ == expected_module
    assert node.__name__ in expected_names


def test_no_service_borrows_another_vendors_logo() -> None:
    """Every brand key must resolve to itself or to a neutral placeholder."""
    from archlens.render.icons import ICON_REGISTRY

    forbidden = {"Facebook", "Twitter", "Twilio"}
    for service in ICON_REGISTRY:
        vendor = service.split(".", 1)[-1]
        name = resolve(service).__name__
        if name in forbidden and vendor.lower() not in name.lower():
            pytest.fail(f"{service} resolves to unrelated vendor logo {name}")


def test_services_without_a_diagrams_icon_get_our_own_tile() -> None:
    """Stripe et al. must render a real tile, not an unlabelled blank box."""
    from archlens.render.icons import ASSETS_DIR, CUSTOM_ICONS

    for service, asset in CUSTOM_ICONS.items():
        assert (ASSETS_DIR / f"{asset}.png").is_file(), f"missing asset for {service}"
        node = resolve(service)
        assert node.__name__ == f"Custom_{asset}", f"{service} did not use its own tile"


def test_unknown_service_falls_back_by_role_not_by_accident() -> None:
    from archlens.models import ComponentKind

    assert resolve("aws.not_a_real_service", ComponentKind.DATABASE) is not None
    assert resolve("totally.unknown", ComponentKind.QUEUE).__name__ in {
        "RabbitMQ", "Rabbitmq", "Blank",
    }
