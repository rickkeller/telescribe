"""Recoverable filesystem/state commit boundary for generated archives."""

import os
import tempfile
from collections.abc import Callable
from pathlib import Path

from markdown_it import MarkdownIt


class ArchiveTransaction:
    """Validate, replace files, then commit state; restore files if state fails."""

    def __init__(self, changes: dict[Path, str], *, target_validator: Callable[[Path], Path | None] | None = None) -> None:
        self.changes = changes
        self.target_validator = target_validator

    def _validate_target(self, path: Path) -> Path:
        if self.target_validator is None:
            return path
        validated = self.target_validator(path)
        return path if validated is None else validated

    def commit(self, commit_state: Callable[[], None]) -> list[Path]:
        parser = MarkdownIt()
        for content in self.changes.values():
            parser.parse(content)
        # Validate every concrete archive target before the first stat/read.
        targets = {path: self._validate_target(path) for path in self.changes}
        originals = {path: target.read_bytes() if target.exists() else None for path, target in targets.items()}
        changed: list[Path] = []
        try:
            for path, content in self.changes.items():
                target = self._validate_target(targets[path])
                if originals[path] == content.encode("utf-8"):
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target = self._validate_target(target)
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, delete=False) as temp:
                    temp.write(content)
                    staged = Path(temp.name)
                target = self._validate_target(target)
                os.replace(staged, target)
                changed.append(target)
            commit_state()
        except Exception:
            for path, original in originals.items():
                target = self._validate_target(targets[path])
                if original is None:
                    if target.exists():
                        target.unlink()
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target = self._validate_target(target)
                    with tempfile.NamedTemporaryFile("wb", dir=target.parent, delete=False) as temp:
                        temp.write(original)
                        backup = Path(temp.name)
                    target = self._validate_target(target)
                    os.replace(backup, target)
            raise
        return changed
