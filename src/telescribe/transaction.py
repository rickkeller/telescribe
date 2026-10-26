"""Recoverable filesystem/state commit boundary for generated archives."""

import os
import tempfile
from collections.abc import Callable
from pathlib import Path

from markdown_it import MarkdownIt


class ArchiveTransaction:
    """Validate, replace files, then commit state; restore files if state fails."""

    def __init__(self, changes: dict[Path, str]) -> None:
        self.changes = changes

    def commit(self, commit_state: Callable[[], None]) -> list[Path]:
        parser = MarkdownIt()
        for content in self.changes.values():
            parser.parse(content)
        originals = {path: path.read_bytes() if path.exists() else None for path in self.changes}
        changed: list[Path] = []
        try:
            for path, content in self.changes.items():
                if originals[path] == content.encode("utf-8"):
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as temp:
                    temp.write(content)
                    staged = Path(temp.name)
                os.replace(staged, path)
                changed.append(path)
            commit_state()
        except Exception:
            for path, original in originals.items():
                if original is None:
                    if path.exists():
                        path.unlink()
                else:
                    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as temp:
                        temp.write(original)
                        backup = Path(temp.name)
                    os.replace(backup, path)
            raise
        return changed
