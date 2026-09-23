"""Repository traversal: decide which files are worth reading.

Scanning a real repo naively is dominated by `node_modules`, build output and
vendored dependencies, so the walker prunes those directories before descending
rather than filtering afterwards.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..models import FileInfo

# Directories pruned before descending. Anything here is either generated,
# vendored, or a virtualenv - none of it describes the project's own design.
SKIP_DIRS: frozenset[str] = frozenset({
    ".git", ".hg", ".svn", ".idea", ".vscode", ".vs", ".claude",
    "node_modules", "bower_components", "jspm_packages",
    "venv", ".venv", "env", ".env.d", "virtualenv", "site-packages",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
    "dist", "build", "out", "target", "bin", "obj", ".next", ".nuxt",
    ".output", ".svelte-kit", ".parcel-cache", ".turbo", ".cache",
    "coverage", "htmlcov", ".nyc_output", "vendor", "third_party",
    ".terraform", ".serverless", ".aws-sam", "cdk.out",
    ".gradle", ".m2", "Pods", "DerivedData", ".dart_tool",
    "migrations_backup", ".expo", "storybook-static",
})

# Binary / generated extensions we never open.
SKIP_EXTS: frozenset[str] = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svg", ".avif",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp4", ".mp3", ".wav", ".avi", ".mov", ".webm", ".ogg",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar", ".jar", ".war",
    ".pdf", ".docx", ".xlsx", ".pptx",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".o", ".a", ".class", ".pyc",
    ".db", ".sqlite", ".sqlite3", ".mdb", ".parquet", ".avro",
    ".lock", ".map", ".min.js", ".min.css",
    ".pkl", ".joblib", ".h5", ".onnx", ".pt", ".pth", ".safetensors",
})

# Lockfiles carry dependency data but are enormous; we read manifests instead.
SKIP_FILES: frozenset[str] = frozenset({
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "Pipfile.lock", "composer.lock", "Cargo.lock", "gradle.lockfile",
    "bun.lockb", "go.sum",
})

EXT_LANGUAGE: dict[str, str] = {
    ".py": "Python", ".pyi": "Python",
    ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".mts": "TypeScript", ".cts": "TypeScript",
    ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin", ".scala": "Scala",
    ".go": "Go", ".rs": "Rust", ".rb": "Ruby", ".php": "PHP",
    ".cs": "C#", ".fs": "F#", ".vb": "VB.NET",
    ".c": "C", ".h": "C", ".cpp": "C++", ".cc": "C++", ".cxx": "C++", ".hpp": "C++",
    ".swift": "Swift", ".m": "Objective-C", ".mm": "Objective-C",
    ".dart": "Dart", ".ex": "Elixir", ".exs": "Elixir", ".erl": "Erlang",
    ".clj": "Clojure", ".hs": "Haskell", ".lua": "Lua", ".r": "R",
    ".sql": "SQL", ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell",
    ".ps1": "PowerShell", ".psm1": "PowerShell",
    ".vue": "Vue", ".svelte": "Svelte",
    ".tf": "Terraform", ".tfvars": "Terraform", ".hcl": "HCL",
    ".bicep": "Bicep",
    ".yml": "YAML", ".yaml": "YAML", ".json": "JSON", ".toml": "TOML",
    ".xml": "XML", ".html": "HTML", ".htm": "HTML",
    ".css": "CSS", ".scss": "SCSS", ".sass": "SCSS", ".less": "LESS",
    ".md": "Markdown", ".rst": "reStructuredText", ".txt": "Text",
    ".graphql": "GraphQL", ".gql": "GraphQL", ".proto": "Protobuf",
}

# Languages that count as "source" for the LOC / primary-language tally.
CODE_LANGUAGES: frozenset[str] = frozenset({
    "Python", "JavaScript", "TypeScript", "Java", "Kotlin", "Scala", "Go",
    "Rust", "Ruby", "PHP", "C#", "F#", "VB.NET", "C", "C++", "Swift",
    "Objective-C", "Dart", "Elixir", "Erlang", "Clojure", "Haskell", "Lua",
    "R", "Vue", "Svelte", "Shell", "PowerShell",
})

# Files always read in full regardless of extension - they define the project.
ALWAYS_READ: frozenset[str] = frozenset({
    "dockerfile", "makefile", "procfile", "jenkinsfile", "vagrantfile",
    "requirements.txt", "requirements-dev.txt", "pyproject.toml", "setup.py",
    "setup.cfg", "pipfile", "package.json", "pom.xml", "build.gradle",
    "build.gradle.kts", "go.mod", "cargo.toml", "gemfile", "composer.json",
    "readme.md", "readme.rst", "readme", "architecture.md", "design.md",
})

MAX_FILE_BYTES = 400_000  # anything larger is generated or a data blob


def _load_gitignore_spec(root: Path):
    """Build a pathspec matcher from .gitignore, if pathspec is installed.

    Returns None when unavailable - the static SKIP_DIRS list already handles
    the common cases, so a missing .gitignore matcher is a quality loss, not a
    correctness problem.
    """
    try:
        import pathspec  # noqa: PLC0415 - optional dependency
    except ImportError:
        return None

    patterns: list[str] = []
    for name in (".gitignore", ".archlensignore"):
        f = root / name
        if f.is_file():
            try:
                patterns.extend(f.read_text(encoding="utf-8", errors="ignore").splitlines())
            except OSError:
                continue
    if not patterns:
        return None
    try:
        return pathspec.PathSpec.from_lines("gitwildmatch", patterns)
    except Exception:
        return None


def detect_language(path: Path) -> str:
    name = path.name.lower()
    if name.startswith("dockerfile"):
        return "Dockerfile"
    if name in {"makefile", "gnumakefile"}:
        return "Makefile"
    if name == "jenkinsfile":
        return "Groovy"
    return EXT_LANGUAGE.get(path.suffix.lower(), "Other")


def should_read(path: Path, size: int) -> bool:
    """Whether the file's *contents* are worth loading into memory."""
    name = path.name.lower()
    if name in ALWAYS_READ or name.startswith("dockerfile"):
        return True
    if name in SKIP_FILES:
        return False
    suffix = path.suffix.lower()
    if suffix in SKIP_EXTS:
        return False
    if name.endswith((".min.js", ".min.css", ".bundle.js", ".chunk.js")):
        return False
    return size <= MAX_FILE_BYTES


def read_text(path: Path) -> str:
    """Read a text file, tolerating any encoding mess a real repo contains.

    `utf-8-sig` rather than `utf-8`: Windows editors and PowerShell's `Out-File`
    write a UTF-8 BOM, and a leading U+FEFF silently breaks `json.loads` and
    every anchored regex in the manifest parsers. Stripping it here fixes all of
    them at once.
    """
    try:
        return path.read_text(encoding="utf-8-sig", errors="ignore")
    except (OSError, ValueError):
        return ""


def walk(root: Path, max_files: int = 20_000) -> list[FileInfo]:
    """Enumerate candidate files under `root`, pruning noise directories.

    `max_files` is a guard against pointing the tool at a home directory - it
    stops enumeration rather than failing, and the caller surfaces a warning.
    """
    spec = _load_gitignore_spec(root)
    found: list[FileInfo] = []

    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        current = Path(dirpath)

        # Prune in place so os.walk never descends into them.
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS and not (d.startswith(".") and d not in {".github", ".circleci"})
        ]
        if spec is not None:
            rel_dir = current.relative_to(root).as_posix()
            dirnames[:] = [
                d for d in dirnames
                if not spec.match_file(f"{rel_dir}/{d}/" if rel_dir != "." else f"{d}/")
            ]

        for filename in filenames:
            if len(found) >= max_files:
                return found
            fpath = current / filename
            try:
                size = fpath.stat().st_size
            except OSError:
                continue

            rel = fpath.relative_to(root).as_posix()
            if spec is not None and spec.match_file(rel):
                continue
            if not should_read(fpath, size):
                continue

            found.append(FileInfo(path=rel, language=detect_language(fpath), size=size))

    return found


# Directory names and filename shapes that mark test code, across ecosystems.
_TEST_DIRS = frozenset({
    "test", "tests", "testing", "__tests__", "spec", "specs", "e2e",
    "integration_tests", "unit_tests", "fixtures", "testdata", "mocks",
})
_TEST_FILE_PREFIXES = ("test_", "conftest")
_TEST_FILE_SUFFIXES = (
    "_test.py", "_test.go", "_test.rb", "_tests.py",
    ".test.ts", ".test.tsx", ".test.js", ".test.jsx",
    ".spec.ts", ".spec.tsx", ".spec.js", ".spec.jsx",
    "test.java", "tests.cs",
)


def is_test_file(rel_path: str) -> bool:
    """Whether a path is test code rather than production code.

    Test files routinely contain SDK call shapes, connection strings and
    fixture payloads for services the production system does not use - this
    project's own test suite embeds `boto3.client("s3")` as a string literal.
    Treating them like dev dependencies (scanned for evidence, but not allowed
    to originate a component) keeps fixtures out of the architecture.
    """
    parts = rel_path.lower().split("/")
    if any(part in _TEST_DIRS for part in parts[:-1]):
        return True
    name = parts[-1]
    return name.startswith(_TEST_FILE_PREFIXES) or name.endswith(_TEST_FILE_SUFFIXES)


def count_loc(text: str) -> int:
    """Non-blank, non-trivially-commented line count."""
    total = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("#", "//", "*", "/*", "<!--", "--")):
            continue
        total += 1
    return total
