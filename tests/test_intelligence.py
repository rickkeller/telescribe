from pathlib import Path
import os
import sqlite3

import pytest

from telescribe.config import AppConfig, IntelligenceConfig, ProjectConfig, SourceConfig
from telescribe.intelligence import FixtureClassifier, ReviewService
from telescribe.pipeline import ArchiveService


def _config(tmp_path: Path, *, enabled: bool = True, context_paths: tuple[Path, ...] = ()) -> AppConfig:
    fixture = Path(__file__).parent / "fixtures" / "string_text_result.json"
    return AppConfig(sources=[SourceConfig(id="test_source", telegram_names=["Example Bot"],
        export_path=fixture, attachment_dir=tmp_path / "files")], archive_root=tmp_path / "vault",
        state_path=tmp_path / "state.sqlite3", intelligence=IntelligenceConfig(enabled=enabled,
        review_queue_root=tmp_path / "review" if enabled else None,
        project_context_root=tmp_path / "project-context" if enabled else None,
        projects=[ProjectConfig(id="alpha", aliases=["A"], context_paths=context_paths)] if enabled else []))


def _candidate(**overrides: object) -> dict[str, object]:
    candidate: dict[str, object] = {"source_identity": "test_source:42", "source_chat_id": "test_source",
        "timestamp_utc": "2026-08-01T03:30:00+00:00", "archive_block_id": "tg-test-source-42", "project": "A", "proposal_type": "task",
        "confidence": 0.8, "rationale": "A concrete follow-up is requested.", "proposed_text": "Follow up on the request",
        "model_run": "fixture-1", "possible_duplicate": False, "possible_contradiction": False, "needs_review": True}
    candidate.update(overrides)
    return candidate


def test_disabled_and_unavailable_review_fail_closed_without_mutation(tmp_path: Path) -> None:
    disabled = _config(tmp_path, enabled=False)
    with pytest.raises(ValueError, match="disabled"):
        ReviewService(disabled).review(all_dates=True)
    assert not (tmp_path / "review").exists()

    enabled = _config(tmp_path, enabled=True)
    with pytest.raises(ValueError, match="unavailable"):
        ReviewService(enabled).review(all_dates=True)
    assert not (tmp_path / "review").exists()


def test_review_writes_safe_source_link_is_idempotent_and_preserves_human_text(tmp_path: Path) -> None:
    config = _config(tmp_path)
    ArchiveService(config).process()
    service = ReviewService(config, FixtureClassifier([_candidate()]))
    first = service.review(all_dates=True)
    target = tmp_path / "review" / "2026-08-01.md"
    target.write_text("Human introduction\n" + target.read_text(encoding="utf-8") + "Human ending\n", encoding="utf-8")
    second = service.review(all_dates=True)
    text = target.read_text(encoding="utf-8")
    assert first.accepted_count == 1
    assert second.accepted_count == 0
    assert "Human introduction" in text and "Human ending" in text
    assert "#tg-test-source-42" in text
    assert text.count("telescribe:key:") == 1


def test_dry_run_and_invalid_or_foreign_candidates_do_not_write_queue(tmp_path: Path) -> None:
    config = _config(tmp_path)
    ArchiveService(config).process()
    dry = ReviewService(config, FixtureClassifier([_candidate()])).review(all_dates=True, dry_run=True)
    assert dry.accepted_count == 1 and not (tmp_path / "review").exists()
    result = ReviewService(config, FixtureClassifier([_candidate(source_identity="foreign"), _candidate(owner="no")])).review(all_dates=True)
    assert result.accepted_count == 0 and result.rejected_count == 2
    assert not (tmp_path / "review" / "2026-08-01.md").exists()


def test_markdown_and_unsafe_context_are_rejected_before_queue_write(tmp_path: Path) -> None:
    config = _config(tmp_path)
    ArchiveService(config).process()
    result = ReviewService(config, FixtureClassifier([_candidate(proposed_text="# injected")])).review(all_dates=True)
    assert result.rejected_count == 1
    assert not (tmp_path / "review" / "2026-08-01.md").exists()

    unsafe = _config(tmp_path / "unsafe", context_paths=(Path("../secret.md"),))
    ArchiveService(unsafe).process()
    with pytest.raises(ValueError, match="traversal"):
        ReviewService(unsafe, FixtureClassifier([_candidate()])).review(all_dates=True)


def test_context_symlink_and_intelligence_provider_layer_are_safe(tmp_path: Path) -> None:
    config = _config(tmp_path, context_paths=(Path("linked.md"),))
    config.intelligence.project_context_root.mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("private context", encoding="utf-8")
    (config.intelligence.project_context_root / "linked.md").symlink_to(outside)
    ArchiveService(config).process()
    with pytest.raises(ValueError, match="unsafe context"):
        ReviewService(config, FixtureClassifier([_candidate()])).review(all_dates=True)
    implementation = (Path(__file__).parents[1] / "src" / "telescribe" / "intelligence.py").read_text(encoding="utf-8")
    assert "telegram_parser" not in implementation and "parse_export" not in implementation


@pytest.mark.parametrize("value", ["../outside", "2026-8-1", "2026-02-30", "2026-08-01x"])
def test_review_service_rejects_non_calendar_date_without_writes(tmp_path: Path, value: str) -> None:
    config = _config(tmp_path)
    ArchiveService(config).process()
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        ReviewService(config, FixtureClassifier([_candidate()])).review(date=value)
    assert not (tmp_path / "review").exists()
    assert not (tmp_path / "outside.md").exists()


def test_queue_root_rejects_archive_overlap_and_symlinked_missing_ancestor(tmp_path: Path) -> None:
    archive = tmp_path / "vault"
    for review in (archive, archive / "review"):
        with pytest.raises(ValueError, match="overlap"):
            AppConfig(sources=_config(tmp_path).sources, archive_root=archive,
                state_path=tmp_path / "state.sqlite3", intelligence=IntelligenceConfig(enabled=True,
                review_queue_root=review, project_context_root=tmp_path / "context", projects=[ProjectConfig(id="alpha")]))
    with pytest.raises(ValueError, match="overlap"):
        AppConfig(sources=_config(tmp_path).sources, archive_root=tmp_path / "review-parent" / "archive",
            state_path=tmp_path / "state.sqlite3", intelligence=IntelligenceConfig(enabled=True,
            review_queue_root=tmp_path / "review-parent", project_context_root=tmp_path / "context", projects=[ProjectConfig(id="alpha")]))

    bypass = _config(tmp_path / "bypass")
    ArchiveService(bypass).process()
    unsafe_overlap = bypass.model_copy(update={"intelligence": bypass.intelligence.model_copy(
        update={"review_queue_root": bypass.archive_root})})
    with pytest.raises(ValueError, match="overlap"):
        ReviewService(unsafe_overlap, FixtureClassifier([_candidate()])).review(all_dates=True)

    config = _config(tmp_path / "symlink")
    outside = tmp_path / "outside"
    outside.mkdir()
    config.intelligence.review_queue_root.parent.mkdir(parents=True)
    (config.intelligence.review_queue_root.parent / "jump").symlink_to(outside, target_is_directory=True)
    unsafe_root = config.intelligence.review_queue_root.parent / "jump" / "missing-review"
    unsafe = config.model_copy(update={"intelligence": config.intelligence.model_copy(update={"review_queue_root": unsafe_root})})
    ArchiveService(unsafe).process()
    with pytest.raises(ValueError, match="symlink"):
        ReviewService(unsafe, FixtureClassifier([_candidate()])).review(all_dates=True)
    assert not (outside / "missing-review").exists()


def test_queue_rejects_broken_symlink_target_without_replacing_it(tmp_path: Path) -> None:
    config = _config(tmp_path)
    ArchiveService(config).process()
    target = tmp_path / "review" / "2026-08-01.md"
    target.parent.mkdir()
    target.symlink_to(tmp_path / "missing-target.md")
    with pytest.raises(ValueError, match="target symlink"):
        ReviewService(config, FixtureClassifier([_candidate()])).review(all_dates=True)
    assert target.is_symlink()


def test_model_candidate_is_redacted_before_queue_or_audit_and_source_is_unchanged(tmp_path: Path) -> None:
    config = _config(tmp_path)
    ArchiveService(config).process()
    secret = ('api_' + 'key=' + 'sk-a' + 'bcDE' + 'F123' + '4567' + '890 ' + 'emai' + 'l=me' + '@exa' + 'mple' + '.com' + ' pho' + 'ne=2' + '12-5' + '55-1' + '212 ' + 'acco' + 'unt=' + '1234' + '5678' + '9')
    result = ReviewService(config, FixtureClassifier([_candidate(rationale=secret, proposed_text="Ordinary " + secret,
        model_run="token=reallysecret")])).review(all_dates=True)
    persisted = "\n".join(path.read_text(encoding="utf-8") for path in (tmp_path / "review").rglob("*") if path.is_file())
    assert result.accepted_count == 1
    assert "Ordinary" in persisted
    for raw in (('sk-a' + 'bcDE' + 'F123' + '4567' + '890'), "me@example.com", "212-555-1212", "123456789", "reallysecret"):
        assert raw not in persisted
    assert secret in _candidate(rationale=secret)["rationale"]


def test_evidence_and_context_are_bounded_before_classifier_dispatch(tmp_path: Path) -> None:
    class Capture:
        def __init__(self) -> None: self.evidence = (); self.context = ()
        def classify(self, evidence, context):
            self.evidence, self.context = evidence, context
            return [_candidate()]
    config = _config(tmp_path, context_paths=(Path("context.md"),))
    config.intelligence.project_context_root.mkdir(parents=True)
    (config.intelligence.project_context_root / "context.md").write_text("x" * 5000, encoding="utf-8")
    ArchiveService(config).process()
    capture = Capture()
    with pytest.raises(ValueError, match="context exceeds"):
        ReviewService(config, capture).review(all_dates=True)
    assert not capture.evidence

    bounded = _config(tmp_path / "bounded")
    ArchiveService(bounded).process()
    with sqlite3.connect(bounded.state_path) as connection:
        connection.execute("UPDATE messages SET plain_text = ?", ("z" * 5000,))
    ReviewService(bounded, capture).review(all_dates=True, dry_run=True)
    assert len(capture.evidence[0].text) == 1000
    assert capture.evidence[0].text.endswith("[TRUNCATED BY TELESCRIBE]")


def test_context_rejects_enabled_export_under_archive_before_classifier_dispatch(tmp_path: Path) -> None:
    class Capture:
        def __init__(self) -> None: self.evidence = (); self.context = ()
        def classify(self, evidence, context):
            self.evidence, self.context = evidence, context
            return [_candidate()]

    config = _config(tmp_path, context_paths=(Path("export.json"),))
    raw_export = config.archive_root / "context" / "export.json"
    raw_export.parent.mkdir(parents=True)
    raw_export.write_bytes((Path(__file__).parent / "fixtures" / "string_text_result.json").read_bytes())
    context_root = raw_export.parent
    unsafe = config.model_copy(update={"sources": [config.sources[0].model_copy(update={"export_path": raw_export})],
        "intelligence": config.intelligence.model_copy(update={"project_context_root": context_root})})
    ArchiveService(unsafe).process()
    capture = Capture()
    with pytest.raises(ValueError, match="overlap|source export"):
        ReviewService(unsafe, capture).review(all_dates=True)
    assert not capture.evidence and not capture.context
    assert raw_export.read_bytes() not in capture.context


@pytest.mark.parametrize("relation", ["context_inside_archive", "archive_inside_context"])
def test_runtime_rejects_project_context_root_archive_root_nesting_before_classifier_dispatch(
    tmp_path: Path, relation: str,
) -> None:
    class Capture:
        def __init__(self) -> None: self.called = False
        def classify(self, evidence, context):
            self.called = True
            return [_candidate()]

    config = _config(tmp_path)
    ArchiveService(config).process()
    if relation == "context_inside_archive":
        context_root = config.archive_root / "context"
        context_root.mkdir()
        archive_root = config.archive_root
    else:
        context_root = tmp_path / "context"
        context_root.mkdir()
        archive_root = context_root / "archive"
    (context_root / "context.md").write_text("ordinary context", encoding="utf-8")
    unsafe = config.model_copy(update={"archive_root": archive_root,
        "intelligence": config.intelligence.model_copy(update={
            "project_context_root": context_root,
            "projects": [ProjectConfig(id="alpha", context_paths=[Path("context.md")])],
        })})
    capture = Capture()
    with pytest.raises(ValueError, match="project_context_root and archive_root overlap"):
        ReviewService(unsafe, capture).review(all_dates=True)
    assert not capture.called


def test_generated_archive_note_under_nested_context_root_never_reaches_classifier(tmp_path: Path) -> None:
    class Capture:
        def __init__(self) -> None: self.context = ()
        def classify(self, evidence, context):
            self.context = context
            return [_candidate()]

    config = _config(tmp_path)
    ArchiveService(config).process()
    archive_note = next(config.archive_root.rglob("*.md"))
    nested_context = config.archive_root / "01 Daily Archives"
    unsafe = config.model_copy(update={"intelligence": config.intelligence.model_copy(update={
        "project_context_root": nested_context,
        "projects": [ProjectConfig(id="alpha", context_paths=[archive_note.relative_to(nested_context)])],
    })})
    capture = Capture()
    with pytest.raises(ValueError, match="project_context_root and archive_root overlap"):
        ReviewService(unsafe, capture).review(all_dates=True)
    assert not capture.context


def test_context_rejects_hardlinked_enabled_and_direct_disabled_exports_before_classifier_dispatch(tmp_path: Path) -> None:
    class Capture:
        def __init__(self) -> None: self.evidence = (); self.context = ()
        def classify(self, evidence, context):
            self.evidence, self.context = evidence, context
            return [_candidate()]

    for name, disabled in (("hardlinked-enabled.json", False), ("disabled-export.json", True)):
        case = tmp_path / name
        config = _config(case, context_paths=(Path(name),))
        raw_export = case / "raw-export.json"
        raw_export.parent.mkdir(parents=True)
        raw_export.write_bytes((Path(__file__).parent / "fixtures" / "string_text_result.json").read_bytes())
        sources = [config.sources[0].model_copy(update={"export_path": raw_export})]
        if disabled:
            sources.append(SourceConfig(id="disabled_source", telegram_names=["Disabled"],
                export_path=case / "project-context" / name, attachment_dir=case / "files-disabled", enabled=False))
        unsafe = config.model_copy(update={"sources": sources})
        unsafe.intelligence.project_context_root.mkdir(parents=True)
        context = unsafe.intelligence.project_context_root / name
        if disabled:
            context.write_bytes(raw_export.read_bytes())
        else:
            os.link(raw_export, context)
        ArchiveService(unsafe).process()
        capture = Capture()
        with pytest.raises(ValueError, match="source export"):
            ReviewService(unsafe, capture).review(all_dates=True)
        assert not capture.evidence and not capture.context
        assert raw_export.read_bytes() not in capture.context


def test_context_rejects_hardlinked_disabled_export_before_classifier_dispatch(tmp_path: Path) -> None:
    class Capture:
        def __init__(self) -> None: self.called = False
        def classify(self, evidence, context):
            self.called = True
            return [_candidate()]

    config = _config(tmp_path, context_paths=(Path("hardlinked-disabled.json"),))
    raw_export = tmp_path / "disabled-export.json"
    raw_export.write_bytes((Path(__file__).parent / "fixtures" / "string_text_result.json").read_bytes())
    disabled = SourceConfig(id="disabled_source", telegram_names=["Disabled"], export_path=raw_export,
        attachment_dir=tmp_path / "files-disabled", enabled=False)
    unsafe = config.model_copy(update={"sources": [*config.sources, disabled]})
    unsafe.intelligence.project_context_root.mkdir(parents=True)
    os.link(raw_export, unsafe.intelligence.project_context_root / "hardlinked-disabled.json")
    ArchiveService(unsafe).process()
    capture = Capture()
    with pytest.raises(ValueError, match="source export"):
        ReviewService(unsafe, capture).review(all_dates=True)
    assert not capture.called


def test_queue_rewrites_only_the_matched_owned_marker_range(tmp_path: Path) -> None:
    config = _config(tmp_path)
    ArchiveService(config).process()
    target = tmp_path / "review" / "2026-08-01.md"
    target.parent.mkdir()
    human_before = "Human stray <!-- HERMES:END telescribe:review-queue --> marker\n"
    owned = "<!-- HERMES:BEGIN telescribe:review-queue -->\nold generated\n<!-- HERMES:END telescribe:review-queue -->\n"
    human_after = "Human ending\n"
    target.write_text(human_before + owned + human_after, encoding="utf-8")
    ReviewService(config, FixtureClassifier([_candidate()])).review(all_dates=True)
    rewritten = target.read_text(encoding="utf-8")
    assert rewritten.startswith(human_before)
    assert rewritten.endswith(human_after)
    assert rewritten.count("<!-- HERMES:END telescribe:review-queue -->") == 2
    assert "old generated" not in rewritten
    assert "<!-- HERMES:END telescribe:review-queue -->\n\nHuman ending" not in rewritten
    assert "Follow up on the request" in rewritten


def test_queue_treats_inline_marker_like_human_prose_as_ordinary_text(tmp_path: Path) -> None:
    config = _config(tmp_path)
    ArchiveService(config).process()
    target = tmp_path / "review" / "2026-08-01.md"
    target.parent.mkdir()
    human = ("Human prose mentions <!-- HERMES:BEGIN telescribe:review-queue --> inline and "
             "<!-- HERMES:END telescribe:review-queue --> inline.\n")
    target.write_text(human, encoding="utf-8")
    ReviewService(config, FixtureClassifier([_candidate()])).review(all_dates=True)
    rewritten = target.read_text(encoding="utf-8")
    assert rewritten.startswith(human)
    assert rewritten.count("<!-- HERMES:BEGIN telescribe:review-queue -->") == 2
    assert rewritten.count("<!-- HERMES:END telescribe:review-queue -->") == 2


@pytest.mark.parametrize("opening, closing", [("```html", "```"), ("~~~~html", "~~~~")])
def test_queue_preserves_standalone_human_markers_inside_fenced_code_blocks(
    tmp_path: Path, opening: str, closing: str,
) -> None:
    config = _config(tmp_path)
    ArchiveService(config).process()
    target = tmp_path / "review" / "2026-08-01.md"
    target.parent.mkdir()
    human_fence = (
        f"{opening}\n"
        "<!-- HERMES:BEGIN telescribe:review-queue -->\n"
        "<p>human-owned example</p>\n"
        "<!-- HERMES:END telescribe:review-queue -->\n"
        f"{closing}\n"
    )
    old_owned = (
        "<!-- HERMES:BEGIN telescribe:review-queue -->\n"
        "old generated content\n"
        "<!-- HERMES:END telescribe:review-queue -->\n"
    )
    target.write_text("Human before\n" + human_fence + old_owned + "Human after\n", encoding="utf-8")

    ReviewService(config, FixtureClassifier([_candidate()])).review(all_dates=True)

    rewritten = target.read_text(encoding="utf-8")
    assert human_fence in rewritten
    assert "old generated content" not in rewritten
    assert rewritten.endswith("Human after\n")


def test_queue_preserves_fenced_human_marker_bytes_including_crlf(tmp_path: Path) -> None:
    config = _config(tmp_path)
    ArchiveService(config).process()
    target = tmp_path / "review" / "2026-08-01.md"
    target.parent.mkdir()
    human_fence = (
        b"```html\r\n"
        b"<!-- HERMES:BEGIN telescribe:review-queue -->\r\n"
        b"<p>human-owned example</p>\r\n"
        b"<!-- HERMES:END telescribe:review-queue -->\r\n"
        b"```\r\n"
    )
    target.write_bytes(human_fence + (
        b"<!-- HERMES:BEGIN telescribe:review-queue -->\n"
        b"old generated content\n"
        b"<!-- HERMES:END telescribe:review-queue -->\n"
    ))

    ReviewService(config, FixtureClassifier([_candidate()])).review(all_dates=True)

    assert human_fence in target.read_bytes()


@pytest.mark.parametrize("content", [
    "<!-- HERMES:END telescribe:review-queue -->\n",
    "<!-- HERMES:BEGIN telescribe:review-queue -->\n",
    "<!-- HERMES:END telescribe:review-queue -->\n"
    "<!-- HERMES:BEGIN telescribe:review-queue -->\na\n<!-- HERMES:END telescribe:review-queue -->\n",
    "<!-- HERMES:BEGIN telescribe:review-queue -->\na\n<!-- HERMES:END telescribe:review-queue -->\n"
    "<!-- HERMES:BEGIN telescribe:review-queue -->\nb\n<!-- HERMES:END telescribe:review-queue -->\n",
])
def test_queue_rejects_malformed_or_ambiguous_owned_marker_ranges(tmp_path: Path, content: str) -> None:
    config = _config(tmp_path)
    ArchiveService(config).process()
    target = tmp_path / "review" / "2026-08-01.md"
    target.parent.mkdir()
    target.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="owned marker"):
        ReviewService(config, FixtureClassifier([_candidate()])).review(all_dates=True)
    assert target.read_text(encoding="utf-8") == content


def test_audit_staging_failure_preserves_existing_queue_and_creates_nothing_partial(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    ArchiveService(config).process()
    target = tmp_path / "review" / "2026-08-01.md"
    target.parent.mkdir()
    target.write_text("existing queue\n", encoding="utf-8")
    service = ReviewService(config, FixtureClassifier([_candidate()]))
    monkeypatch.setattr(service, "_audit_content", lambda *args: (_ for _ in ()).throw(OSError("audit unavailable")))
    with pytest.raises(OSError, match="audit unavailable"):
        service.review(all_dates=True)
    assert target.read_text(encoding="utf-8") == "existing queue\n"
    assert not (tmp_path / "review" / "audit").exists()

    fresh = _config(tmp_path / "fresh")
    ArchiveService(fresh).process()
    fresh_service = ReviewService(fresh, FixtureClassifier([_candidate()]))
    monkeypatch.setattr(fresh_service, "_audit_content", lambda *args: (_ for _ in ()).throw(OSError("audit unavailable")))
    with pytest.raises(OSError, match="audit unavailable"):
        fresh_service.review(all_dates=True)
    assert not (tmp_path / "fresh" / "review").exists()
