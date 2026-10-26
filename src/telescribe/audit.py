"""Privacy-safe, structured records for mutating archive runs."""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .redaction import redact


def new_run_id() -> str:
    return str(uuid4())


def utc_now() -> datetime:
    return datetime.now(UTC)


def redacted_error(error: Exception) -> str:
    """Return a bounded diagnostic category without copying exception data."""
    return type(error).__name__


def write_audit(state_path: Path, record: dict[str, object]) -> Path:
    """Atomically write one JSON record beside state, without exposing source details."""
    runs = state_path.parent / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    target = runs / f"{record['run_id']}.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(redact(record), sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
    return target
