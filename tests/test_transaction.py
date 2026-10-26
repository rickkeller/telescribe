from pathlib import Path

import pytest

from telescribe.transaction import ArchiveTransaction


def test_failed_state_commit_restores_prior_file(tmp_path: Path) -> None:
    target = tmp_path / "archive.md"
    target.write_text("old\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="state failure"):
        ArchiveTransaction({target: "new\n"}).commit(lambda: (_ for _ in ()).throw(RuntimeError("state failure")))

    assert target.read_text(encoding="utf-8") == "old\n"
