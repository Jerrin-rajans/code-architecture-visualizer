"""End-to-end orchestration: a path in, diagrams and a model out.

The pipeline is deliberately linear and each stage degrades independently:
a Claude failure falls back to static inference, and a Graphviz failure still
leaves you the architecture model and the Mermaid flowcharts. You never get
nothing back.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from .inference import flows as flow_builder
from .inference import heuristics, llm
from .models import Architecture, Evidence, ScanResult
from .render import architecture as arch_render
from .render.icons import available as icons_available
from .scanner import scan as scan_repo

log = logging.getLogger(__name__)

ProgressFn = Callable[[str, float], None]


def analyse(
    root: str | Path,
    out_dir: str | Path,
    *,
    use_llm: bool = True,
    render: bool = True,
    dark: bool = False,
    direction: str = "LR",
    job_id: str | None = None,
    progress: ProgressFn | None = None,
    max_files: int = 20_000,
) -> ScanResult:
    """Scan `root`, build an architecture, render diagrams into `out_dir`."""
    started = time.perf_counter()
    job_id = job_id or uuid.uuid4().hex[:12]
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []

    def report(message: str, fraction: float) -> None:
        if progress:
            progress(message, min(max(fraction, 0.0), 1.0))

    # ---- 1. Static scan -----------------------------------------------------
    report("Scanning repository", 0.02)
    evidence: Evidence = scan_repo(root, progress=report, max_files=max_files)
    warnings.extend(evidence.notes)

    # ---- 2. Static inference (always - it is also the LLM's baseline) -------
    report("Deriving component model", 0.80)
    architecture: Architecture = heuristics.build_architecture(evidence)
    static_flows = flow_builder.build_flows(evidence, architecture)
    used_llm = False

    # ---- 3. Claude refinement -----------------------------------------------
    if use_llm:
        if not llm.sdk_available():
            warnings.append("`anthropic` is not installed — ran static analysis only. "
                            "Install it with `pip install anthropic`.")
        elif not llm.api_key_available():
            warnings.append("No Anthropic credentials found — ran static analysis only. "
                            "Set ANTHROPIC_API_KEY to enable semantic analysis.")
        else:
            result = llm.enrich(evidence, architecture, progress=report)
            warnings.extend(result.warnings)
            if result.architecture is not None:
                architecture = result.architecture
                used_llm = True
            if result.flows:
                architecture.flows = result.flows
                used_llm = True
            if result.input_tokens or result.cached_tokens:
                log.info(
                    "Claude usage: %d input (%d cached), %d output",
                    result.input_tokens, result.cached_tokens, result.output_tokens,
                )

    # Always keep the generated system flow - it mirrors the final architecture
    # and is the one diagram guaranteed to be consistent with it.
    system_flow = flow_builder.system_flow(architecture)
    existing_ids = {f.id for f in architecture.flows}
    if not architecture.flows:
        architecture.flows = static_flows
    elif system_flow.id not in existing_ids:
        architecture.flows.append(system_flow)

    # ---- 4. Render -----------------------------------------------------------
    diagrams: list = []
    if render:
        report("Rendering architecture diagram", 0.94)
        if not icons_available():
            warnings.append("`diagrams` is not installed — no image was rendered. "
                            "Install it with `pip install diagrams`.")
        else:
            try:
                diagrams.append(arch_render.render_architecture(
                    architecture, out_path, basename="architecture",
                    direction=direction, dark=dark,
                ))
            except arch_render.GraphvizMissingError as exc:
                warnings.append(str(exc))
            except Exception as exc:
                log.warning("Architecture render failed", exc_info=True)
                warnings.append(f"Architecture diagram failed to render: {exc}")

            # The context view is a nice-to-have; never fail the scan over it.
            if len(architecture.components) > 6:
                report("Rendering context diagram", 0.97)
                try:
                    context = arch_render.render_context_diagram(
                        architecture, out_path, basename="context", dark=dark,
                    )
                    if not context.error:
                        diagrams.append(context)
                except Exception:
                    log.info("Context diagram skipped", exc_info=True)

    report("Done", 1.0)
    return ScanResult(
        job_id=job_id,
        root=str(Path(root).resolve()),
        architecture=architecture,
        evidence=evidence,
        diagrams=diagrams,
        duration_seconds=round(time.perf_counter() - started, 2),
        used_llm=used_llm,
        warnings=_dedupe(warnings),
    )


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
