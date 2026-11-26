"""Command-line interface."""

from __future__ import annotations

import shutil
from pathlib import Path

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from color_book_generator import __version__
from color_book_generator.config import BookConfig, load_book_config, load_env_settings
from color_book_generator.pipeline import compose_from_pages
from color_book_generator.pipeline import generate as run_generate
from color_book_generator.prompts import load_subjects
from color_book_generator.providers import available_providers

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Generate print-ready line-art coloring books.",
)
console = Console()

auth_app = typer.Typer(no_args_is_help=True, help="Authentication for image providers.")
app.add_typer(auth_app, name="auth")

DEFAULT_CONFIG = Path("config/book.yaml")


def _load(config: Path) -> BookConfig:
    if not config.exists():
        console.print(f"[red]config not found:[/red] {config}")
        raise typer.Exit(1)
    return load_book_config(config)


@app.command()
def generate(
    config: Path = typer.Option(DEFAULT_CONFIG, "--config", "-c"),
    pages: int | None = typer.Option(None, "--pages", "-p", help="Override page count"),
    provider: str | None = typer.Option(None, "--provider"),
    model: str | None = typer.Option(None, "--model"),
    subjects: Path | None = typer.Option(None, "--subjects", help="File with one subject per line"),
    out: Path = typer.Option(Path("output"), "--out"),
    run_name: str | None = typer.Option(None, "--run-name"),
    resume: bool = typer.Option(False, "--resume", help="Skip pages already generated"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Only build prompts"),
) -> None:
    """Generate all pages and compose the PDF."""
    cfg = _load(config)
    env = load_env_settings()
    cfg.provider = provider or env.provider or cfg.provider
    cfg.model = model or env.model or cfg.model
    if pages is not None:
        cfg.pages = pages
    subject_list = load_subjects(subjects)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("generating", total=cfg.pages)

        def on_page(index: int, total: int) -> None:
            progress.update(task, completed=index)

        result = run_generate(
            cfg,
            subjects=subject_list,
            out_dir=out,
            run_name=run_name,
            resume=resume,
            dry_run=dry_run,
            on_page=on_page,
            on_message=lambda m: progress.console.log(m),
        )

    console.print(f"\n[green]done[/green] -> {result.run.pdf if not dry_run else result.run.root}")


@app.command()
def compose(
    pages_dir: Path = typer.Argument(..., help="Directory with page-*.png files"),
    out: Path = typer.Option(Path("book.pdf"), "--out", "-o"),
    config: Path = typer.Option(DEFAULT_CONFIG, "--config", "-c"),
) -> None:
    """Compose a PDF from already-processed pages."""
    cfg = _load(config)
    path = compose_from_pages(pages_dir, out, cfg)
    console.print(f"[green]pdf[/green] -> {path}")


@auth_app.command("codex")
def auth_codex() -> None:
    """Log in to ChatGPT Plus via device code and use your Codex plan."""
    from color_book_generator.auth.codex import authenticate, is_authenticated

    console.print("Aguardando autorizacao no navegador. Ctrl+C cancela.")
    authenticate(on_wait=lambda: None)
    if not is_authenticated():
        console.print("[red]autenticacao nao concluida[/red]")
        raise typer.Exit(1)
    console.print("[green]ChatGPT Plus autenticado com sucesso.[/green]")


@auth_app.command("status")
def auth_status() -> None:
    """Show provider authentication status."""
    from color_book_generator.auth.codex import is_authenticated

    state = "conectado" if is_authenticated() else "desconectado"
    console.print(f"codex: {state}")


@auth_app.command("models")
def auth_models() -> None:
    """List Codex models available to your ChatGPT account."""
    from color_book_generator.auth.codex import available_models

    models = available_models()
    if not models:
        console.print("[yellow]nenhum modelo encontrado (nao autenticado?)[/yellow]")
        raise typer.Exit(1)
    for slug in models:
        console.print(slug)


@auth_app.command("logout")
def auth_logout() -> None:
    """Remove stored ChatGPT Plus credentials."""
    from color_book_generator.auth.codex import clear_tokens

    clear_tokens()
    console.print("[green]credenciais removidas[/green]")


@app.command("init")
def init_config(
    path: Path = typer.Option(DEFAULT_CONFIG, "--path"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Create a starter book.yaml."""
    if path.exists() and not force:
        console.print(f"[yellow]exists[/yellow] {path} (use --force)")
        raise typer.Exit(1)
    source = Path(__file__).resolve().parents[2] / "config" / "book.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    if source.exists():
        shutil.copy(source, path)
    else:  # pragma: no cover
        path.write_text("title: My Coloring Book\ntheme: cute animals\n", encoding="utf-8")
    console.print(f"[green]created[/green] {path}")


@app.command()
def providers() -> None:
    """List available image providers."""
    table = Table("provider")
    for name in available_providers():
        table.add_row(name)
    console.print(table)


@app.command()
def show(config: Path = typer.Option(DEFAULT_CONFIG, "--config", "-c")) -> None:
    """Show the resolved configuration."""
    cfg = _load(config)
    w, h = cfg.trim_size_in
    px = cfg.image_px
    table = Table(show_header=False)
    table.add_row("title", cfg.title)
    table.add_row("pages", str(cfg.pages))
    table.add_row("trim", f"{w} x {h} in")
    table.add_row("bleed", f"{cfg.bleed_in} in")
    table.add_row("image", f"{px[0]} x {px[1]} px @ {cfg.dpi} dpi")
    table.add_row("provider", f"{cfg.provider} / {cfg.model}")
    table.add_row("theme", cfg.theme)
    console.print(table)


@app.command()
def web(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
    reload: bool = typer.Option(False, "--reload"),
) -> None:
    """Serve the web interface for page-by-page generation."""
    try:
        import uvicorn
    except ImportError:
        console.print("[red]web extra not installed:[/red] uv sync --extra web")
        raise typer.Exit(1) from None
    console.print(f"[green]web[/green] http://{host}:{port}")
    uvicorn.run(
        "color_book_generator.web.app:app", host=host, port=port, reload=reload
    )


@app.command()
def version() -> None:
    console.print(__version__)


if __name__ == "__main__":
    app()
