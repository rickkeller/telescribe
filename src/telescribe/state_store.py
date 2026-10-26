"""SQLite-backed authoritative message checkpoint index."""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .models import NormalizedMessage


@dataclass(frozen=True, slots=True)
class IndexedMessage:
    identity: str
    source_chat_id: str
    local_date: str
    timestamp_local: str


class StateStore:
    """Small transactional repository; archive success is the sole terminal checkpoint."""

    def __init__(self, path: Path, *, read_only: bool = False) -> None:
        self.read_only = read_only
        if read_only:
            self.connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        if not read_only:
            self.connection.execute("PRAGMA journal_mode = WAL")
            self._initialize()

    def _initialize(self) -> None:
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                identity TEXT PRIMARY KEY, source_chat_id TEXT NOT NULL,
                source_message_id TEXT, fingerprint TEXT NOT NULL,
                timestamp_utc TEXT NOT NULL, timestamp_local TEXT NOT NULL, local_date TEXT NOT NULL,
                sender TEXT, plain_text TEXT NOT NULL, markdown_text TEXT NOT NULL,
                entities_json TEXT NOT NULL, attachments_json TEXT NOT NULL, reply_to TEXT,
                forwarded_from TEXT, original_json TEXT NOT NULL,
                archive_path TEXT, block_id TEXT, classifications_json TEXT NOT NULL DEFAULT '[]',
                run_id TEXT NOT NULL, archive_status TEXT NOT NULL DEFAULT 'pending'
            )
        """)
        self.connection.execute("CREATE INDEX IF NOT EXISTS messages_pending_date ON messages(archive_status, timestamp_local, source_chat_id, source_message_id)")
        self.connection.commit()

    def _upsert_messages(self, messages: Iterable[NormalizedMessage], run_id: str) -> None:
        rows = [(
            item.identity, item.source_chat_id, item.source_message_id, item.fingerprint,
            item.timestamp_utc.isoformat(), item.timestamp_local.isoformat(), item.local_date,
            item.sender, item.plain_text, item.markdown_text, json.dumps(item.entities, sort_keys=True),
            json.dumps(item.attachments), item.reply_to, item.forwarded_from,
            json.dumps(item.original, sort_keys=True), run_id,
        ) for item in messages]
        self.connection.executemany("""
            INSERT INTO messages (
              identity, source_chat_id, source_message_id, fingerprint, timestamp_utc, timestamp_local,
              local_date, sender, plain_text, markdown_text, entities_json, attachments_json, reply_to,
              forwarded_from, original_json, run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(identity) DO UPDATE SET run_id = excluded.run_id
        """, rows)

    def upsert_messages(self, messages: Iterable[NormalizedMessage], run_id: str) -> None:
        self._upsert_messages(messages, run_id)
        self.connection.commit()

    def earliest_unarchived(self) -> IndexedMessage | None:
        row = self.connection.execute("""
            SELECT identity, source_chat_id, local_date, timestamp_local FROM messages
            WHERE archive_status != 'archived'
            ORDER BY timestamp_local, source_chat_id,
                CASE WHEN source_message_id GLOB '[0-9]*' THEN 0 ELSE 1 END,
                CASE WHEN source_message_id GLOB '[0-9]*' THEN CAST(source_message_id AS INTEGER) END,
                COALESCE(source_message_id, fingerprint), identity
            LIMIT 1
        """).fetchone()
        return IndexedMessage(**dict(row)) if row else None

    def archived_identities(self) -> set[str]:
        return {row[0] for row in self.connection.execute(
            "SELECT identity FROM messages WHERE archive_status = 'archived'"
        )}

    def archived_messages(self, date: str | None = None, source: str | None = None) -> list[NormalizedMessage]:
        """Return terminal indexed normalized records for deterministic note rebuilding."""
        query = "SELECT * FROM messages WHERE archive_status = 'archived'"
        params: tuple[str, ...] = ()
        if date is not None:
            query += " AND local_date = ?"
            params = (date,)
        if source is not None:
            query += " AND source_chat_id = ?"
            params += (source,)
        query += " ORDER BY timestamp_local, source_chat_id, source_message_id, identity"
        records: list[NormalizedMessage] = []
        for row in self.connection.execute(query, params):
            records.append(NormalizedMessage(
                source_chat_id=row["source_chat_id"], identity=row["identity"],
                source_message_id=row["source_message_id"], fingerprint=row["fingerprint"],
                timestamp_utc=datetime.fromisoformat(row["timestamp_utc"]),
                timestamp_local=datetime.fromisoformat(row["timestamp_local"]), sender=row["sender"],
                plain_text=row["plain_text"], markdown_text=row["markdown_text"],
                entities=tuple(json.loads(row["entities_json"])), attachments=tuple(json.loads(row["attachments_json"])),
                reply_to=row["reply_to"], forwarded_from=row["forwarded_from"],
                original=json.loads(row["original_json"]),
            ))
        return records

    def archived_dates_for_source(self, source: str) -> tuple[str, ...]:
        return tuple(row[0] for row in self.connection.execute(
            "SELECT DISTINCT local_date FROM messages WHERE archive_status = 'archived' "
            "AND source_chat_id = ? ORDER BY local_date", (source,)
        ))

    def archived_locations(self) -> dict[str, tuple[str, str]]:
        """Locations for terminal records, used only to link review evidence."""
        return {row["identity"]: (row["archive_path"], row["block_id"])
                for row in self.connection.execute(
                    "SELECT identity, archive_path, block_id FROM messages "
                    "WHERE archive_status = 'archived' AND archive_path IS NOT NULL AND block_id IS NOT NULL"
                )}

    def mark_archived(self, locations: dict[str, tuple[str, str]]) -> None:
        self.connection.executemany(
            "UPDATE messages SET archive_path = ?, block_id = ?, archive_status = 'archived' WHERE identity = ?",
            [(path, block_id, identity) for identity, (path, block_id) in locations.items()],
        )
        self.connection.commit()

    def checkpoint_archived(
        self, messages: Iterable[NormalizedMessage], locations: dict[str, tuple[str, str]], run_id: str
    ) -> None:
        """Atomically index normalized records and mark their archive locations terminal."""
        try:
            self.connection.execute("BEGIN")
            self._upsert_messages(messages, run_id)
            self.connection.executemany(
                "UPDATE messages SET archive_path = ?, block_id = ?, archive_status = 'archived' WHERE identity = ?",
                [(path, block_id, identity) for identity, (path, block_id) in locations.items()],
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def message_count(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0])

    def close(self) -> None:
        self.connection.close()
