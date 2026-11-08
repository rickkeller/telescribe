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
    start_date: str | None = typer.Option(None, "--start-date", help="Inclusive NY-local review start date (YYYY-MM-DD)."),
    end_date: str | None = typer.Option(None, "--end-date", help="Inclusive NY-local review end date (YYYY-MM-DD)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan only; do not write queue or audit."),
    dispatch: bool = typer.Option(False, "--dispatch", help="Explicitly allow the approved direct provider request."),
    cursor: int = typer.Option(0, "--cursor", "--offset", help="Zero-based ordered review page cursor."),
    batch_size: int | None = typer.Option(None, "--batch-size", help="Page size, capped by intelligence.max_messages."),
    chronological: bool = typer.Option(False, "--chronological", help="Opt in to oldest-first reconstruction with a review-root ledger."),
) -> None:
    """Generate review-only proposals from the indexed message archive."""
    if bool(start_date) != bool(end_date):
        raise typer.BadParameter("--start-date and --end-date must be provided together")
    if sum((bool(date), bool(source), all_dates, bool(start_date))) != 1:
        raise typer.BadParameter("provide exactly one of --date YYYY-MM-DD, --source ID, --all, or --start-date/--end-date")
    if dry_run and dispatch:
        raise typer.BadParameter("--dry-run and --dispatch are mutually exclusive")
    if date:
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                raise ValueError
            date_type.fromisoformat(date)
        except ValueError as error:
            raise typer.BadParameter("--date must use strict YYYY-MM-DD calendar date") from error
    for option, value in (("--start-date", start_date), ("--end-date", end_date)):
        if value:
            try:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    raise ValueError
                date_type.fromisoformat(value)
            except ValueError as error:
                raise typer.BadParameter(f"{option} must use strict YYYY-MM-DD calendar date") from error
    if start_date and end_date and start_date > end_date:
        raise typer.BadParameter("--start-date must not be after --end-date")
    try:
        settings = load_config(config)
        classifier = create_direct_deepseek_classifier(settings, dispatch=dispatch) if dispatch else None
        result = ReviewService(settings, classifier).review(
            date=date, source=source, all_dates=all_dates, start_date=start_date, end_date=end_date, dry_run=dry_run,
            cursor=cursor, batch_size=batch_size, ordering="chronological" if chronological else "default",
        )
    except ValueError as error:
        typer.echo(f"review unavailable: {error}", err=True)
        raise typer.Exit(code=2) from error
    continuation = str(result.next_cursor) if result.next_cursor is not None else "none"
    range_label = (f", NY-local range: {result.start_date} through {result.end_date}"
                   if result.start_date else "")
    typer.echo(f"{'dry-run' if dry_run else 'completed'}: {result.accepted_count} proposal(s), {len(result.changed_files)} changed file(s), ordering: {result.ordering}, next cursor: {continuation}, has more: {result.has_more}{range_label}")


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
