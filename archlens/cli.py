"""Command-line entry point.

`archlens serve` is the primary interface; `archlens scan` exists so the same
pipeline can run in CI and write artifacts to disk without a browser.
"""

from __future__ import annotations

import json
import logging
import sys
import webbrowser
from pathlib import Path

import typer

from . import __version__

cli = typer.Typer(
    name="archlens",
    help="Scan a codebase, understand it, and draw the architecture.",
    add_completion=False,
    no_args_is_help=True,
)


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)-8s %(name)s: %(message)s",
    )
    if not verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)


@cli.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Interface to bind."),
    port: int = typer.Option(8420, help="Port to listen on."),
    output: Path = typer.Option(Path.cwd() / "archlens-output",
                                help="Where rendered diagrams are written."),
    open_browser: bool = typer.Option(True, "--open/--no-open",
                                      help="Open the UI in your browser."),
    reload: bool = typer.Option(False, help="Auto-reload on code changes (development)."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Start the local web UI."""
    _configure_logging(verbose)

    try:
        import uvicorn
    except ImportError:
        typer.secho("uvicorn is not installed. Run: pip install 'uvicorn[standard]'",
                    fg=typer.colors.RED)
        raise typer.Exit(1) from None

    from .server.app import create_app

    url = f"http://{'localhost' if host in {'127.0.0.1', '0.0.0.0'} else host}:{port}"
    typer.secho(f"\n  ArchLens  →  {url}\n", fg=typer.colors.BRIGHT_CYAN, bold=True)
    _print_capabilities()

    if open_browser:
        # Fire after the server is listening; a thread timer is enough here.
        import threading
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    if reload:
        uvicorn.run("archlens.server.app:app", host=host, port=port,
                    reload=True, log_level="debug" if verbose else "info")
    else:
        uvicorn.run(create_app(output), host=host, port=port,
                    log_level="debug" if verbose else "info")


@cli.command()
def scan(
    path: Path = typer.Argument(..., help="Repository to analyse."),
    output: Path = typer.Option(Path.cwd() / "archlens-output", "--output", "-o",
                                help="Directory for diagrams and the JSON model."),
    no_llm: bool = typer.Option(False, "--no-llm", help="Skip the Claude pass."),
    dark: bool = typer.Option(False, "--dark", help="Render on a dark canvas."),
    direction: str = typer.Option("LR", help="Diagram direction: LR or TB."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Analyse a repository and write the diagrams to disk."""
    _configure_logging(verbose)

    if not path.is_dir():
        typer.secho(f"Not a directory: {path}", fg=typer.colors.RED)
        raise typer.Exit(1)

    from .pipeline import analyse

    out_dir = output / path.resolve().name
    last_line = {"text": ""}

    def progress(message: str, fraction: float) -> None:
        line = f"  [{int(fraction * 100):3d}%] {message}"
        # Overwrite in place so the terminal shows one live status line.
        padding = max(0, len(last_line["text"]) - len(line))
        sys.stdout.write("\r" + line + " " * padding)
        sys.stdout.flush()
        last_line["text"] = line

    typer.secho(f"\nAnalysing {path.resolve()}", fg=typer.colors.BRIGHT_CYAN, bold=True)
    result = analyse(
        path, out_dir,
        use_llm=not no_llm,
        dark=dark,
        direction=direction.upper(),
        progress=progress,
    )
    sys.stdout.write("\r" + " " * (len(last_line["text"]) + 2) + "\r")

    model_path = out_dir / "architecture.json"
    model_path.write_text(
        json.dumps(result.model_dump(mode="json", exclude={"evidence": {"files"}}), indent=2),
        encoding="utf-8",
    )

    for flow in result.architecture.flows:
        (out_dir / f"flow-{flow.id}.mmd").write_text(flow.mermaid, encoding="utf-8")

    arch = result.architecture
    typer.secho(f"\n{arch.name}", fg=typer.colors.BRIGHT_WHITE, bold=True)
    if arch.summary:
        typer.echo(f"  {arch.summary}\n")
    typer.echo(f"  Components : {len(arch.components)}")
    typer.echo(f"  Connections: {len(arch.edges)}")
    typer.echo(f"  Flowcharts : {len(arch.flows)}")
    typer.echo(f"  Analysis   : {'Claude + static' if result.used_llm else 'static only'}")
    typer.echo(f"  Duration   : {result.duration_seconds}s")

    typer.secho("\n  Written to " + str(out_dir.resolve()), fg=typer.colors.GREEN)
    for diagram in result.diagrams:
        for fmt in ("png", "svg"):
            name = getattr(diagram, fmt)
            if name:
                typer.echo(f"    {out_dir / name}")
    typer.echo(f"    {model_path}")

    if result.warnings:
        typer.secho("\n  Notes:", fg=typer.colors.YELLOW)
        for warning in result.warnings:
            typer.echo(f"    - {warning}")
    typer.echo()


@cli.command()
def doctor() -> None:
    """Check that every optional dependency is installed and working."""
    _print_capabilities(detailed=True)


def _print_capabilities(detailed: bool = False) -> None:
    from .inference import llm
    from .render.architecture import graphviz_available
    from .render.icons import available as icons_available

    checks = [
        ("diagrams (icon packs)", icons_available(), "pip install diagrams"),
        ("Graphviz `dot` binary", graphviz_available(),
         "conda install -c conda-forge graphviz  —  or  https://graphviz.org/download/"),
        ("anthropic SDK", llm.sdk_available(), "pip install anthropic"),
        ("Anthropic credentials", llm.api_key_available(), "set ANTHROPIC_API_KEY"),
    ]

    for label, ok, remedy in checks:
        mark = "OK  " if ok else "MISS"
        colour = typer.colors.GREEN if ok else typer.colors.YELLOW
        typer.secho(f"  [{mark}] {label}", fg=colour)
        if not ok:
            typer.secho(f"         → {remedy}", fg=typer.colors.BRIGHT_BLACK)

    if detailed:
        typer.echo(f"\n  archlens {__version__}")
        typer.echo(f"  python   {sys.version.split()[0]}")
        missing_hard = not icons_available() or not graphviz_available()
        if missing_hard:
            typer.secho(
                "\n  Diagrams cannot be rendered until both `diagrams` and Graphviz "
                "are installed.\n  Everything else (analysis, flowcharts, JSON model) "
                "still works.", fg=typer.colors.YELLOW,
            )
    typer.echo()


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
