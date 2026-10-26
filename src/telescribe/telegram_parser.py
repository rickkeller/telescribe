"""Telegram Desktop JSON parsing and loss-aware text normalization."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import SourceConfig
from .models import NormalizedMessage
from .redaction import redact


def _render_text(value: Any) -> tuple[str, str, tuple[dict[str, Any], ...]]:
    if isinstance(value, str):
        return value, value, ()
    if not isinstance(value, list):
        return "", "", ()
    plain: list[str] = []
    markdown: list[str] = []
    entities: list[dict[str, Any]] = []
    for part in value:
        if isinstance(part, str):
            plain.append(part)
            markdown.append(part)
            continue
        if not isinstance(part, dict):
            continue
        text = str(part.get("text", ""))
        kind = str(part.get("type", ""))
        plain.append(text)
        entities.append(dict(part))
        if kind == "link" and part.get("href"):
            markdown.append(f"[{text}]({part['href']})")
        elif kind == "bold":
            markdown.append(f"**{text}**")
        elif kind == "italic":
            markdown.append(f"*{text}*")
        elif kind == "code":
            markdown.append(f"`{text}`")
        else:
            markdown.append(text)
    return "".join(plain), "".join(markdown), tuple(entities)


def _parse_timestamp(raw: Any) -> datetime:
    if not isinstance(raw, str):
        raise ValueError("Telegram message has no valid date")
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _attachments(message: dict[str, Any]) -> tuple[str, ...]:
    values = []
    for key in ("file", "photo", "thumbnail", "media"):
        value = message.get(key)
        if isinstance(value, str) and value:
            values.append(value)
    return tuple(values)


def _forwarded_from(value: Any) -> str | None:
    """Return a displayable forward origin while retaining the full raw value."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("name", "from", "actor"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate:
                return candidate
    return None


def _fingerprint(source_id: str, timestamp: datetime, sender: str | None, text: str,
                 reply_to: str | None, attachments: tuple[str, ...]) -> str:
    content = "\x1f".join([source_id, timestamp.isoformat(), sender or "", text, reply_to or "", *attachments])
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def parse_export(source: SourceConfig, timezone: str = "America/New_York") -> list[NormalizedMessage]:
    """Read an export without modifying it and normalize supported message records."""
    with Path(source.export_path).open("r", encoding="utf-8") as export_file:
        export = json.load(export_file)
    if not isinstance(export, dict) or not isinstance(export.get("messages"), list):
        raise ValueError("Telegram export must contain a messages list")
    export_name = export.get("name")
    if not isinstance(export_name, str) or export_name not in source.telegram_names:
        raise ValueError("Telegram export name does not match configured names")
    local_zone = ZoneInfo(timezone)
    normalized: list[NormalizedMessage] = []
    for raw in export["messages"]:
        if not isinstance(raw, dict):
            continue
        # The raw export is never changed.  From this point on, all derived,
        # persisted representations use the same redacted copy.
        persisted_raw = redact(raw)
        timestamp_utc = _parse_timestamp(persisted_raw.get("date"))
        plain, markdown, entities = _render_text(persisted_raw.get("text", ""))
        if not plain and not markdown and "caption" in persisted_raw:
            plain, markdown, entities = _render_text(persisted_raw["caption"])
        sender = persisted_raw.get("from") if isinstance(persisted_raw.get("from"), str) else None
        reply = persisted_raw.get("reply_to_message_id")
        reply_to = str(reply) if reply is not None else None
        attachments = _attachments(persisted_raw)
        fingerprint = _fingerprint(source.id, timestamp_utc, sender, plain, reply_to, attachments)
        message_id = persisted_raw.get("id")
        # Telegram's stable primary key is numeric.  Treat malformed/exporter-specific
        # string IDs as absent so the documented content fingerprint remains stable.
        source_message_id = (
            str(message_id)
            if (isinstance(message_id, int) and not isinstance(message_id, bool))
            or (isinstance(message_id, str) and message_id.isdecimal())
            else None
        )
        identity = f"{source.id}:{source_message_id}" if source_message_id else f"{source.id}:{fingerprint}"
        normalized.append(NormalizedMessage(
            source_chat_id=source.id, identity=identity, source_message_id=source_message_id,
            fingerprint=fingerprint, timestamp_utc=timestamp_utc,
            timestamp_local=timestamp_utc.astimezone(local_zone), sender=sender,
            plain_text=plain, markdown_text=markdown, entities=entities, attachments=attachments,
            reply_to=reply_to,
            forwarded_from=_forwarded_from(persisted_raw.get("forwarded_from")),
            original=dict(persisted_raw),
        ))
    return normalized
