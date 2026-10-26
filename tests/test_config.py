from pathlib import Path

import pytest

from telescribe.config import AppConfig, IntelligenceConfig, ProjectConfig, SourceConfig


def test_source_config_requires_stable_id_and_paths() -> None:
    source = SourceConfig(
        id="example_bot",
        telegram_names=["Example Bot"],
        export_path=Path("exports/bot/result.json"),
        attachment_dir=Path("exports/bot/files"),
    )

    config = AppConfig(sources=[source], archive_root=Path("vault"))

    assert config.timezone == "America/New_York"
    assert config.sources[0].id == "example_bot"


def test_configuration_collections_are_immutable() -> None:
    source = SourceConfig(
        id="example_bot", telegram_names=["Example Bot"],
        export_path=Path("result.json"), attachment_dir=Path("files"),
    )
    config = AppConfig(sources=[source], archive_root=Path("vault"))

    assert source.telegram_names == ("Example Bot",)
    assert config.sources == (source,)
    with pytest.raises(AttributeError):
        source.telegram_names.append("Another Chat")


def test_source_config_rejects_blank_stable_id() -> None:
    with pytest.raises(ValueError, match="id"):
        SourceConfig(
            id=" ", telegram_names=["Bot"], export_path=Path("result.json"), attachment_dir=Path("files")
        )


def test_config_rejects_controls_in_source_ids_and_ambiguous_enabled_export_names() -> None:
    with pytest.raises(ValueError, match="stable ASCII"):
        SourceConfig(id="bad\nsource", telegram_names=["One"], export_path=Path("a"), attachment_dir=Path("b"))
    one = SourceConfig(id="one", telegram_names=["Shared"], export_path=Path("a"), attachment_dir=Path("b"))
    two = SourceConfig(id="two", telegram_names=["Shared"], export_path=Path("c"), attachment_dir=Path("d"))
    with pytest.raises(ValueError, match="unique across enabled"):
        AppConfig(sources=[one, two], archive_root=Path("vault"))


def test_source_config_rejects_blank_accepted_telegram_name() -> None:
    with pytest.raises(ValueError, match="telegram_names"):
        SourceConfig(
            id="example_bot", telegram_names=[" "],
            export_path=Path("result.json"), attachment_dir=Path("files"),
        )


@pytest.mark.parametrize("context, archive", [
    (Path("same"), Path("same")),
    (Path("vault/context"), Path("vault")),
    (Path("context"), Path("context/archive")),
])
def test_config_rejects_project_context_root_archive_root_nesting(context: Path, archive: Path) -> None:
    source = SourceConfig(id="source", telegram_names=["Chat"], export_path=Path("result.json"), attachment_dir=Path("files"))
    with pytest.raises(ValueError, match="project_context_root and archive_root overlap"):
        AppConfig(sources=[source], archive_root=archive, intelligence=IntelligenceConfig(
            enabled=True, review_queue_root=Path("review"), project_context_root=context,
            projects=[ProjectConfig(id="alpha")],
        ))


@pytest.mark.parametrize("alias_target", ["review", "context"])
def test_config_rejects_archive_root_symlink_alias_before_outputs(tmp_path: Path, alias_target: str) -> None:
    """An archive alias must not bypass the review/context boundary lexically."""
    review = tmp_path / "review"
    context = tmp_path / "context"
    review.mkdir()
    context.mkdir()
    target = review if alias_target == "review" else context
    archive = tmp_path / "archive-alias"
    archive.symlink_to(target, target_is_directory=True)
    source = SourceConfig(id="source", telegram_names=["Chat"],
                          export_path=tmp_path / "result.json", attachment_dir=tmp_path / "files")

    with pytest.raises(ValueError, match="unsafe archive_root symlink"):
        AppConfig(sources=[source], archive_root=archive, intelligence=IntelligenceConfig(
            enabled=True, review_queue_root=review, project_context_root=context,
            projects=[ProjectConfig(id="alpha")],
        ))

    assert archive.is_symlink()
    assert not any(review.iterdir())
    assert not any(context.iterdir())
    assert not list(tmp_path.rglob("*.md"))


@pytest.mark.parametrize("root_name", ["archive_root", "review_queue_root", "project_context_root"])
def test_config_rejects_symlinked_root_ancestor(tmp_path: Path, root_name: str) -> None:
    real = tmp_path / "real"
    real.mkdir()
    jump = tmp_path / "jump"
    jump.symlink_to(real, target_is_directory=True)
    roots = {
        "archive_root": tmp_path / "archive",
        "review_queue_root": tmp_path / "review",
        "project_context_root": tmp_path / "context",
    }
    roots[root_name] = jump / "nested"
    source = SourceConfig(id="source", telegram_names=["Chat"],
                          export_path=tmp_path / "result.json", attachment_dir=tmp_path / "files")

    with pytest.raises(ValueError, match="symlink"):
        AppConfig(sources=[source], archive_root=roots["archive_root"], intelligence=IntelligenceConfig(
            enabled=True, review_queue_root=roots["review_queue_root"],
            project_context_root=roots["project_context_root"], projects=[ProjectConfig(id="alpha")],
        ))


@pytest.mark.parametrize("provider, model", [("other", "deepseek-v4-flash"), ("deepseek", "other"), ("deepseek", None)])
def test_config_rejects_non_allowlisted_direct_provider(provider: str, model: str | None) -> None:
    with pytest.raises(ValueError, match="provider|deepseek"):
        IntelligenceConfig(enabled=True, review_queue_root=Path("review"), project_context_root=Path("context"),
            projects=[ProjectConfig(id="alpha")], provider=provider, model=model)
