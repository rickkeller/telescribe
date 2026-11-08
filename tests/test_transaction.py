from pathlib import Path

import pytest

from telescribe.transaction import ArchiveTransaction
from telescribe.paths import validate_archive_target


def test_failed_state_commit_restores_prior_file(tmp_path: Path) -> None:
    target = tmp_path / "archive.md"
    target.write_text("old\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="state failure"):
        ArchiveTransaction({target: "new\n"}).commit(lambda: (_ for _ in ()).throw(RuntimeError("state failure")))

    assert target.read_text(encoding="utf-8") == "old\n"


def test_target_validator_blocks_a_nested_archive_symlink_at_write_boundary(tmp_path: Path) -> None:
    archive_root = tmp_path / "vault"
    queue = tmp_path / "queue"
    output = archive_root / "01 Daily Archives" / "Telegram"
    output.mkdir(parents=True)
    (output / "2026").symlink_to(queue, target_is_directory=True)
    target = output / "2026" / "2026-07" / "2026-07-31.md"

    with pytest.raises(ValueError, match="unsafe archive target symlink"):
        ArchiveTransaction({target: "generated\n"}, target_validator=lambda path: validate_archive_target(archive_root, path)).commit(lambda: None)

    assert not queue.exists()
