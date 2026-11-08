import json
from pathlib import Path
import os
import sqlite3
from datetime import datetime

import pytest

from telescribe.config import AppConfig, IntelligenceConfig, ProjectConfig, SourceConfig
from telescribe.intelligence import EvidencePacket, FixtureClassifier, ReviewService
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
        "model_run": "fixture-1", "possible_duplicate": False, "possible_contradiction": False, "needs_review": True,
        "temporal_status": "current", "context_conflict": False, "version_relation": None,
        "related_proposal_key": None, "related_source_identity": None}
    candidate.update(overrides)
    return candidate


def _add_archived_messages(config: AppConfig, count: int) -> None:
    """Add ordered terminal records without exercising another input/provider path."""
    with sqlite3.connect(config.state_path) as connection:
        connection.row_factory = sqlite3.Row
        original = dict(connection.execute("SELECT * FROM messages WHERE identity = ?", ("test_source:42",)).fetchone())
        columns = tuple(original)
        statement = f"INSERT INTO messages ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})"
        for message_id in range(43, 43 + count):
            record = dict(original)
            record.update({
                "identity": f"test_source:{message_id}", "source_message_id": str(message_id),
                "fingerprint": f"test-{message_id}", "plain_text": f"message {message_id}",
                "markdown_text": f"message {message_id}", "block_id": f"tg-test-source-{message_id}",
            })
            connection.execute(statement, tuple(record[column] for column in columns))


class _PerEvidenceClassifier:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def classify(self, evidence, context):
        self.calls.append(tuple(item.source_identity for item in evidence))
        return [_candidate(source_identity=item.source_identity, source_chat_id=item.source_chat_id,
                           timestamp_utc=item.timestamp_utc.isoformat(), archive_block_id=item.archive_block_id)
                for item in evidence]


def test_disabled_and_unavailable_review_fail_closed_without_mutation(tmp_path: Path) -> None:
    disabled = _config(tmp_path, enabled=False)
    with pytest.raises(ValueError, match="disabled"):
        ReviewService(disabled).review(all_dates=True)
    assert not (tmp_path / "review" / "2026-08-01.md").exists()

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
    assert "#^tg-test-source-42" in text
    assert text.count("telescribe:key:") == 1


def test_review_pages_are_stable_non_overlapping_idempotent_and_audited_separately(tmp_path: Path) -> None:
    config = _config(tmp_path)
    ArchiveService(config).process()
    _add_archived_messages(config, 3)
    classifier = _PerEvidenceClassifier()
    service = ReviewService(config, classifier)

    first = service.review(all_dates=True, cursor=0, batch_size=2)
    second = service.review(all_dates=True, cursor=first.next_cursor, batch_size=2)
    repeated = service.review(all_dates=True, cursor=0, batch_size=2)

    assert (first.selected_count, first.next_cursor, first.has_more) == (2, 2, True)
    assert (second.selected_count, second.next_cursor, second.has_more) == (2, None, False)
    assert classifier.calls == [
        ("test_source:42", "test_source:43"),
        ("test_source:44", "test_source:45"),
        ("test_source:42", "test_source:43"),
    ]
    assert repeated.accepted_count == 0
    queue = (tmp_path / "review" / "2026-08-01.md").read_text(encoding="utf-8")
    assert queue.count("telescribe:key:") == 4
    assert queue.count("# TeleScribe review queue") == 1
    assert queue.count("## Proposed tasks") == 1
    assert queue.count("## Research / references") == 1
    audits = sorted((tmp_path / "review" / "audit").glob("*.json"))
    assert len(audits) == 2
    assert {json.loads(path.read_text(encoding="utf-8"))["cursor"] for path in audits} == {0, 2}


def test_queue_rewrite_preserves_underscore_proposal_types_across_pages(tmp_path: Path) -> None:
    config = _config(tmp_path)
    ArchiveService(config).process()
    _add_archived_messages(config, 3)

    class _UnderscoreTypeClassifier:
        def classify(self, evidence, context):
            return [_candidate(source_identity=item.source_identity, source_chat_id=item.source_chat_id,
                               timestamp_utc=item.timestamp_utc.isoformat(), archive_block_id=item.archive_block_id,
                               proposal_type="status_update", project="A")
                    for item in evidence]

    service = ReviewService(config, _UnderscoreTypeClassifier())
    first = service.review(all_dates=True, cursor=0, batch_size=2)
    second = service.review(all_dates=True, cursor=first.next_cursor, batch_size=2)

    assert (first.accepted_count, second.accepted_count) == (2, 2)
    queue = (tmp_path / "review" / "2026-08-01.md").read_text(encoding="utf-8")
    # The second page's rewrite must recover and preserve the first page's
    # underscore proposal types instead of silently dropping them.
    assert queue.count("telescribe:key:") == 4
    assert queue.count("## Project updates") == 1
    assert queue.count("· status\\_update]") == 4


def test_chronological_reconstruction_is_oldest_first_and_uses_only_prior_redacted_ledger(tmp_path: Path) -> None:
    config = _config(tmp_path)
    ArchiveService(config).process()
    # Add older terminal rows after the fixture row, proving the reconstruction
    # selector does not inherit insertion order or the default review order.
    with sqlite3.connect(config.state_path) as connection:
        connection.row_factory = sqlite3.Row
        original = dict(connection.execute("SELECT * FROM messages WHERE identity = ?", ("test_source:42",)).fetchone())
        columns = tuple(original)
        statement = f"INSERT INTO messages ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})"
        for message_id, stamp in ((43, "2026-01-24T03:30:00+00:00"), (44, "2026-01-29T03:30:00+00:00")):
            row = dict(original)
            row.update({"identity": f"test_source:{message_id}", "source_message_id": str(message_id),
                        "fingerprint": f"chronological-{message_id}", "plain_text": "RAW EVIDENCE MUST NOT LEAK",
                        "markdown_text": "RAW EVIDENCE MUST NOT LEAK", "timestamp_utc": stamp,
                        "timestamp_local": stamp, "local_date": stamp[:10],
                        "archive_path": f"2026/2026-01/{stamp[:10]}.md", "block_id": f"tg-test-source-{message_id}"})
            connection.execute(statement, tuple(row[column] for column in columns))

    class Capture:
        def __init__(self) -> None: self.calls = []
        def classify(self, evidence, context):
            self.calls.append((tuple(item.source_identity for item in evidence), tuple(context)))
            result = []
            for item in evidence:
                status = "superseded" if item.source_identity.endswith(":44") else "historical"
                result.append(_candidate(source_identity=item.source_identity, source_chat_id=item.source_chat_id,
                    timestamp_utc=item.timestamp_utc.isoformat(), archive_block_id=item.archive_block_id,
                    project="reconstruction-inbox", proposed_text=f"Route {item.source_identity}",
                    temporal_status=status, version_relation="Later reviewed history may replace this" if status == "superseded" else None))
            return result

    capture = Capture()
    service = ReviewService(config, capture)
    first = service.review(all_dates=True, ordering="chronological", batch_size=1)
    second = service.review(all_dates=True, ordering="chronological", cursor=first.next_cursor, batch_size=1)

    assert capture.calls[0][0] == ("test_source:43",)
    assert capture.calls[0][1] == ()
    assert capture.calls[1][0] == ("test_source:44",)
    assert "reconstruction_ledger" in capture.calls[1][1][0]
    assert "RAW EVIDENCE MUST NOT LEAK" not in capture.calls[1][1][0]
    ledger_files = sorted((tmp_path / "review" / "reconstruction-ledger").glob("*.json"))
    ledger = "".join(path.read_text(encoding="utf-8") for path in ledger_files)
    assert "RAW EVIDENCE MUST NOT LEAK" not in ledger
    assert '"temporal_status":"historical"' in ledger
    assert '"temporal_status":"superseded"' in ledger
    assert second.ledger_context_records == 1


def test_chronological_reconstruction_dry_run_writes_no_ledger_queue_or_audit(tmp_path: Path) -> None:
    config = _config(tmp_path)
    ArchiveService(config).process()
    _add_archived_messages(config, 1)
    service = ReviewService(config, FixtureClassifier([_candidate(project="reconstruction-inbox")]))
    with pytest.raises(ValueError, match="preceding ledger page"):
        service.review(all_dates=True, ordering="chronological", cursor=1, batch_size=1)
    result = ReviewService(config, FixtureClassifier([_candidate(project="reconstruction-inbox")])).review(
        all_dates=True, ordering="chronological", dry_run=True)
    assert result.ordering == "chronological"
    assert not (tmp_path / "review").exists()


def test_chronological_date_range_is_inclusive_ny_local_and_bounds_cursor_pages(tmp_path: Path) -> None:
    config = _config(tmp_path)
    ArchiveService(config).process()
    with sqlite3.connect(config.state_path) as connection:
        connection.row_factory = sqlite3.Row
        original = dict(connection.execute("SELECT * FROM messages WHERE identity = ?", ("test_source:42",)).fetchone())
        columns = tuple(original)
        statement = f"INSERT INTO messages ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})"
        for message_id, stamp, local_day in (
            (43, "2026-01-24T05:00:00+00:00", "2026-01-24"),
            (44, "2026-01-30T05:00:00+00:00", "2026-01-30"),
            (45, "2026-02-04T05:00:00+00:00", "2026-02-04"),
        ):
            row = dict(original)
            row.update({"identity": f"test_source:{message_id}", "source_message_id": str(message_id),
                        "fingerprint": f"range-{message_id}", "plain_text": f"message {message_id}",
                        "markdown_text": f"message {message_id}", "timestamp_utc": stamp,
                        "timestamp_local": stamp, "local_date": local_day,
                        "archive_path": f"2026/2026-01/{local_day}.md", "block_id": f"tg-test-source-{message_id}"})
            connection.execute(statement, tuple(row[column] for column in columns))

    classifier = _PerEvidenceClassifier()
    service = ReviewService(config, classifier)
    first = service.review(start_date="2026-01-24", end_date="2026-01-30", ordering="chronological", batch_size=1)
    second = service.review(start_date="2026-01-24", end_date="2026-01-30", ordering="chronological",
                            cursor=first.next_cursor, batch_size=1)

    assert classifier.calls == [("test_source:43",), ("test_source:44",)]
    assert first.next_cursor == 1 and second.next_cursor is None
    audit = json.loads(next((tmp_path / "review" / "audit").glob("*.json")).read_text(encoding="utf-8"))
    assert audit["date_range"] == {"start_date": "2026-01-24", "end_date": "2026-01-30"}
    queue = (tmp_path / "review" / "2026-01-24--2026-01-30.md").read_text(encoding="utf-8")
    assert "test_source:45" not in queue


def test_chronological_date_tranches_receive_only_strictly_prior_ledger_context(tmp_path: Path) -> None:
    config = _config(tmp_path)
    ArchiveService(config).process()
    with sqlite3.connect(config.state_path) as connection:
        connection.row_factory = sqlite3.Row
        original = dict(connection.execute("SELECT * FROM messages WHERE identity = ?", ("test_source:42",)).fetchone())
        columns = tuple(original)
        statement = f"INSERT INTO messages ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})"
        for message_id, stamp in ((43, "2026-01-24T05:00:00+00:00"),
                                  (44, "2026-01-26T05:00:00+00:00"),
                                  (46, "2026-01-26T06:00:00+00:00"),
                                  (45, "2026-01-27T05:00:00+00:00")):
            row = dict(original)
            row.update({"identity": f"test_source:{message_id}", "source_message_id": str(message_id),
                        "fingerprint": f"cross-tranche-{message_id}", "plain_text": f"RAW EVIDENCE {message_id}",
                        "markdown_text": f"RAW EVIDENCE {message_id}", "timestamp_utc": stamp,
                        "timestamp_local": stamp, "local_date": stamp[:10],
                        "archive_path": f"2026/2026-01/{stamp[:10]}.md", "block_id": f"tg-test-source-{message_id}"})
            connection.execute(statement, tuple(row[column] for column in columns))

    class Capture:
        def __init__(self) -> None:
            self.calls: list[tuple[tuple[str, ...], tuple[str, ...]]] = []

        def classify(self, evidence, context):
            self.calls.append((tuple(item.source_identity for item in evidence), tuple(context)))
            return [_candidate(source_identity=item.source_identity, source_chat_id=item.source_chat_id,
                               timestamp_utc=item.timestamp_utc.isoformat(), archive_block_id=item.archive_block_id,
                               proposed_text=f"Route {item.source_identity}") for item in evidence]

    capture = Capture()
    service = ReviewService(config, capture)
    service.review(start_date="2026-01-24", end_date="2026-01-24", ordering="chronological")
    # This later tranche exists before the Jan. 26 run, so it proves that context
    # selection is temporal rather than simply whichever ledger files are present.
    service.review(start_date="2026-01-27", end_date="2026-01-27", ordering="chronological")
    # A ledger page from a different range is not a page-resume predecessor.
    with pytest.raises(ValueError, match="preceding ledger page"):
        service.review(start_date="2026-01-26", end_date="2026-01-26", ordering="chronological",
                       cursor=1, batch_size=1)
    later = service.review(start_date="2026-01-26", end_date="2026-01-26", ordering="chronological", cursor=0)

    assert later.ledger_context_records == 1
    later_context = capture.calls[-1][1]
    assert len(later_context) == 1
    assert "test_source:43" in later_context[0]
    assert "test_source:45" not in later_context[0]
    assert "RAW EVIDENCE" not in later_context[0]

    # An overlapping re-run starts at the same timestamp as its old ledger page,
    # so that equal boundary must not leak back as predecessor context.
    overlap = service.review(start_date="2026-01-24", end_date="2026-01-26", ordering="chronological", cursor=0)
    assert overlap.ledger_context_records == 0
    assert capture.calls[-1][1] == ()

    # A schema-valid ledger with an equal range_end is likewise excluded.
    ledger_root = tmp_path / "review" / "reconstruction-ledger"
    documents = [json.loads(path.read_text(encoding="utf-8")) for path in ledger_root.glob("*.json")]
    equal_document = next(document for document in documents
                          if document["range_start"] == "2026-01-26T05:00:00+00:00")
    equal_document["range_start"] = "2026-01-26T05:00:00+00:00"
    equal_document["range_end"] = "2026-01-26T05:00:00+00:00"
    equal_document["start_date"] = "odd-start"
    equal_document["end_date"] = "odd-end"
    equal_document["records"] = [record for record in equal_document["records"]
                                 if record["timestamp_utc"] == "2026-01-26T05:00:00+00:00"]
    (ledger_root / "equal-boundary.json").write_text(json.dumps(equal_document), encoding="utf-8")
    equal_boundary = service.review(start_date="2026-01-26", end_date="2026-01-26", ordering="chronological", cursor=0)
    assert equal_boundary.ledger_context_records == 1
    assert "test_source:43" in capture.calls[-1][1][0]


def test_chronological_ledger_context_stays_valid_json_and_keeps_newest_when_bounded(tmp_path: Path) -> None:
    # A tiny bounded-context budget forces trimming; the payload must remain
    # parseable JSON containing the most recent validated prior records, not a
    # malformed tail fragment of the oldest ones.
    config = _config(tmp_path).model_copy(update={
        "intelligence": _config(tmp_path).intelligence.model_copy(update={"max_context_chars": 500})})
    ArchiveService(config).process()
    with sqlite3.connect(config.state_path) as connection:
        connection.row_factory = sqlite3.Row
        original = dict(connection.execute("SELECT * FROM messages WHERE identity = ?", ("test_source:42",)).fetchone())
        columns = tuple(original)
        statement = f"INSERT INTO messages ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})"
        for message_id, stamp in ((43, "2026-01-24T05:00:00+00:00"),
                                  (44, "2026-01-24T06:00:00+00:00"),
                                  (45, "2026-01-25T05:00:00+00:00"),
                                  (46, "2026-01-25T06:00:00+00:00"),
                                  (47, "2026-01-26T05:00:00+00:00")):
            row = dict(original)
            row.update({"identity": f"test_source:{message_id}", "source_message_id": str(message_id),
                        "fingerprint": f"bounded-{message_id}", "plain_text": f"RAW EVIDENCE {message_id}",
                        "markdown_text": f"RAW EVIDENCE {message_id}", "timestamp_utc": stamp,
                        "timestamp_local": stamp, "local_date": stamp[:10],
                        "archive_path": f"2026/2026-01/{stamp[:10]}.md", "block_id": f"tg-test-source-{message_id}"})
            connection.execute(statement, tuple(row[column] for column in columns))

    class Capture:
        def __init__(self) -> None:
            self.calls: list[tuple[tuple[str, ...], tuple[str, ...]]] = []

        def classify(self, evidence, context):
            self.calls.append((tuple(item.source_identity for item in evidence), tuple(context)))
            return [_candidate(source_identity=item.source_identity, source_chat_id=item.source_chat_id,
                               timestamp_utc=item.timestamp_utc.isoformat(), archive_block_id=item.archive_block_id,
                               proposed_text=f"Route {item.source_identity}") for item in evidence]

    capture = Capture()
    service = ReviewService(config, capture)
    service.review(start_date="2026-01-24", end_date="2026-01-25", ordering="chronological")
    later = service.review(start_date="2026-01-26", end_date="2026-01-26", ordering="chronological")
    context = capture.calls[-1][1]

    assert later.ledger_context_records > 0
    assert len(context) == 1
    payload = json.loads(context[0])
    assert set(payload) == {"reconstruction_ledger"}
    assert len(context[0]) <= 500
    rendered = json.dumps(payload, sort_keys=True)
    # The most recent prior record survives; the oldest is trimmed away.
    assert "test_source:46" in rendered
    assert "test_source:43" not in rendered


def test_chronological_ledger_entries_render_once_in_primary_sections_across_large_page(tmp_path: Path) -> None:
    config = _config(tmp_path)
    ArchiveService(config).process()
    _add_archived_messages(config, 16)
    store = sqlite3.connect(config.state_path)
    rows = store.execute("SELECT identity, source_message_id, timestamp_utc, block_id FROM messages ORDER BY source_message_id").fetchall()
    store.close()
    types = ["status_update", "reference", "historical", "uncertain", "task", "idea", "decision", "requirement",
             "risk", "problem", "bug", "research", "reference", "project_update", "unclassified", "task", "idea"]

    candidates = []
    for index, (identity, _, timestamp, block_id) in enumerate(rows[:17]):
        temporal = "historical" if types[index] == "historical" else ("uncertain" if types[index] == "uncertain" else "current")
        proposal_type = "task" if types[index] in {"historical", "uncertain"} else types[index]
        candidates.append(_candidate(source_identity=identity, timestamp_utc=timestamp, archive_block_id=block_id,
                                     proposal_type=proposal_type, proposed_text=f"Visible proposal {index}",
                                     temporal_status=temporal, context_conflict=temporal == "uncertain"))
    result = ReviewService(config, FixtureClassifier(candidates)).review(all_dates=True, ordering="chronological", batch_size=20)
    queue = (tmp_path / "review" / "2026-08-01.md").read_text(encoding="utf-8")
    ledger = next((tmp_path / "review" / "reconstruction-ledger").glob("*.json"))
    records = json.loads(ledger.read_text(encoding="utf-8"))["records"]

    assert result.accepted_count == len(records) == 17
    assert queue.count("<!-- telescribe:key:") == len(records)
    assert all(queue.count(f"telescribe:key:{record['proposal_key']}") == 1 for record in records)
    assert "Visible proposal 1" in queue.split("## Research / references", 1)[1].split("## Decisions", 1)[0]
    assert "Visible proposal 2" in queue.split("## Historical versions / reconstruction", 1)[1].split("##", 1)[0]
    assert "Visible proposal 3" in queue.split("## Uncertain / context conflicts", 1)[1].split("##", 1)[0]


def test_invalid_candidate_audit_uses_only_aggregate_schema_categories(tmp_path: Path) -> None:
    config = _config(tmp_path)
    ArchiveService(config).process()
    invalid = _candidate(proposed_text="DO NOT PERSIST THIS CANDIDATE", unexpected="secret")
    ReviewService(config, FixtureClassifier([invalid])).review(all_dates=True)
    audit = json.loads(next((tmp_path / "review" / "audit").glob("*.json")).read_text(encoding="utf-8"))
    assert audit["reason_ids"] == ["unexpected_fields"]
    assert "DO NOT PERSIST THIS CANDIDATE" not in json.dumps(audit)


def test_renderer_places_research_and_references_once_in_their_own_section(tmp_path: Path) -> None:
    config = _config(tmp_path)
    ArchiveService(config).process()
    candidates = [_candidate(proposal_type="research", proposed_text="Research the migration"),
                  _candidate(proposal_type="reference", proposed_text="Keep the architecture reference")]
    ReviewService(config, FixtureClassifier(candidates)).review(all_dates=True)
    queue = (tmp_path / "review" / "2026-08-01.md").read_text(encoding="utf-8")
    section = queue.split("## Research / references\n", 1)[1].split("## Decisions", 1)[0]
    assert "Research the migration" in section and "Keep the architecture reference" in section
    ideas = queue.split("## Ideas\n", 1)[1].split("## Research / references", 1)[0]
    assert "Research the migration" not in ideas and "architecture reference" not in ideas


def test_duplicate_relation_requires_a_concrete_retained_primary_reference(tmp_path: Path) -> None:
    config = _config(tmp_path)
    ArchiveService(config).process()
    primary = _candidate(proposed_text="Retained primary proposal")
    ReviewService(config, FixtureClassifier([primary])).review(all_dates=True)
    # The same deterministic formula used by the service gives the model-visible primary key.
    import hashlib
    primary_key = hashlib.sha256("test_source:42\0task\0alpha\0Retained primary proposal".encode()).hexdigest()
    related = _candidate(proposed_text="Possible duplicate candidate", possible_duplicate=True,
                         related_proposal_key=primary_key)
    ReviewService(config, FixtureClassifier([related])).review(all_dates=True)
    queue = (tmp_path / "review" / "2026-08-01.md").read_text(encoding="utf-8")
    relation = queue.split("## Possible duplicates / contradictions\n", 1)[1]
    assert primary_key[:12] in relation
    assert "Retained primary proposal" not in relation

    no_match = _candidate(proposed_text="Unmatched flagged candidate", possible_duplicate=True,
                          related_proposal_key="a" * 64)
    ReviewService(config, FixtureClassifier([no_match])).review(all_dates=True)
    queue = (tmp_path / "review" / "2026-08-01.md").read_text(encoding="utf-8")
    relation = queue.split("## Possible duplicates / contradictions\n", 1)[1]
    assert "Unmatched flagged candidate" not in relation


def test_historical_and_superseded_claims_are_retained_source_linked_and_routed_for_reconstruction(tmp_path: Path) -> None:
    config = _config(tmp_path)
    ArchiveService(config).process()
    historical = _candidate(temporal_status="historical", proposed_text="Implement the old requirement")
    superseded = _candidate(temporal_status="superseded", proposed_text="Use the former rollout plan",
                            version_relation="Replaced by the later staged rollout; confirm during review.")
    result = ReviewService(config, FixtureClassifier([historical, superseded])).review(all_dates=True)
    assert (result.accepted_count, result.rejected_count) == (2, 0)

    evidence = (EvidencePacket("test_source:42", "test_source", datetime.fromisoformat("2026-08-01T03:30:00+00:00"),
                               "tg-test-source-42", "2026/2026-08/2026-08-01.md", "source"),)
    proposals, rejected_count = ReviewService(config)._validate([historical, superseded], evidence)
    assert rejected_count == 0
    assert [proposal.temporal_status for proposal in proposals] == ["historical", "superseded"]
    assert all(proposal.needs_review for proposal in proposals)

    queue = (tmp_path / "review" / "2026-08-01.md").read_text(encoding="utf-8")
    reconstruction = queue.split("## Historical versions / reconstruction\n", 1)[1].split("##", 1)[0]
    assert "Dated-version semantics" in queue and "`as_of`" in queue
    assert "Implement the old requirement" in reconstruction
    assert "Use the former rollout plan" in reconstruction
    assert "Replaced by the later staged rollout; confirm during review." in reconstruction
    assert reconstruction.count("[source](") == 2
    assert "Implement the old requirement" not in queue.split("## Proposed tasks\n", 1)[1].split("##", 1)[0]


def test_current_proposals_remain_in_their_base_section(tmp_path: Path) -> None:
    config = _config(tmp_path)
    ArchiveService(config).process()
    result = ReviewService(config, FixtureClassifier([_candidate(temporal_status="current")])).review(all_dates=True)
    assert result.accepted_count == 1
    queue = (tmp_path / "review" / "2026-08-01.md").read_text(encoding="utf-8")
    tasks = queue.split("## Proposed tasks\n", 1)[1].split("##", 1)[0]
    reconstruction = queue.split("## Historical versions / reconstruction\n", 1)[1].split("##", 1)[0]
    assert "Follow up on the request" in tasks
    assert "Follow up on the request" not in reconstruction


def test_supersession_requires_model_review_mark_and_relationship_context(tmp_path: Path) -> None:
    config = _config(tmp_path)
    ArchiveService(config).process()
    unreviewed = _candidate(temporal_status="superseded", needs_review=False,
                            version_relation="Replaced by a later reviewed version.")
    missing_context = _candidate(temporal_status="superseded", proposed_text="Another older version")
    result = ReviewService(config, FixtureClassifier([unreviewed, missing_context])).review(all_dates=True)
    assert (result.accepted_count, result.rejected_count) == (0, 2)
    assert not (tmp_path / "review" / "2026-08-01.md").exists()


def test_temporal_schema_is_closed_and_uncertainty_forces_review(tmp_path: Path) -> None:
    config = _config(tmp_path)
    ArchiveService(config).process()
    invalid = _candidate(temporal_status="future")
    result = ReviewService(config, FixtureClassifier([invalid])).review(all_dates=True)
    assert result.rejected_count == 1
    missing = _candidate()
    missing.pop("context_conflict")
    assert ReviewService(config, FixtureClassifier([missing])).review(all_dates=True).rejected_count == 1
    # Existing valid current candidates retain their backward-compatible acceptance.
    assert ReviewService(config, FixtureClassifier([_candidate()])).review(all_dates=True).accepted_count == 1

    evidence = (EvidencePacket("test_source:42", "test_source", datetime.fromisoformat("2026-08-01T03:30:00+00:00"),
                               "tg-test-source-42", "2026/2026-08/2026-08-01.md", "source"),)
    conflicted = _candidate(needs_review=False, context_conflict=True)
    proposals, rejected_count = ReviewService(config)._validate([conflicted], evidence)
    assert rejected_count == 0 and proposals[0].needs_review


@pytest.mark.parametrize("cursor", [-1, True, "0"])
def test_review_rejects_invalid_page_cursor_without_mutation(tmp_path: Path, cursor: object) -> None:
    config = _config(tmp_path)
    ArchiveService(config).process()

    with pytest.raises(ValueError, match="cursor must be a nonnegative integer"):
        ReviewService(config, FixtureClassifier([_candidate()])).review(all_dates=True, cursor=cursor)  # type: ignore[arg-type]

    assert not (tmp_path / "review").exists()


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
    output = archive / "01 Daily Archives" / "Telegram"
    for review in (archive, output, output / "review"):
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


def test_runtime_review_writes_to_isolated_shadow_sibling_under_vault_base(tmp_path: Path) -> None:
    config = _config(tmp_path)
    vault = config.archive_root
    shadow_queue = vault / "20-Review Queues" / "TeleScribe Shadow"
    shadow_context = vault / "10-Projects" / "TeleScribe Shadow Context"
    shadow_context.mkdir(parents=True)
    (shadow_context / "context.md").write_text("ordinary project context", encoding="utf-8")
    shadow = config.model_copy(update={"intelligence": config.intelligence.model_copy(update={
        "review_queue_root": shadow_queue,
        "project_context_root": shadow_context,
        "projects": [ProjectConfig(id="alpha", aliases=["A"], context_paths=[Path("context.md")])],
    })})
    ArchiveService(shadow).process()

    result = ReviewService(shadow, FixtureClassifier([_candidate()])).review(all_dates=True)

    assert result.accepted_count == 1
    assert (shadow_queue / "2026-08-01.md").is_file()


def test_archive_rejects_symlinked_daily_archives_before_writing(tmp_path: Path) -> None:
    config = _config(tmp_path)
    review = config.intelligence.review_queue_root
    assert review is not None
    review.mkdir(parents=True)
    config.archive_root.mkdir()
    daily_archives = config.archive_root / "01 Daily Archives"
    daily_archives.symlink_to(review, target_is_directory=True)

    with pytest.raises(ValueError, match="unsafe archive output symlink"):
        ArchiveService(config).process()

    assert daily_archives.is_symlink()
    assert not list(review.iterdir())
    assert not config.state_path.exists()


def test_runtime_review_rejects_symlinked_telegram_output_before_classifier_dispatch(tmp_path: Path) -> None:
    config = _config(tmp_path)
    context = config.intelligence.project_context_root
    assert context is not None
    context.mkdir(parents=True)
    daily_archives = config.archive_root / "01 Daily Archives"
    daily_archives.mkdir(parents=True)
    telegram = daily_archives / "Telegram"
    telegram.symlink_to(context, target_is_directory=True)
    calls: list[object] = []

    class NoDispatchClassifier:
        def classify(self, evidence: object, context: object) -> list[object]:
            calls.append((evidence, context))
            return []

    with pytest.raises(ValueError, match="unsafe archive output symlink"):
        ReviewService(config, NoDispatchClassifier()).review(all_dates=True)

    assert telegram.is_symlink()
    assert calls == []
    assert not list(context.iterdir())


def test_runtime_review_rejects_nested_archive_month_symlink_before_context_or_classifier(tmp_path: Path) -> None:
    config = _config(tmp_path)
    ArchiveService(config).process()
    context = config.intelligence.project_context_root
    assert context is not None
    context.mkdir()
    month = config.archive_root / "01 Daily Archives" / "Telegram" / "2026" / "2026-99"
    month.symlink_to(context, target_is_directory=True)
    calls: list[object] = []

    class NoDispatchClassifier:
        def classify(self, evidence: object, context: object) -> list[object]:
            calls.append((evidence, context))
            return []

    with pytest.raises(ValueError, match="unsafe archive tree symlink"):
        ReviewService(config, NoDispatchClassifier()).review(all_dates=True)

    assert month.is_symlink()
    assert calls == []
    assert not (tmp_path / "review").exists()
    assert not (tmp_path / "review" / "audit").exists()


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
    secret = "api_key=sk-abcDEF1234567890 email=me@example.com phone=212-555-1212 account=123456789"
    result = ReviewService(config, FixtureClassifier([_candidate(rationale=secret, proposed_text="Ordinary " + secret,
        model_run="token=reallysecret")])).review(all_dates=True)
    persisted = "\n".join(path.read_text(encoding="utf-8") for path in (tmp_path / "review").rglob("*") if path.is_file())
    assert result.accepted_count == 1
    assert "Ordinary" in persisted
    for raw in ("sk-abcDEF1234567890", "me@example.com", "212-555-1212", "123456789", "reallysecret"):
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
        context_root = config.archive_root / "01 Daily Archives" / "Telegram" / "context"
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
