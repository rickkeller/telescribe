"""Validated, local-only application configuration."""

from pathlib import Path
import re
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .daily_archiver import telegram_archive_output_root
from .paths import safe_canonical_root


class SourceConfig(BaseModel):
    """One immutable Telegram Desktop export source."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    telegram_names: tuple[str, ...] = Field(min_length=1)
    export_path: Path
    attachment_dir: Path
    enabled: bool = True
    export_timezone: str | None = None

    @field_validator("id")
    @classmethod
    def stable_id_is_not_blank(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", value):
            raise ValueError("id must be a stable ASCII identifier without controls or whitespace")
        return value

    @field_validator("telegram_names")
    @classmethod
    def accepted_telegram_names_must_not_be_blank(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        names = tuple(name.strip() for name in value)
        if any(not name for name in names):
            raise ValueError("telegram_names must not contain blank names")
        if len(names) != len(set(names)):
            raise ValueError("telegram_names must be unique per source")
        return names


class AppConfig(BaseModel):
    """Configuration required by the MVP archive pipeline."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sources: tuple[SourceConfig, ...] = Field(min_length=1)
    archive_root: Path
    state_path: Path = Path(".hermes-telegram-archive/state.sqlite3")
    timezone: str = "America/New_York"
    archive_redaction: bool = False
    intelligence: "IntelligenceConfig" = Field(default_factory=lambda: IntelligenceConfig())

    @field_validator("timezone")
    @classmethod
    def timezone_must_exist(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @field_validator("sources")
    @classmethod
    def source_ids_must_be_unique(cls, value: tuple[SourceConfig, ...]) -> tuple[SourceConfig, ...]:
        ids = [source.id for source in value]
        if len(ids) != len(set(ids)):
            raise ValueError("source ids must be unique")
        enabled_names = [name for source in value if source.enabled for name in source.telegram_names]
        if len(enabled_names) != len(set(enabled_names)):
            raise ValueError("accepted Telegram export names must be unique across enabled sources")
        return value

    @model_validator(mode="after")
    def review_queue_must_not_overlap_archive(self) -> "AppConfig":
        archive = safe_canonical_root(self.archive_root, label="archive_root")
        output = telegram_archive_output_root(archive)
        root = self.intelligence.review_queue_root
        queue: Path | None = None
        if root is not None:
            queue = safe_canonical_root(root, label="review_queue_root")
            if not _queue_allowed(archive, output, queue):
                raise ValueError("review_queue_root and archive_root must not overlap")
        context = self.intelligence.project_context_root
        if context is not None:
            context_root = safe_canonical_root(context, label="project_context_root")
            if context_root == archive or _roots_overlap(context_root, output):
                raise ValueError("project_context_root and archive_root overlap")
            if queue is not None and _roots_overlap(queue, context_root):
                raise ValueError("review_queue_root and project_context_root must not overlap")
        return self


class ProjectConfig(BaseModel):
    """A small, explicit allowlist for review-only project proposals."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    aliases: tuple[str, ...] = ()
    context_paths: tuple[Path, ...] = ()

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", value):
            raise ValueError("project id must be a stable ASCII identifier")
        return value

    @field_validator("aliases")
    @classmethod
    def valid_aliases(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        aliases = tuple(alias.strip() for alias in value)
        if any(not alias for alias in aliases) or len(aliases) != len(set(aliases)):
            raise ValueError("project aliases must be non-blank and unique")
        return aliases


class IntelligenceConfig(BaseModel):
    """Configuration for the deliberately disabled-by-default review boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    enabled: bool = False
    review_queue_root: Path | None = None
    project_context_root: Path | None = None
    projects: tuple[ProjectConfig, ...] = ()
    max_messages: int = Field(default=20, ge=1, le=100)
    max_context_notes: int = Field(default=3, ge=0, le=10)
    max_context_chars: int = Field(default=4000, ge=0, le=12000)
    # Selection only: credentials and provider endpoints are deliberately not
    # representable in configuration.
    provider: str | None = None
    model: str | None = None

    @model_validator(mode="after")
    def enabled_requires_explicit_boundary(self) -> "IntelligenceConfig":
        if self.enabled and self.review_queue_root is None:
            raise ValueError("enabled intelligence requires review_queue_root")
        if self.enabled and self.project_context_root is None:
            raise ValueError("enabled intelligence requires project_context_root")
        if self.enabled and not self.projects:
            raise ValueError("enabled intelligence requires at least one project")
        if (self.provider is None) != (self.model is None):
            raise ValueError("direct provider requires both provider and model")
        if self.provider is not None and (self.provider != "deepseek" or self.model != "deepseek-v4-flash"):
            raise ValueError("direct provider must be deepseek / deepseek-v4-flash")
        aliases = [alias.casefold() for project in self.projects for alias in (project.id, *project.aliases)]
        if len(aliases) != len(set(aliases)):
            raise ValueError("project aliases must be unique across projects")
        return self

def _queue_allowed(archive: Path, output: Path, queue: Path) -> bool:
    if queue == archive or queue in archive.parents:
        return False
    return not (queue == output or output in queue.parents or queue in output.parents)


def _roots_overlap(first: Path, second: Path) -> bool:
    return first == second or first in second.parents or second in first.parents


def load_config(path: Path) -> AppConfig:
    """Load YAML config without resolving relative paths prematurely."""
    with path.open("r", encoding="utf-8") as config_file:
        data = yaml.safe_load(config_file) or {}
    _reject_secret_keys(data)
    return AppConfig.model_validate(data)


def _reject_secret_keys(value: Any) -> None:
    """Configuration names are intentionally incapable of carrying credentials."""
    if isinstance(value, dict):
        for key, nested in value.items():
            if re.search(r"(?:secret|password|credential|api[_-]?key|access[_-]?token)", str(key), re.I):
                raise ValueError("secrets are not permitted in YAML configuration")
            _reject_secret_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_secret_keys(nested)
