from pathlib import Path

import pytest

from telescribe.config import SourceConfig
from telescribe.state_store import StateStore
from telescribe.telegram_parser import parse_export

def test_index_identity_and_earliest_unarchived_are_stable(tmp_path: Path) -> None:
    message = parse_export(SourceConfig(
        id="example_bot", telegram_names=["Example Bot"],
        export_path=Path(__file__).parent / "fixtures/string_text_result.json",
        attachment_dir=Path(__file__).parent / "fixtures/files",
    ))[0]
    store = StateStore(tmp_path / "state.sqlite3")
    try:
        store.upsert_messages([message], run_id="run-1")
        store.upsert_messages([message], run_id="run-2")

        earliest = store.earliest_unarchived()

        assert store.message_count() == 1
        assert earliest is not None
        assert earliest.identity == "example_bot:42"
        store.mark_archived({message.identity: ("01 Daily Archives/Telegram/2026/2026-07/2026-07-31.md", message.block_id)})
        assert store.earliest_unarchived() is None
    finally:
        store.close()


def test_checkpoint_archived_writes_index_and_terminal_location_together(tmp_path: Path) -> None:
    message = parse_export(SourceConfig(
        id="example_bot", telegram_names=["Example Bot"],
        export_path=Path(__file__).parent / "fixtures/string_text_result.json",
        attachment_dir=Path(__file__).parent / "fixtures/files",
    ))[0]
    store = StateStore(tmp_path / "state.sqlite3")
    try:
        store.checkpoint_archived(
            [message],
            {message.identity: ("01 Daily Archives/Telegram/2026/2026-07/2026-07-31.md", message.block_id)},
            run_id="run-1",
        )

        assert store.message_count() == 1
        assert store.earliest_unarchived() is None
    finally:
        store.close()


def test_checkpoint_archived_rolls_back_the_index_when_terminal_marking_fails(tmp_path: Path) -> None:
    message = parse_export(SourceConfig(
        id="example_bot", telegram_names=["Example Bot"],
        export_path=Path(__file__).parent / "fixtures/string_text_result.json",
        attachment_dir=Path(__file__).parent / "fixtures/files",
    ))[0]
    store = StateStore(tmp_path / "state.sqlite3")
    try:
        store.connection.execute("""
            CREATE TRIGGER reject_archive_mark BEFORE UPDATE OF archive_status ON messages
            WHEN NEW.archive_status = 'archived'
            BEGIN SELECT RAISE(ABORT, 'forced archive failure'); END;
        """)
        with pytest.raises(Exception, match="forced archive failure"):
            store.checkpoint_archived(
                [message], {message.identity: ("daily.md", message.block_id)}, run_id="run-1"
            )

        assert store.message_count() == 0
    finally:
        store.close()
