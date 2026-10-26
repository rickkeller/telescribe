"""Deterministic daily archive planning and Markdown rendering."""

import json
import os
import re
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .models import NormalizedMessage, message_id_sort_key


@dataclass(frozen=True, slots=True)
class DailyArchive:
    date: str
    target: Path
    content: str
    messages: tuple[NormalizedMessage, ...]


def _target(root: Path, date: str) -> Path:
    year, month, _ = date.split("-")
    return root / "01 Daily Archives" / "Telegram" / year / f"{year}-{month}" / f"{date}.md"


def _render_message(message: NormalizedMessage) -> str:
    def quoted(value: object) -> str:
        # JSON makes controls one-line, and a dynamically sized code span makes
        # the remaining Markdown/HTML syntax literal.
        encoded = json.dumps(value, ensure_ascii=False)
        ticks = "`" * (max((len(run) for run in re.findall(r"`+", encoded)), default=0) + 1)
        return f"{ticks}{encoded}{ticks}"

    # Keep all Telegram prose in a fence.  The fence is longer than every
    # literal backtick run, so it cannot create headings, links, HTML, block
    # IDs, or managed markers in the generated archive.
    text = message.markdown_text or "Unsupported or empty Telegram content."
    text_fence = "`" * max(3, max((len(run) for run in re.findall(r"`+", text)), default=0) + 1)
    safe_text = f"{text_fence}text\n{text}\n{text_fence}"
    attachment_lines = "".join(f"- attachment: {quoted(item)}\n" for item in message.attachments)
    metadata = [
        # Source IDs and resulting identities are configuration-validated.
        f"- source_chat: {message.source_chat_id}",
        f"- source_identity: {message.identity}",
        f"- timestamp_utc: {quoted(message.timestamp_utc.isoformat())}",
        f"- sender: {quoted(message.sender or 'unknown')}",
        f"- classification: unclassified",
    ]
    if message.reply_to:
        metadata.append(f"- reply_to: {quoted(message.reply_to)}")
    if message.forwarded_from:
        metadata.append(f"- forwarded_from: {quoted(message.forwarded_from)}")
    raw_source = json.dumps(message.original, ensure_ascii=False, sort_keys=True, indent=2)
    # Choose a fence longer than any literal backtick run in export text, so raw
    # Telegram content cannot prematurely close its own metadata block.
    backtick_runs = re.findall(r"`+", raw_source)
    fence = "`" * max(3, max((len(run) for run in backtick_runs), default=0) + 1)
    original_metadata = "\n".join([
        "- original_source_metadata:",
        f"  {fence}json",
        *(f"  {line}" for line in raw_source.splitlines()),
        f"  {fence}",
    ])
    return "\n".join([
        # Sender is user-controlled and therefore belongs only in the inert
        # metadata value below, never in structural Markdown.
        f"### {message.timestamp_local.strftime('%H:%M')} — Telegram message ^{message.block_id}",
        safe_text,
        "\n".join(metadata), attachment_lines.rstrip(),
        original_metadata,
    ]).strip() + "\n"


def _render_day(date: str, messages: tuple[NormalizedMessage, ...], processed_at: datetime,
                new_identities: set[str]) -> str:
    chats = sorted({message.source_chat_id for message in messages})
    records = "\n".join(_render_message(message) for message in messages)
    return "\n".join([
        "---",
        f"date: {date}",
        "source_chats:",
        *(f"  - {json.dumps(chat, ensure_ascii=False)}" for chat in chats),
        f"total_messages: {len(messages)}",
        f"new_messages: {sum(message.identity in new_identities for message in messages)}",
        f"processed_at: {processed_at.isoformat()}",
        "---",
        "",
        f"# Telegram Archive — {date}",
        "",
        "## Daily Summary",
        "",
        "Deterministic raw-message archive; summarization is not enabled in this MVP.",
        "",
        "## Projects Mentioned",
        "",
        "None (classification is not enabled).",
        "",
        "## Extracted Tasks, Ideas, and Decisions",
        "",
        "None (extraction is not enabled).",
        "",
        "## Complete Conversation Records",
        "",
        records.rstrip(),
        "",
    ])


def plan_daily_archives(messages: Iterable[NormalizedMessage], archive_root: Path,
                        processed_at: datetime, new_identities: set[str] | None = None) -> dict[str, DailyArchive]:
    """Produce fully deterministic daily note content, without filesystem mutation."""
    all_messages = list(messages)
    new = new_identities if new_identities is not None else {message.identity for message in all_messages}
    grouped: dict[str, list[NormalizedMessage]] = defaultdict(list)
    for message in all_messages:
        grouped[message.local_date].append(message)
    planned: dict[str, DailyArchive] = {}
    for date in sorted(grouped):
        ordered = tuple(sorted(grouped[date], key=lambda item: (
            item.timestamp_local, item.source_chat_id,
            message_id_sort_key(item.source_message_id, item.fingerprint), item.identity
        )))
        planned[date] = DailyArchive(date, _target(archive_root, date), _render_day(date, ordered, processed_at, new), ordered)
    return planned


def write_daily_archives(plan: dict[str, DailyArchive]) -> list[Path]:
    """Atomically write a fully prepared plan. Callers own state checkpointing."""
    changed: list[Path] = []
    for archive in plan.values():
        if archive.target.exists() and archive.target.read_text(encoding="utf-8") == archive.content:
            continue
        archive.target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=archive.target.parent, delete=False) as temp:
            temp.write(archive.content)
            temporary = Path(temp.name)
        os.replace(temporary, archive.target)
        changed.append(archive.target)
    return changed
