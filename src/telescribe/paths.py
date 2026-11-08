"""Filesystem-safe roots shared by archive and review boundaries."""

import os
from pathlib import Path


class UnsafeArchivePathError(ValueError):
    """An archive path can no longer be trusted to remain in its output tree."""


def safe_canonical_root(value: Path, *, label: str) -> Path:
    """Return a lexical absolute root only when no existing component is a symlink.

    A configured root may not exist yet, but no existing component may be a
    symlink. That makes lexical containment checks authoritative without
    resolving through an attacker-controlled alias.
    """
    if any(part == ".." for part in value.parts):
        raise ValueError(f"unsafe {label} traversal")
    root = value if value.is_absolute() else Path.cwd() / value
    root = Path(os.path.abspath(root))
    current = Path(root.anchor)
    for part in root.parts[1:]:
        current /= part
        if current.is_symlink():
            raise ValueError(f"unsafe {label} symlink")
    return root


def telegram_archive_output_root(archive_root: Path) -> Path:
    """Return the lexical Telegram output root after checking fixed components."""
    root = safe_canonical_root(archive_root, label="archive_root")
    daily_archives = root / "01 Daily Archives"
    output = daily_archives / "Telegram"
    for component in (daily_archives, output):
        if component.is_symlink():
            raise UnsafeArchivePathError("unsafe archive output symlink")
    return output


def validate_archive_target(archive_root: Path, target: Path) -> Path:
    """Reject aliases in every existing component of one concrete archive target.

    This intentionally uses lexical paths and ``lstat``-backed symlink checks;
    resolving a target would itself follow the alias that this boundary rejects.
    """
    output = telegram_archive_output_root(archive_root)
    concrete = Path(os.path.abspath(target))
    try:
        relative = concrete.relative_to(output)
    except ValueError as error:
        raise UnsafeArchivePathError("unsafe archive target outside output") from error
    if not relative.parts:
        raise UnsafeArchivePathError("unsafe archive target")
    current = output
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise UnsafeArchivePathError("unsafe archive target symlink")
    return concrete


def assert_archive_tree_has_no_symlinks(archive_root: Path) -> Path:
    """Fail closed on any existing alias beneath Telegram output without following it."""
    output = telegram_archive_output_root(archive_root)
    if not output.exists():
        return output
    if not output.is_dir():
        return output
    pending = [output]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if entry.is_symlink():
                        raise UnsafeArchivePathError("unsafe archive tree symlink")
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path))
        except OSError as error:
            raise UnsafeArchivePathError("unsafe archive tree") from error
    return output
