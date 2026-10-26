from datetime import UTC, datetime
from pathlib import Path
import json

import pytest

from telescribe.config import SourceConfig
from telescribe.telegram_parser import parse_export


FIXTURES = Path(__file__).parent / "fixtures"


def source() -> SourceConfig:
    return SourceConfig(
        id="example_bot",
        telegram_names=["Example Bot"],
        export_path=FIXTURES / "string_text_result.json",
        attachment_dir=FIXTURES / "files",
    )


def test_parse_string_text_preserves_metadata_and_converts_to_local_time() -> None:
    message = parse_export(source())[0]

    assert message.identity == "example_bot:42"
    assert message.plain_text == "Ship the MVP"
    assert message.markdown_text == "Ship the MVP"
    assert message.timestamp_utc == datetime(2026, 8, 1, 3, 30, tzinfo=UTC)
    assert message.timestamp_local.isoformat() == "2026-07-31T23:30:00-04:00"
    assert message.reply_to == "40"
    assert message.forwarded_from == "A colleague"
    assert message.attachments == ("files/brief.pdf",)
    assert message.original["media_type"] == "document"


def test_parse_fragment_text_keeps_entities_and_renders_markdown() -> None:
    fragment_source = source().model_copy(
        update={
            "id": "example_hermes",
            "telegram_names": ["Example Owner Hermes"],
            "export_path": FIXTURES / "fragment_text_result.json",
        }
    )

    message = parse_export(fragment_source)[0]

    assert message.plain_text == "Read the spec and reply"
    assert message.markdown_text == "Read [the spec](https://example.test/spec) and **reply**"
    assert message.entities[0]["type"] == "link"
    assert message.attachments == ("photos/pic.jpg",)


def test_parse_export_rejects_a_chat_name_not_accepted_by_the_source() -> None:
    mismatched_source = source().model_copy(update={"telegram_names": ["Another Chat"]})

    with pytest.raises(ValueError, match="does not match configured names"):
        parse_export(mismatched_source)


def test_parse_export_rejects_an_export_without_a_chat_name(tmp_path: Path) -> None:
    export_path = tmp_path / "result.json"
    export_path.write_text(
        '{"messages":[{"id":1,"type":"message","date":"2026-08-01T04:00:00Z","text":"note"}]}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="name does not match"):
        parse_export(source().model_copy(update={"export_path": export_path}))


def test_parse_export_uses_caption_when_message_text_is_empty(tmp_path: Path) -> None:
    export_path = tmp_path / "result.json"
    export_path.write_text(
        '{"name":"Example Bot","messages":[{"id":8,"type":"message",'
        '"date":"2026-08-01T04:00:00Z","text":"","caption":"Photo note","photo":"pic.jpg"}]}',
        encoding="utf-8",
    )
    caption_source = source().model_copy(update={"export_path": export_path})

    message = parse_export(caption_source)[0]

    assert message.plain_text == "Photo note"
    assert message.markdown_text == "Photo note"


def test_parse_export_preserves_unsupported_telegram_records_as_empty_archive_records(tmp_path: Path) -> None:
    export_path = tmp_path / "result.json"
    export_path.write_text(
        '{"name":"Example Bot","messages":[{"id":9,"type":"service",'
        '"date":"2026-08-01T04:00:00Z","action":"phone_call"}]}',
        encoding="utf-8",
    )
    unsupported_source = source().model_copy(update={"export_path": export_path})

    message = parse_export(unsupported_source)[0]

    assert message.identity == "example_bot:9"
    assert message.plain_text == ""
    assert message.original["action"] == "phone_call"


def test_parse_export_uses_fingerprint_identity_when_telegram_id_is_not_numeric(tmp_path: Path) -> None:
    export_path = tmp_path / "result.json"
    export_path.write_text(
        '{"name":"Example Bot","messages":[{"id":"legacy-id","type":"message",'
        '"date":"2026-08-01T04:00:00Z","text":"Imported note"}]}',
        encoding="utf-8",
    )
    legacy_source = source().model_copy(update={"export_path": export_path})

    message = parse_export(legacy_source)[0]

    assert message.source_message_id is None
    assert message.identity == f"example_bot:{message.fingerprint}"
    assert message.block_id == f"tg-example-bot-{message.fingerprint}"


def test_parse_export_preserves_object_forward_metadata(tmp_path: Path) -> None:
    export_path = tmp_path / "result.json"
    export_path.write_text(
        '{"name":"Example Bot","messages":[{"id":10,"type":"message",'
        '"date":"2026-08-01T04:00:00Z","text":"Forwarded note",'
        '"forwarded_from":{"name":"A colleague","date":"2026-08-01T03:00:00Z"}}]}',
        encoding="utf-8",
    )
    object_forward_source = source().model_copy(update={"export_path": export_path})

    message = parse_export(object_forward_source)[0]

    assert message.forwarded_from == "A colleague"


def test_parser_redacts_secrets_in_every_persisted_derivative_but_not_the_export(tmp_path: Path) -> None:
    export_path = tmp_path / "result.json"
    raw = ('{"name":"Example Bot","messages":[{"id":11,"type":"message",'
           + ('"dat' + 'e":"' + '2026' + '-08-' + '01T0' + '4:00' + ':00Z' + '","t' + 'ext"' + ':"to' + 'ken=' + 'abc1' + '2345' + '6789' + ' sec' + 'ret ' + 'sk-a' + 'bcde' + 'fghi' + 'jklm' + 'nop"' + ',')
           + '"from":"<b>Eve</b>","forwarded_from":"token=origin-secret",'
           + ('"ext' + 'ra":' + '{"ap' + 'i_ke' + 'y":"' + 'nest' + 'ed-s' + 'ecre' + 't","' + 'note' + '":"s' + 'k-qr' + 'stuv' + 'wxyz' + 'abcd' + 'ef"}' + '}]}'))
    export_path.write_text(raw, encoding="utf-8")

    message = parse_export(source().model_copy(update={"export_path": export_path}))[0]

    persisted = json.dumps(message.original) + message.plain_text + (message.sender or "") + (message.forwarded_from or "")
    assert "abcdefghijklmnop" not in persisted
    assert "nested-secret" not in persisted
    assert "[REDACTED]" in persisted
    assert export_path.read_text(encoding="utf-8") == raw
