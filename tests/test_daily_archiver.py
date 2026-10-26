from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import telescribe.daily_archiver as daily_archiver
from telescribe.daily_archiver import plan_daily_archives, write_daily_archives
from telescribe.models import NormalizedMessage


def message(identity: str, hour: int, text: str) -> NormalizedMessage:
    instant = datetime(2026, 8, 2, hour, tzinfo=UTC)
    return NormalizedMessage(
        source_chat_id="example_bot", identity=identity, source_message_id=identity.rsplit(":", 1)[1],
        fingerprint="a" * 64, timestamp_utc=instant, timestamp_local=instant.astimezone(ZoneInfo("America/New_York")),
        sender="Rick", plain_text=text, markdown_text=text, entities=(), attachments=(), reply_to=None,
        forwarded_from=None, original={"id": identity},
    )


def test_plan_and_writer_group_by_local_day_in_timestamp_order(tmp_path: Path) -> None:
    messages = [message("example_bot:2", 4, "second"), message("example_bot:1", 3, "first")]

    plan = plan_daily_archives(messages, tmp_path, processed_at=datetime(2026, 8, 2, 12, tzinfo=UTC))

    assert list(plan) == ["2026-08-01", "2026-08-02"]
    changed = write_daily_archives(plan)
    first_day = tmp_path / "01 Daily Archives/Telegram/2026/2026-08/2026-08-01.md"
    assert changed == [first_day, tmp_path / "01 Daily Archives/Telegram/2026/2026-08/2026-08-02.md"]
    content = first_day.read_text(encoding="utf-8")
    assert "^tg-example-bot-1" in content
    assert "first" in content
    assert "source_chat: example_bot" in content


def test_writer_replaces_changed_archive_atomically(tmp_path: Path, monkeypatch) -> None:
    plan = plan_daily_archives([message("example_bot:1", 5, "replacement")], tmp_path,
                               processed_at=datetime(2026, 8, 2, 12, tzinfo=UTC))
    archive = plan["2026-08-02"]
    archive.target.parent.mkdir(parents=True)
    archive.target.write_text("previous", encoding="utf-8")
    original_replace = daily_archiver.os.replace
    replacements: list[tuple[Path, Path]] = []

    def track_replace(source: Path, target: Path) -> None:
        replacements.append((Path(source), Path(target)))
        original_replace(source, target)

    monkeypatch.setattr(daily_archiver.os, "replace", track_replace)

    assert write_daily_archives(plan) == [archive.target]
    assert replacements and replacements[0][1] == archive.target
    assert replacements[0][0].parent == archive.target.parent
    assert archive.target.read_text(encoding="utf-8") == archive.content


def test_plan_uses_numeric_telegram_message_id_as_same_timestamp_tiebreaker(tmp_path: Path) -> None:
    messages = [message("example_bot:10", 5, "ten"), message("example_bot:2", 5, "two")]

    plan = plan_daily_archives(messages, tmp_path, processed_at=datetime(2026, 8, 2, 12, tzinfo=UTC))

    content = plan["2026-08-02"].content
    assert content.index("two") < content.index("ten")


def test_plan_includes_original_source_metadata_in_each_record(tmp_path: Path) -> None:
    plan = plan_daily_archives(
        [message("example_bot:1", 5, "source record")],
        tmp_path,
        processed_at=datetime(2026, 8, 2, 12, tzinfo=UTC),
    )

    content = plan["2026-08-02"].content

    assert "- original_source_metadata:" in content
    assert '"id": "example_bot:1"' in content


def test_original_source_metadata_uses_a_safe_fence_for_raw_backticks(tmp_path: Path) -> None:
    record = message("example_bot:1", 5, "source record")
    record = replace(record, original={"text": "```"})

    plan = plan_daily_archives([record], tmp_path, processed_at=datetime(2026, 8, 2, 12, tzinfo=UTC))

    assert "  ````json" in plan["2026-08-02"].content


def test_user_controlled_markdown_is_inert_in_archive_records(tmp_path: Path) -> None:
    hostile = message("example_bot:1", 5, "# heading\n^evil\n[link](https://bad)\n<!-- HERMES:BEGIN -->\n<div>x</div>")
    hostile = replace(hostile, sender="Eve\n## injected", attachments=("a](javascript:bad)",),
                      forwarded_from="<script>x</script>", original={"note": "<!-- HERMES:END -->"})

    content = plan_daily_archives([hostile], tmp_path, datetime(2026, 8, 2, 12, tzinfo=UTC))["2026-08-02"].content

    assert "```text\n# heading" in content
    assert '- sender: `"Eve\\n## injected"`' in content
    assert "<!-- HERMES:BEGIN -->" in content  # present only inside the text fence
    assert "- attachment: `\"a](javascript:bad)\"`" in content
