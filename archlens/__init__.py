"""ArchLens — scan a codebase, understand it, and draw the architecture.

Typical use:

    from archlens.pipeline import analyse

    result = analyse("/path/to/repo", "./out")
    print(result.architecture.summary)

Or from the shell:

    archlens serve          # local web UI
    archlens scan ./myrepo  # write diagrams to disk
    archlens doctor         # check optional dependencies
"""

from __future__ import annotations

__version__ = "1.0.0"

__all__ = ["__version__", "analyse", "scan"]


def __getattr__(name: str):
    # Lazy re-exports: importing `archlens` should not drag in FastAPI,
    # the diagrams icon packs or the Anthropic SDK.
    if name == "analyse":
        from .pipeline import analyse

        return analyse
    if name == "scan":
        from .scanner import scan

        return scan
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
