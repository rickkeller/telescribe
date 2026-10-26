import json
from pathlib import Path

from typer.testing import CliRunner

from telescribe.cli import app


def test_cli_exposes_process_and_dry_run() -> None:
    result = CliRunner().invoke(app, ["process", "--help"])

    assert result.exit_code == 0
    assert "--dry-run" in result.output


def test_cli_exposes_read_only_validate_command() -> None:
    result = CliRunner().invoke(app, ["validate", "--help"])

    assert result.exit_code == 0
    assert "Validate enabled exports" in result.output


def _config_file(tmp_path: Path) -> Path:
    fixture = Path(__file__).parent / "fixtures" / "string_text_result.json"
    config = tmp_path / "telescribe.yaml"
    config.write_text(f"""archive_root: {tmp_path / 'vault'}
state_path: {tmp_path / 'state.sqlite3'}
sources:
  - id: test_source
    telegram_names: [Example Bot]
    export_path: {fixture}
    attachment_dir: {tmp_path / 'files'}
""", encoding="utf-8")
    return config


def test_reprocess_cli_rebuilds_indexed_day_without_duplicate_index_records(tmp_path: Path) -> None:
    config = _config_file(tmp_path)
    runner = CliRunner()

    first = runner.invoke(app, ["process", "--config", str(config)])
    rebuilt = runner.invoke(app, ["reprocess", "--config", str(config), "--date", "2026-07-31"])
    repeated = runner.invoke(app, ["reprocess", "--config", str(config), "--date", "2026-07-31"])
    count = runner.invoke(app, ["status", "--config", str(config)])

    assert first.exit_code == rebuilt.exit_code == repeated.exit_code == count.exit_code == 0
    assert "1 indexed messages" in count.output
    assert "1 message(s)" in rebuilt.output
    assert "0 changed file(s)" in repeated.output
    audit_files = sorted((tmp_path / "runs").glob("*.json"))
    audit = json.loads(audit_files[-1].read_text(encoding="utf-8"))
    assert set(audit) == {"run_id", "started_at", "completed_at", "status", "sources", "messages_scanned", "new_messages", "dates_updated", "files_created", "files_modified", "warnings"}
    assert audit["status"] == "success"
    assert audit["dates_updated"] == ["2026-07-31"]
    assert "hello" not in audit_files[-1].read_text(encoding="utf-8").lower()


def test_reprocess_requires_one_valid_selector_and_dry_run_creates_no_audit(tmp_path: Path) -> None:
    config = _config_file(tmp_path)
    runner = CliRunner()

    dry = runner.invoke(app, ["process", "--config", str(config), "--dry-run"])
    assert dry.exit_code == 0
    assert not (tmp_path / "runs").exists()
    missing = runner.invoke(app, ["reprocess", "--config", str(config)])
    both = runner.invoke(app, ["reprocess", "--config", str(config), "--date", "2026-07-31", "--all"])
    malformed = runner.invoke(app, ["reprocess", "--config", str(config), "--date", "not-a-date"])

    assert missing.exit_code != 0
    assert both.exit_code != 0
    assert malformed.exit_code != 0

    failed = runner.invoke(app, ["reprocess", "--config", str(config), "--date", "2026-07-31"])
    audit_files = list((tmp_path / "runs").glob("*.json"))
    assert failed.exit_code != 0
    assert len(audit_files) == 1
    assert json.loads(audit_files[0].read_text(encoding="utf-8"))["status"] == "failed"


def test_reprocess_source_selector_is_valid_and_does_not_write_index_rows(tmp_path: Path) -> None:
    config = _config_file(tmp_path)
    runner = CliRunner()
    assert runner.invoke(app, ["process", "--config", str(config)]).exit_code == 0

    selected = runner.invoke(app, ["reprocess", "--config", str(config), "--source", "test_source"])
    ambiguous = runner.invoke(app, ["reprocess", "--config", str(config), "--source", "test_source", "--all"])
    unknown = runner.invoke(app, ["reprocess", "--config", str(config), "--source", "not-configured"])
    count = runner.invoke(app, ["status", "--config", str(config)])

    assert selected.exit_code == 0
    assert "1 message(s)" in selected.output
    assert ambiguous.exit_code != 0
    assert unknown.exit_code != 0
    assert "1 indexed messages" in count.output


def test_review_cli_requires_one_selector_and_fails_closed_without_provider(tmp_path: Path) -> None:
    config = _config_file(tmp_path)
    runner = CliRunner()
    missing = runner.invoke(app, ["review", "--config", str(config)])
    both = runner.invoke(app, ["review", "--config", str(config), "--date", "2026-07-31", "--all"])
    malformed = runner.invoke(app, ["review", "--config", str(config), "--date", "2026-7-31"])
    unavailable = runner.invoke(app, ["review", "--config", str(config), "--all"])

    assert missing.exit_code != 0 and both.exit_code != 0 and malformed.exit_code != 0
    assert unavailable.exit_code != 0
    assert "project intelligence is disabled" in unavailable.output
    assert not (tmp_path / "review").exists()


def test_review_cli_dispatch_and_dry_run_are_mutually_exclusive(tmp_path: Path) -> None:
    config = _config_file(tmp_path)
    result = CliRunner().invoke(app, ["review", "--config", str(config), "--all", "--dry-run", "--dispatch"])
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output
