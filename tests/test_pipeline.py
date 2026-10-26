import json
from pathlib import Path

import pytest

from telescribe.config import AppConfig, SourceConfig
from telescribe.audit import write_audit
from telescribe.pipeline import ArchiveService
from telescribe.state_store import StateStore
from telescribe.telegram_parser import parse_export


def config(tmp_path: Path) -> AppConfig:
    fixtures = Path(__file__).parent / "fixtures"
    return AppConfig(
        sources=[SourceConfig(
            id="example_bot", telegram_names=["Example Bot"],
            export_path=fixtures / "string_text_result.json", attachment_dir=fixtures / "files",
        )],
        archive_root=tmp_path / "vault", state_path=tmp_path / "state.sqlite3",
    )


def test_dry_run_has_zero_filesystem_or_state_mutations(tmp_path: Path) -> None:
    result = ArchiveService(config(tmp_path)).process(dry_run=True)

    assert result.message_count == 1
    assert result.changed_files == (
        tmp_path / "vault/01 Daily Archives/Telegram/2026/2026-07/2026-07-31.md",
    )
    assert not (tmp_path / "vault").exists()
    assert not (tmp_path / "state.sqlite3").exists()


def test_process_is_idempotent_and_writes_state_after_archive(tmp_path: Path) -> None:
    service = ArchiveService(config(tmp_path))

    first = service.process()
    second = service.process()

    assert first.message_count == 1
    assert len(first.changed_files) == 1
    assert second.message_count == 0
    assert second.changed_files == ()


def test_reprocess_recreates_selected_archive_at_newly_configured_root_without_index_writes(
    tmp_path: Path,
) -> None:
    initial = config(tmp_path).model_copy(
        update={"archive_root": tmp_path / "vault/01 Daily Archives/Telegram"}
    )
    ArchiveService(initial).process()
    store = StateStore(initial.state_path, read_only=True)
    try:
        original_count = store.message_count()
        original_identities = store.archived_identities()
    finally:
        store.close()

    corrected_root = tmp_path / "vault"
    corrected = initial.model_copy(update={"archive_root": corrected_root})
    target = corrected_root / "01 Daily Archives/Telegram/2026/2026-07/2026-07-31.md"

    result = ArchiveService(corrected).reprocess(date="2026-07-31")

    assert result.message_count == 1
    assert result.changed_files == (target,)
    assert target.exists()
    store = StateStore(corrected.state_path, read_only=True)
    try:
        assert store.message_count() == original_count
        assert store.archived_identities() == original_identities
    finally:
        store.close()


def test_dry_run_does_not_touch_an_existing_state_database(tmp_path: Path) -> None:
    service = ArchiveService(config(tmp_path))
    service.process()
    state_path = tmp_path / "state.sqlite3"
    before = state_path.read_bytes()
    before_mtime = state_path.stat().st_mtime_ns

    result = service.process(dry_run=True)

    assert result.message_count == 0
    assert state_path.read_bytes() == before
    assert state_path.stat().st_mtime_ns == before_mtime


def test_dry_run_opens_an_existing_index_read_only(tmp_path: Path, monkeypatch) -> None:
    service = ArchiveService(config(tmp_path))
    service.process()
    original = StateStore
    requested_modes: list[bool] = []

    def tracking_store(path: Path, *, read_only: bool = False) -> StateStore:
        requested_modes.append(read_only)
        return original(path, read_only=read_only)

    monkeypatch.setattr("telescribe.pipeline.StateStore", tracking_store)

    service.process(dry_run=True)

    assert requested_modes == [True]


def test_dry_run_leaves_an_existing_empty_sqlite_placeholder_untouched(tmp_path: Path) -> None:
    service = ArchiveService(config(tmp_path))
    state_path = tmp_path / "state.sqlite3"
    state_path.write_bytes(b"")
    before = state_path.read_bytes()
    before_mtime = state_path.stat().st_mtime_ns

    result = service.process(dry_run=True)

    assert result.message_count == 1
    assert state_path.read_bytes() == before
    assert state_path.stat().st_mtime_ns == before_mtime
    assert not (tmp_path / "vault").exists()


def test_audit_staging_failure_leaves_files_and_terminal_state_uncommitted(tmp_path: Path, monkeypatch) -> None:
    service = ArchiveService(config(tmp_path))

    def fail_audit(*_args, **_kwargs):
        raise OSError("audit volume unavailable")

    monkeypatch.setattr("telescribe.pipeline.write_audit", fail_audit)
    with pytest.raises(OSError, match="audit volume"):
        service.process()

    assert not (tmp_path / "vault").exists()
    assert not (tmp_path / "state.sqlite3").exists()


def test_redaction_reaches_sqlite_and_markdown_without_changing_raw_export(tmp_path: Path) -> None:
    export = tmp_path / "result.json"
    raw = ('{"name":"Secret Chat","messages":[{"id":1,"date":"2026-08-01T04:00:00Z",'
           '"text":"api_key=super-secret-value", "nested":{"token":"also-secret"}}]}')
    export.write_text(raw, encoding="utf-8")
    settings = AppConfig(sources=[SourceConfig(
        id="safe_source", telegram_names=["Secret Chat"], export_path=export,
        attachment_dir=tmp_path / "files",
    )], archive_root=tmp_path / "vault", state_path=tmp_path / "state.sqlite3")

    ArchiveService(settings).process()
    store = StateStore(settings.state_path, read_only=True)
    try:
        record = store.archived_messages()[0]
    finally:
        store.close()
    markdown = next((tmp_path / "vault").rglob("*.md")).read_text(encoding="utf-8")

    assert "super-secret-value" not in record.plain_text + str(record.original)
    assert "also-secret" not in str(record.original)
    assert "super-secret-value" not in markdown
    assert "also-secret" not in markdown
    assert "[REDACTED]" in markdown
    assert export.read_text(encoding="utf-8") == raw


def test_timestamp_epoch_metadata_survives_persisted_derivatives_but_phone_like_values_redact(
    tmp_path: Path,
) -> None:
    export = tmp_path / "result.json"
    epoch = "1785556800"
    raw = json.dumps({
        "name": "Secret Chat",
        "messages": [{
            "id": 1,
            "date": "2026-08-01T04:00:00Z",
            "date_unixtime": epoch,
            "text": f"Call {epoch}",
            "external_reference": epoch,
        }],
    })
    export.write_text(raw, encoding="utf-8")
    settings = AppConfig(sources=[SourceConfig(
        id="safe_source", telegram_names=["Secret Chat"], export_path=export,
        attachment_dir=tmp_path / "files",
    )], archive_root=tmp_path / "vault", state_path=tmp_path / "state.sqlite3")

    parsed = parse_export(settings.sources[0])[0]
    assert parsed.original["date_unixtime"] == epoch
    assert parsed.plain_text == "Call [REDACTED]"
    assert parsed.original["external_reference"] == "[REDACTED]"

    ArchiveService(settings).process()
    store = StateStore(settings.state_path, read_only=True)
    try:
        stored = store.archived_messages()[0]
    finally:
        store.close()
    markdown = next((tmp_path / "vault").rglob("*.md")).read_text(encoding="utf-8")

    assert stored.original["date_unixtime"] == epoch
    assert stored.original["external_reference"] == "[REDACTED]"
    assert f'"date_unixtime": "{epoch}"' in markdown
    assert "Call [REDACTED]" in markdown
    assert "external_reference\": \"[REDACTED]" in markdown
    assert export.read_text(encoding="utf-8") == raw


def test_persisted_derivatives_redact_adversarial_credentials_and_pii(tmp_path: Path) -> None:
    export = tmp_path / "result.json"
    secrets = (
        "bearer-credential-987654", "authorization-credential-123456", "eve.private@example.test",
        "212-555-0188", "+44 20 7946 0958", "super-private-key-material-123456",
        "inline-api-key-abcdef", "nested-token-abcdef", "4111 1111 1111 1111",
        "GB82WEST12345698765432", "987654321012", "natural-api-key-abcdef",
        "standalone-token-abcdef", "natural-password-abcdef",
    )
    message = {
        "id": 1, "date": "2026-08-01T04:00:00Z",
        "text": (
            "Bearer bearer-credential-987654; Authorization: Bearer authorization-credential-123456; "
            "eve.private@example.test; 212-555-0188; +44 20 7946 0958; "
            "api_key=inline-api-key-abcdef; API key is natural-api-key-abcdef; "
            "token: standalone-token-abcdef; password is natural-password-abcdef; "
            "account number 987654321012; card 4111 1111 1111 1111; "
            "IBAN GB82WEST12345698765432. Ordinary prose: bear market update for invoice 2026."
        ),
        "extra": {
            "token": "nested-token-abcdef", "account_number": "987654321012",
            "private_key": "-----BEGIN PRIVATE KEY-----\nsuper-private-key-material-123456\n-----END PRIVATE KEY-----",
        },
    }
    raw = json.dumps({"name": "Secret Chat", "messages": [message]})
    export.write_text(raw, encoding="utf-8")
    settings = AppConfig(sources=[SourceConfig(
        id="safe_source", telegram_names=["Secret Chat"], export_path=export,
        attachment_dir=tmp_path / "files",
    )], archive_root=tmp_path / "vault", state_path=tmp_path / "state.sqlite3")

    ArchiveService(settings).process()
    store = StateStore(settings.state_path, read_only=True)
    try:
        record = store.archived_messages()[0]
        row_text = "\n".join(str(value) for value in store.connection.execute("SELECT * FROM messages").fetchone())
    finally:
        store.close()
    markdown = next((tmp_path / "vault").rglob("*.md")).read_text(encoding="utf-8")
    audit = write_audit(settings.state_path, {"run_id": "redaction-proof", "payload": {"nested": message}})
    audit_text = audit.read_text(encoding="utf-8")
    normalized = json.dumps({
        "plain": record.plain_text, "markdown": record.markdown_text, "sender": record.sender,
        "forwarded": record.forwarded_from, "entities": record.entities, "original": record.original,
    })

    for value in secrets:
        assert value not in normalized
        assert value not in row_text
        assert value not in markdown
        assert value not in audit_text
    assert "bear market update for invoice 2026" in normalized
    assert "[REDACTED]" in normalized
    assert export.read_text(encoding="utf-8") == raw
