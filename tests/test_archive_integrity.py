"""The three archive-integrity cases the older suite did not name."""

import json
from datetime import UTC, datetime
from pathlib import Path

from telescribe.config import AppConfig, SourceConfig
from telescribe.pipeline import ArchiveService
from telescribe.telegram_parser import parse_export


def _export(path: Path, messages: list[dict]) -> None:
    path.write_text(json.dumps({"name": "Example Bot", "messages": messages}), encoding="utf-8")


def _source(export: Path, files: Path) -> SourceConfig:
    return SourceConfig(
        id="example",
        telegram_names=("Example Bot",),
        export_path=export,
        attachment_dir=files,
        export_timezone="America/New_York",
    )


def _service(tmp_path: Path, export: Path) -> ArchiveService:
    files = tmp_path / "files"
    files.mkdir(exist_ok=True)
    return ArchiveService(AppConfig(
        sources=(_source(export, files),),
        archive_root=tmp_path / "vault",
        state_path=tmp_path / "state.sqlite3",
        timezone="America/New_York",
    ))


def _day(tmp_path: Path, date: str) -> Path:
    year, month, _ = date.split("-")
    return tmp_path / "vault" / "01 Daily Archives" / "Telegram" / year / f"{year}-{month}" / f"{date}.md"


def test_date_unixtime_day_wins_over_a_naive_desktop_date(tmp_path: Path) -> None:
    epoch = int(datetime(2026, 8, 1, 3, 0, tzinfo=UTC).timestamp())
    export = tmp_path / "result.json"
    files = tmp_path / "files"
    files.mkdir()
    _export(export, [{
        "id": 1,
        "type": "message",
        "date": "2026-08-01T03:00:00",
        "date_unixtime": str(epoch),
        "text": "boundary",
    }])

    message = parse_export(_source(export, files), "America/New_York")[0]

    assert message.local_date == "2026-07-31"
    assert message.timestamp_utc == datetime(2026, 8, 1, 3, 0, tzinfo=UTC)


def test_narrower_export_keeps_an_indexed_message_and_skips_an_untouched_day(tmp_path: Path) -> None:
    export = tmp_path / "result.json"
    _export(export, [
        {"id": 1, "type": "message", "date_unixtime": "1785556800", "text": "kept order 100"},
        {"id": 2, "type": "message", "date_unixtime": "1785556860", "text": "still here"},
    ])
    service = _service(tmp_path, export)
    service.process()
    note = _day(tmp_path, "2026-08-01")
    before = note.read_bytes()

    _export(export, [
        {"id": 2, "type": "message", "date_unixtime": "1785556860", "text": "still here"},
    ])
    untouched = service.process()

    assert untouched.changed_files == ()
    assert note.read_bytes() == before
    assert "kept order 100" in note.read_text(encoding="utf-8")

    _export(export, [
        {"id": 2, "type": "message", "date_unixtime": "1785556860", "text": "still here"},
        {"id": 3, "type": "message", "date_unixtime": "1785556920", "text": "added later"},
    ])
    service.process()
    rewritten = note.read_text(encoding="utf-8")

    assert "kept order 100" in rewritten
    assert "still here" in rewritten
    assert "added later" in rewritten


def test_handwritten_text_outside_markers_survives_a_rewrite(tmp_path: Path) -> None:
    export = tmp_path / "result.json"
    _export(export, [
        {"id": 1, "type": "message", "date_unixtime": "1785556800", "text": "generated"},
    ])
    service = _service(tmp_path, export)
    service.process()
    note = _day(tmp_path, "2026-08-01")
    note.write_text(note.read_text(encoding="utf-8") + "\nhandwritten margin\n", encoding="utf-8")

    _export(export, [
        {"id": 1, "type": "message", "date_unixtime": "1785556800", "text": "generated"},
        {"id": 2, "type": "message", "date_unixtime": "1785556860", "text": "added later"},
    ])
    service.process()
    rewritten = note.read_text(encoding="utf-8")

    assert "handwritten margin" in rewritten
    assert "generated" in rewritten
    assert "added later" in rewritten
