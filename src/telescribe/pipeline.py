"""The deliberately narrow first vertical archive slice."""

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .audit import new_run_id, redacted_error, utc_now, write_audit
from .config import AppConfig
from .daily_archiver import plan_daily_archives
from .models import NormalizedMessage, message_id_sort_key
from .state_store import StateStore
from .telegram_parser import parse_export
from .transaction import ArchiveTransaction


@dataclass(frozen=True, slots=True)
class ProcessResult:
    message_count: int
    changed_files: tuple[Path, ...]
    dry_run: bool


@dataclass(frozen=True, slots=True)
class ReprocessResult:
    message_count: int
    changed_files: tuple[Path, ...]


class ArchiveService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def _messages(self) -> list[NormalizedMessage]:
        messages: list[NormalizedMessage] = []
        for source in self.config.sources:
            if source.enabled:
                messages.extend(parse_export(source, self.config.timezone))
        identities = [message.identity for message in messages]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate normalized message identities in configured exports")
        return messages

    def validate(self) -> int:
        """Validate enabled exports and archive/state parent accessibility without mutation."""
        for path in (self.config.archive_root, self.config.state_path.parent):
            existing_parent = path
            while not existing_parent.exists() and existing_parent != existing_parent.parent:
                existing_parent = existing_parent.parent
            if not existing_parent.is_dir() or not os.access(existing_parent, os.W_OK):
                raise ValueError(f"configured output parent is not writable: {path}")
        return len(self._messages())

    def process(self, dry_run: bool = False) -> ProcessResult:
        run_id = new_run_id() if not dry_run else None
        started_at = utc_now() if not dry_run else None
        try:
            messages = self._messages()
            existing = self._existing_store(dry_run=dry_run)
            try:
                archived = existing.archived_identities() if existing else set()
            finally:
                if existing:
                    existing.close()
            pending = [message for message in messages if message.identity not in archived]
            if not pending:
                result = ProcessResult(0, (), dry_run)
            else:
                earliest = min(pending, key=lambda item: (
                    item.timestamp_local, item.source_chat_id,
                    message_id_sort_key(item.source_message_id, item.fingerprint), item.identity
                ))
                represented = [message for message in messages if message.local_date >= earliest.local_date]
                plan = plan_daily_archives(
                    represented, self.config.archive_root, datetime.now(UTC), {message.identity for message in pending}
                )
                if dry_run:
                    intended_changes = tuple(archive.target for archive in plan.values() if not archive.target.exists()
                                             or archive.target.read_text(encoding="utf-8") != archive.content)
                    return ProcessResult(len(pending), intended_changes, True)

                self._stage_audit("process", run_id, started_at)

                def commit_state() -> None:
                    store = StateStore(self.config.state_path)
                    try:
                        locations = {message.identity: (str(plan[message.local_date].target.relative_to(self.config.archive_root)), message.block_id)
                                     for message in pending}
                        store.checkpoint_archived(pending, locations, run_id=run_id or "dry-run")
                    finally:
                        store.close()

                created = tuple(archive.target for archive in plan.values() if not archive.target.exists())
                changed = ArchiveTransaction({archive.target: archive.content for archive in plan.values()}).commit(commit_state)
                result = ProcessResult(len(pending), tuple(changed), False)
        except Exception as error:
            if not dry_run:
                self._audit_failure("process", error, run_id, started_at)
            raise
        if not dry_run:
            self._audit_success("process", result.message_count, result.changed_files,
                                messages_scanned=len(messages), dates=tuple(sorted(plan)) if pending else (), created=created if pending else (),
                                run_id=run_id, started_at=started_at)
        return result

    def reprocess(self, *, date: str | None = None, source: str | None = None,
                  all_dates: bool = False) -> ReprocessResult:
        """Rebuild selected daily notes from archived index records only."""
        if sum((bool(date), bool(source), all_dates)) != 1:
            raise ValueError("select exactly one of date, source, or all_dates")
        if source and source not in {item.id for item in self.config.sources if item.enabled}:
            raise ValueError("source must be an enabled configured source id")
        run_id, started_at = new_run_id(), utc_now()
        try:
            if not self.config.state_path.exists():
                raise ValueError("cannot reprocess without an existing message index")
            store = StateStore(self.config.state_path, read_only=True)
            try:
                if source:
                    selected_dates = store.archived_dates_for_source(source)
                    # A day is an atomic archive document: once selected by a
                    # source, rebuild it from every indexed record for that day.
                    messages = [message for selected_date in selected_dates
                                for message in store.archived_messages(selected_date)]
                else:
                    messages = store.archived_messages(date)
            finally:
                store.close()
            if not messages:
                raise ValueError("no indexed archived messages match the selection")
            processed_at = max(message.timestamp_utc for message in messages)
            plan = plan_daily_archives(messages, self.config.archive_root, processed_at, set())
            created = tuple(archive.target for archive in plan.values() if not archive.target.exists())
            self._stage_audit("reprocess", run_id, started_at)
            changed = ArchiveTransaction({archive.target: archive.content for archive in plan.values()}).commit(lambda: None)
            result = ReprocessResult(len(messages), tuple(changed))
        except Exception as error:
            self._audit_failure("reprocess", error, run_id, started_at)
            raise
        self._audit_success("reprocess", result.message_count, result.changed_files,
                            dates=tuple(sorted(plan)), created=created, run_id=run_id, started_at=started_at)
        return result

    def _existing_store(self, *, dry_run: bool) -> StateStore | None:
        """Open only an initialized index; an empty placeholder is not state.

        In particular, SQLite would initialize a zero-byte file on an ordinary
        connection.  Dry planning treats it as absent and never opens it.
        """
        path = self.config.state_path
        if not path.exists() or path.stat().st_size == 0:
            return None
        try:
            store = StateStore(path, read_only=dry_run)
            store.connection.execute("SELECT 1 FROM messages LIMIT 1")
            return store
        except Exception:
            # A non-SQLite/uninitialized placeholder must remain untouched for
            # dry run.  A real run can initialize it only when it is empty.
            if dry_run:
                return None
            raise

    def _stage_audit(self, operation: str, run_id: str | None, started_at: datetime | None) -> None:
        """Durably record intent before any archive or terminal state commit."""
        write_audit(self.config.state_path, {
            "run_id": run_id or new_run_id(), "started_at": (started_at or utc_now()).isoformat(),
            "completed_at": None, "status": "staged", "operation": operation,
            "sources": len([source for source in self.config.sources if source.enabled]),
            "messages_scanned": 0, "new_messages": 0, "dates_updated": [], "files_created": [],
            "files_modified": [], "warnings": [],
        })

    def _audit_success(self, operation: str, message_count: int, changed: tuple[Path, ...] | list[Path],
                       *, messages_scanned: int | None = None, dates: tuple[str, ...] = (),
                       created: tuple[Path, ...] = (), run_id: str | None = None,
                       started_at: datetime | None = None) -> None:
        run_id = run_id or new_run_id()
        started_at = started_at or utc_now()
        now = utc_now()
        paths = [str(path.relative_to(self.config.archive_root)) for path in changed]
        created_paths = [str(path.relative_to(self.config.archive_root)) for path in created if path in changed]
        record = {"run_id": run_id, "started_at": started_at.isoformat(), "completed_at": now.isoformat(),
            "status": "success", "sources": len([source for source in self.config.sources if source.enabled]),
            "messages_scanned": message_count if messages_scanned is None else messages_scanned,
            "new_messages": message_count if operation == "process" else 0,
            "dates_updated": list(dates), "files_created": created_paths,
            "files_modified": [path for path in paths if path not in created_paths], "warnings": []}
        # A staged record already establishes durable run intent.  Do not turn
        # a post-commit audit-refresh failure into a false failed run.
        try:
            write_audit(self.config.state_path, record)
        except Exception:
            pass

    def _audit_failure(self, status: str, error: Exception, run_id: str | None = None,
                       started_at: datetime | None = None) -> None:
        run_id = run_id or new_run_id()
        started_at = started_at or utc_now()
        now = utc_now()
        record = {"run_id": run_id, "started_at": started_at.isoformat(), "completed_at": now.isoformat(),
            "status": "failed", "sources": len([source for source in self.config.sources if source.enabled]),
            "messages_scanned": 0, "new_messages": 0, "dates_updated": [], "files_created": [], "files_modified": [],
            "warnings": [f"{status} failed: {redacted_error(error)}"]}
        try:
            write_audit(self.config.state_path, record)
        except Exception:
            # Failure auditing is deliberately best effort; it must never mask
            # the original error or mutate archive/state recovery semantics.
            pass
