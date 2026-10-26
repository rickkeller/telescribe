import json
import inspect
from pathlib import Path
import sqlite3

import pytest

from telescribe.config import AppConfig, IntelligenceConfig, ProjectConfig, SourceConfig
from telescribe.intelligence import (
    EvidencePacket, ModelTransportError, ReviewService, _DirectDeepSeekClassifier, _RetrievedContext,
    _create_direct_deepseek_classifier_for_test,
    create_direct_deepseek_classifier,
)
from telescribe.pipeline import ArchiveService


class FakeTransport:
    def __init__(self, *, status: int = 200, body: bytes = b"") -> None:
        self.status, self.body, self.calls = status, body, []

    def __call__(self, url, headers, body, timeout):
        self.calls.append((url, headers, body, timeout))
        return self.status, self.body


def _config(tmp_path: Path, *, enabled: bool = True, provider: str | None = "deepseek", model: str | None = "deepseek-v4-flash") -> AppConfig:
    fixture = Path(__file__).parent / "fixtures" / "string_text_result.json"
    return AppConfig(sources=[SourceConfig(id="test_source", telegram_names=["Example Bot"],
        export_path=fixture, attachment_dir=tmp_path / "files")], archive_root=tmp_path / "vault",
        state_path=tmp_path / "state.sqlite3", intelligence=IntelligenceConfig(enabled=enabled,
        review_queue_root=tmp_path / "review" if enabled else None, project_context_root=tmp_path / "context" if enabled else None,
        projects=[ProjectConfig(id="alpha") ] if enabled else [], provider=provider if enabled else None,
        model=model if enabled else None))


def _evidence() -> tuple[EvidencePacket, ...]:
    return (EvidencePacket("test_source:42", "test_source", __import__("datetime").datetime(2026, 8, 1, 3, 30, tzinfo=__import__("datetime").UTC),
        "tg-test-source-42", "01 Daily Archives/Telegram/2026/2026-08/2026-08-01.md", "Follow up on the ordinary project plan."),)


def test_direct_provider_factory_requires_dispatch_and_credential_without_transport_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    monkeypatch.delenv("TELESCRIBE_DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ValueError, match="credential"):
        create_direct_deepseek_classifier(config, dispatch=True)
    with pytest.raises(ValueError, match="dispatch"):
        create_direct_deepseek_classifier(config, dispatch=False)


def test_public_factory_accepts_only_approved_config_and_explicit_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELESCRIBE_DEEPSEEK_API_KEY", "test-key")
    signature = inspect.signature(create_direct_deepseek_classifier)
    assert tuple(signature.parameters) == ("config", "dispatch")
    disabled = _config(tmp_path / "disabled", enabled=False)
    wrong = _config(tmp_path / "wrong").model_copy(update={"intelligence": _config(tmp_path / "wrong").intelligence.model_copy(update={"provider": "other", "model": "other"})})
    for config, error in ((disabled, "enabled"), (wrong, "approved")):
        with pytest.raises(ValueError, match=error):
            create_direct_deepseek_classifier(config, dispatch=True)
    cli = (Path(__file__).parents[1] / "src" / "telescribe" / "cli.py").read_text(encoding="utf-8")
    assert "transport=" not in cli and "endpoint=" not in cli and "base_url=" not in cli


def test_direct_provider_uses_fixed_url_model_and_minimal_redacted_payload(tmp_path: Path) -> None:
    proposal = {"source_identity": "test_source:42", "source_chat_id": "test_source", "timestamp_utc": "2026-08-01T03:30:00+00:00",
        "archive_block_id": "tg-test-source-42", "project": "alpha", "proposal_type": "task", "confidence": 0.8,
        "rationale": "Follow up", "proposed_text": "Follow up", "model_run": "deepseek", "possible_duplicate": False,
        "possible_contradiction": False, "needs_review": True}
    transport = FakeTransport(body=json.dumps({"choices": [{"message": {"content": json.dumps({"proposals": [proposal]})}}]}).encode())
    classifier = _create_direct_deepseek_classifier_for_test(_config(tmp_path), api_key="example", transport=transport)
    assert list(classifier.classify(_evidence(), (_RetrievedContext("project plan", Path("brief.md")),))) == [proposal]
    url, headers, raw, timeout = transport.calls[0]
    payload = json.loads(raw)
    assert url == "https://api.deepseek.com/v1/chat/completions"
    assert payload["model"] == "deepseek-v4-flash" and headers["Content-Type"] == "application/json"
    assert timeout > 0 and "Authorization" in headers
    user = json.loads(payload["messages"][1]["content"])
    assert user["request_version"] == "telescribe-review-v1"
    assert set(user) == {"request_version", "task", "project_ids", "proposal_types", "evidence", "project_context"}
    assert "archive_path" not in json.dumps(user)
    assert "project plan" in user["project_context"]


@pytest.mark.parametrize("status, body", [
    (500, b"provider failure"), (200, b"{"),
    (200, json.dumps({"choices": [{"message": {"content": "[]"}}]}).encode()),
    (200, json.dumps({"choices": [{"message": {"content": json.dumps({"proposals": [], "extra": 1})}}]}).encode()),
    (200, b"x" * (_DirectDeepSeekClassifier.MAX_RESPONSE_BYTES + 1)),
])
def test_direct_provider_rejects_bad_responses_without_exposing_body(tmp_path: Path, status: int, body: bytes) -> None:
    classifier = _create_direct_deepseek_classifier_for_test(_config(tmp_path), api_key="example", transport=FakeTransport(status=status, body=body))
    with pytest.raises(ModelTransportError):
        list(classifier.classify(_evidence(), ()))


def test_production_adapter_has_no_hermes_subprocess_or_telegram_dependency() -> None:
    implementation = (Path(__file__).parents[1] / "src" / "telescribe" / "intelligence.py").read_text(encoding="utf-8")
    for forbidden in ("subprocess", "telegram_parser", "Hermes CLI", "hermes_cli"):
        assert forbidden not in implementation


def test_direct_response_flows_through_existing_safe_queue_boundary(tmp_path: Path) -> None:
    config = _config(tmp_path)
    ArchiveService(config).process()
    proposal = {"source_identity": "test_source:42", "source_chat_id": "test_source", "timestamp_utc": "2026-08-01T03:30:00+00:00",
        "archive_block_id": "tg-test-source-42", "project": "alpha", "proposal_type": "task", "confidence": 0.8,
        "rationale": "Follow up", "proposed_text": "Follow up", "model_run": "deepseek", "possible_duplicate": False,
        "possible_contradiction": False, "needs_review": True}
    body = json.dumps({"choices": [{"message": {"content": json.dumps({"proposals": [proposal]})}}]}).encode()
    result = ReviewService(config, _create_direct_deepseek_classifier_for_test(config, api_key="example", transport=FakeTransport(body=body))).review(all_dates=True)
    assert result.accepted_count == 1
    assert "Follow up" in (tmp_path / "review" / "2026-08-01.md").read_text(encoding="utf-8")
    assert list((tmp_path / "review" / "audit").glob("*.json"))


@pytest.mark.parametrize("failure", [TimeoutError(), OSError("no route")])
def test_direct_failure_mutates_no_queue_or_audit(tmp_path: Path, failure: Exception) -> None:
    config = _config(tmp_path)
    ArchiveService(config).process()
    def raising_transport(url, headers, body, timeout):
        raise failure
    with pytest.raises(ModelTransportError):
        ReviewService(config, _create_direct_deepseek_classifier_for_test(config, api_key="example", transport=raising_transport)).review(all_dates=True)
    assert not (tmp_path / "review").exists()


@pytest.mark.parametrize("name", [".env", "settings.yaml", "run.log", "state.sqlite3", "result.json", "audit/run.md"])
def test_remote_context_file_classes_fail_closed_before_transport_or_writes(tmp_path: Path, name: str) -> None:
    config = _config(tmp_path, )
    root = config.intelligence.project_context_root
    assert root is not None
    path = root / name
    path.parent.mkdir(parents=True)
    path.write_text("ordinary-looking content", encoding="utf-8")
    config = config.model_copy(update={"intelligence": config.intelligence.model_copy(update={
        "projects": [ProjectConfig(id="alpha", context_paths=[Path(name)])],
    })})
    ArchiveService(config).process()
    transport = FakeTransport()
    classifier = _create_direct_deepseek_classifier_for_test(config, api_key="example", transport=transport)
    with pytest.raises(ModelTransportError, match="payload rejected"):
        ReviewService(config, classifier).review(all_dates=True)
    assert not transport.calls
    assert not (tmp_path / "review").exists()


@pytest.mark.parametrize("field, value", [
    ("context", "read /private/project/secret.md"),
    ("context", "TELESCRIBE_DEEPSEEK_API_KEY=still-forbidden"),
    ("context", "state.sqlite3 and raw export result.json"),
    ("evidence", "see /private/project/secret.md"),
    ("evidence", "see /secret"),
])
def test_remote_adversarial_outbound_text_fails_closed_before_transport_or_writes(tmp_path: Path, field: str, value: str) -> None:
    context_paths = (Path("brief.md"),) if field == "context" else ()
    config = _config(tmp_path, )
    config = config.model_copy(update={"intelligence": config.intelligence.model_copy(update={
        "projects": [ProjectConfig(id="alpha", context_paths=context_paths)],
    })})
    if field == "context":
        root = config.intelligence.project_context_root
        assert root is not None
        root.mkdir(parents=True)
        (root / "brief.md").write_text(value, encoding="utf-8")
    ArchiveService(config).process()
    if field == "evidence":
        with sqlite3.connect(config.state_path) as connection:
            connection.execute("UPDATE messages SET plain_text = ?", (value,))
    transport = FakeTransport()
    classifier = _create_direct_deepseek_classifier_for_test(config, api_key="example", transport=transport)
    with pytest.raises(ModelTransportError, match="payload rejected"):
        ReviewService(config, classifier).review(all_dates=True)
    assert not transport.calls
    assert not (tmp_path / "review").exists()
