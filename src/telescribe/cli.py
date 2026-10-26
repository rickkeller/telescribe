"""Typer command line interface for the archive MVP."""

from pathlib import Path
from datetime import date as date_type
import re

import typer

from .config import load_config
from .pipeline import ArchiveService
from .state_store import StateStore
from .intelligence import ReviewService, create_direct_deepseek_classifier

app = typer.Typer(no_args_is_help=True, help="Local-first Telegram Desktop daily archiver.")


@app.command()
def process(
    config: Path = typer.Option(..., "--config", exists=True, readable=True),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan only; do not write files or state."),
) -> None:
    """Archive uncheckpointed messages from enabled configured sources."""
    result = ArchiveService(load_config(config)).process(dry_run=dry_run)
    mode = "dry-run" if result.dry_run else "completed"
    typer.echo(f"{mode}: {result.message_count} message(s), {len(result.changed_files)} changed file(s)")


@app.command()
def reprocess(
    config: Path = typer.Option(..., "--config", exists=True, readable=True),
    date: str | None = typer.Option(None, "--date", help="Local archive date (YYYY-MM-DD)."),
    source: str | None = typer.Option(None, "--source", help="Enabled configured source ID."),
    all_dates: bool = typer.Option(False, "--all", help="Rebuild every indexed daily archive."),
) -> None:
    """Rebuild selected existing daily archives from indexed records."""
    if sum((bool(date), bool(source), all_dates)) != 1:
        raise typer.BadParameter("provide exactly one of --date YYYY-MM-DD, --source ID, or --all")
    if date:
        try:
            date_type.fromisoformat(date)
        except ValueError as error:
            raise typer.BadParameter("--date must use YYYY-MM-DD") from error
    result = ArchiveService(load_config(config)).reprocess(date=date, source=source, all_dates=all_dates)
    typer.echo(f"completed: {result.message_count} message(s), {len(result.changed_files)} changed file(s)")


@app.command()
def review(
    config: Path = typer.Option(..., "--config", exists=True, readable=True),
    date: str | None = typer.Option(None, "--date", help="Local archive date (YYYY-MM-DD)."),
    source: str | None = typer.Option(None, "--source", help="Enabled configured source ID."),
    all_dates: bool = typer.Option(False, "--all", help="Review every indexed message."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan only; do not write queue or audit."),
    dispatch: bool = typer.Option(False, "--dispatch", help="Explicitly allow the approved direct provider request."),
) -> None:
    """Generate review-only proposals from the indexed message archive."""
    if sum((bool(date), bool(source), all_dates)) != 1:
        raise typer.BadParameter("provide exactly one of --date YYYY-MM-DD, --source ID, or --all")
    if dry_run and dispatch:
        raise typer.BadParameter("--dry-run and --dispatch are mutually exclusive")
    if date:
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                raise ValueError
            date_type.fromisoformat(date)
        except ValueError as error:
            raise typer.BadParameter("--date must use strict YYYY-MM-DD calendar date") from error
    try:
        settings = load_config(config)
        classifier = create_direct_deepseek_classifier(settings, dispatch=dispatch) if dispatch else None
        result = ReviewService(settings, classifier).review(date=date, source=source, all_dates=all_dates, dry_run=dry_run)
    except ValueError as error:
        typer.echo(f"review unavailable: {error}", err=True)
        raise typer.Exit(code=2) from error
    typer.echo(f"{'dry-run' if dry_run else 'completed'}: {result.accepted_count} proposal(s), {len(result.changed_files)} changed file(s)")


@app.command()
def status(config: Path = typer.Option(..., "--config", exists=True, readable=True)) -> None:
    """Show the current indexed message count without changing it."""
    settings = load_config(config)
    if not settings.state_path.exists():
        typer.echo("0 indexed messages")
        return
    store = StateStore(settings.state_path, read_only=True)
    try:
        typer.echo(f"{store.message_count()} indexed messages")
    finally:
        store.close()


@app.command()
def validate(config: Path = typer.Option(..., "--config", exists=True, readable=True)) -> None:
    """Validate enabled exports and output paths without creating archive state."""
    message_count = ArchiveService(load_config(config)).validate()
    typer.echo(f"valid: {message_count} message(s)")


if __name__ == "__main__":
    app()
