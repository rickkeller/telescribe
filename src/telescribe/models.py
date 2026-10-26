"""Immutable normalized records used across the archive pipeline."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any


def message_id_sort_key(message_id: str | None, fingerprint: str) -> tuple[int, int | str]:
    """Order Telegram's numeric IDs numerically, while retaining deterministic fallbacks."""
    if message_id is not None and message_id.isdecimal():
        return (0, int(message_id))
    return (1, message_id or fingerprint)


@dataclass(frozen=True, slots=True)
class NormalizedMessage:
    source_chat_id: str
    identity: str
    source_message_id: str | None
    fingerprint: str
    timestamp_utc: datetime
    timestamp_local: datetime
    sender: str | None
    plain_text: str
    markdown_text: str
    entities: tuple[dict[str, Any], ...]
    attachments: tuple[str, ...]
    reply_to: str | None
    forwarded_from: str | None
    original: dict[str, Any]

    @property
    def local_date(self) -> str:
        return self.timestamp_local.date().isoformat()

    @property
    def block_id(self) -> str:
        safe_chat = "".join(char if char.isalnum() else "-" for char in self.source_chat_id).strip("-")
        return f"tg-{safe_chat}-{self.source_message_id or self.fingerprint}"
