# TeleScribe

TeleScribe is a local-first, deterministic Telegram Desktop JSON daily archiver for Obsidian-compatible Markdown. This first MVP slice supports configuration, JSON parsing/normalization, stable message identities, a SQLite checkpoint index, local `America/New_York` daily archives, dry-run, and a rollback-capable filesystem/state commit boundary.

It does not send network requests by default, automatically classify or extract content, copy attachments, watch exports, write canonical project notes, or modify Telegram exports.

Phase 2B retains the disabled-by-default, review-only boundary and adds one direct opt-in adapter: `deepseek` / `deepseek-v4-flash`. It is used only with enabled matching configuration, the environment-only `TELESCRIBE_DEEPSEEK_API_KEY`, and an explicit `review --dispatch`; no credential, endpoint, transport, timeout, project list, or arbitrary provider setting can appear in YAML or the public factory. The factory constructs the fixed stdlib HTTPS destination internally. The model receives only bounded/redacted indexed evidence plus selectively retrieved approved project context. This retrieval-only context remains limited to regular, non-symlink files under the separate explicit `intelligence.project_context_root`, structurally disjoint from `archive_root` and excluding every configured Telegram export (enabled or disabled, including hardlinks). For remote dispatch, context is narrower still: only intentional `.md` and `.txt` project notes are allowed. Dotfiles, `.env`, config/settings, logs, SQLite/state/WAL/SHM databases, audit/run material, raw exports/results (including JSON), and all other file types are rejected rather than transformed. After redaction, any evidence or context containing an absolute path, secret assignment, operational-storage term, or raw-export indicator also rejects the request before transport. It never receives Hermes memory or session transcripts. The adapter cannot edit canonical project notes, tasks, ideas, decisions, archives, indexes, or checkpoints; successful review may write only its proposal queue and redacted audit.

## Requirements and setup

Python 3.12 and [uv](https://docs.astral.sh/uv/) are required.

```bash
uv sync --python 3.12
uv run pytest
```

Use a YAML configuration such as:

```yaml
archive_root: /safe/output/telegram-archive
state_path: /safe/output/.hermes-telegram-archive/state.sqlite3
timezone: America/New_York
sources:
  - id: example_bot
    telegram_names: [Example Bot]
    export_path: /exports/example_bot/result.json
    attachment_dir: /exports/example_bot/files
    enabled: true
intelligence:
  enabled: true
  provider: deepseek
  model: deepseek-v4-flash
  review_queue_root: /safe/output/review-queue
  project_context_root: /safe/project-context
  projects:
    - id: example
      context_paths: [brief.md]
```

Run a mutation-free plan first, then archive:

```bash
uv run telescribe process --config telescribe.yaml --dry-run
uv run telescribe validate --config telescribe.yaml
uv run telescribe process --config telescribe.yaml
uv run telescribe reprocess --config telescribe.yaml --date 2026-07-31
uv run telescribe reprocess --config telescribe.yaml --source example_bot
uv run telescribe reprocess --config telescribe.yaml --all
uv run telescribe status --config telescribe.yaml
```

Direct review dispatch is deliberately separate from ordinary review and dry runs. Set the credential outside YAML, then use this preflight (it checks only whether the variable exists, never prints its value) before a live dispatch:

```bash
if [ -z "${TELESCRIBE_DEEPSEEK_API_KEY+x}" ]; then
  echo "TELESCRIBE_DEEPSEEK_API_KEY is not set" >&2
  exit 1
fi
uv run telescribe review --config telescribe.yaml --all --dispatch
```

The SQLite index is the checkpoint. Reprocessing an unchanged export yields zero new messages and no note rewrite. A newly observed late message causes its local calendar-day note to be rebuilt chronologically from the current export. Dry-run reports intended changed daily-note paths while making no filesystem or SQLite state changes. During a real run, file replacement is rolled back if the single SQLite index-and-terminal-status transaction fails.

Each export name must match one of its source's `telegram_names`, preventing a configured source from accidentally processing the wrong chat. Telegram captions are normalized when the normal text field is empty.
Configured accepted Telegram names are trimmed, non-blank, unique within each source, and cannot overlap across enabled sources. Stable source IDs reject whitespace and control characters.
Configuration models expose immutable source and accepted-name collections, so a validated run configuration cannot be mutated in place.
Unsupported Telegram record types are retained as empty archive records with their original metadata, and simultaneous numeric Telegram IDs are ordered numerically.
Only numeric Telegram IDs are used as primary identities; malformed or non-numeric IDs use the deterministic SHA-256 fingerprint fallback.
Forward origins expressed as Telegram metadata objects are normalized to their display name while the complete original object remains retained in the raw source metadata.
Every archive record includes deterministic, recursively redacted original-source metadata JSON, including unsupported Telegram record details. Before any normalized record, SQLite row, Markdown archive, or audit JSON is persisted, TeleScribe removes complete Bearer credentials (including `Authorization: Bearer ...`), API keys/tokens/secrets/passwords, private-key blocks, email addresses, phone-like values, and financial-account-like values. Ordinary prose is retained. Message text and user-controlled metadata are rendered inert, so Telegram content cannot inject Markdown, links, HTML, block IDs, or managed markers. Raw Telegram JSON exports remain unchanged.
`validate` parses every enabled export and checks that archive/state output parents are writable, without creating archive files or state. Existing SQLite indexes are opened read-only for `--dry-run` and `status`.
`reprocess` requires exactly one selector, `--date YYYY-MM-DD`, `--source ID`, or `--all`; it rebuilds only selected archive days exclusively from terminal indexed normalized records and never inserts message records. It can recreate a selected generated daily archive at a newly configured `archive_root`. A source selection rebuilds every selected day with all indexed records for that day. Every mutating run durably stages its privacy-safe audit before archive/state terminal commits. Failed runs roll files and state back and receive a best-effort bounded, redacted failure audit; dry runs write neither state nor audits, including for an existing empty SQLite placeholder.

`review` uses the same exactly-one `--date` / `--source` / `--all` selector rule and supports `--dry-run`. Remote review is never automatic: `--dispatch` is required, and it is mutually exclusive with `--dry-run`. The CLI otherwise fails closed with a controlled non-secret error. Direct responses must contain only a bounded JSON `proposals` object and still pass the injected-service deterministic redaction/lineage/schema validation. Prompts, requests, and responses are never logged or persisted. A rejected remote payload performs no transport call and creates no queue or audit. Queue and redacted audit files commit as one rollback-protected outcome. A dry run makes no queue, audit, state, archive, or canonical-note changes.

## Change Log

- **2026-08-02:** Added Phase 2B's disabled-by-default direct DeepSeek Flash review adapter. Only exact `intelligence.provider: deepseek` and `intelligence.model: deepseek-v4-flash`, an environment-only `TELESCRIBE_DEEPSEEK_API_KEY`, and explicit `review --dispatch` can enable it; `--dry-run` cannot dispatch. The fixed stdlib-HTTPS endpoint receives only bounded/redacted indexed evidence and selective retrieval-only project context, never Hermes memory/session data, exports, paths, credentials, logs, or provider payload persistence. Valid proposals retain Phase 2A validation and may update only the review queue plus audit; no automatic or canonical-note updates were added.

- **2026-08-02:** Review context now requires an explicit `intelligence.project_context_root` when intelligence is enabled. It must be structurally disjoint from `archive_root` (neither may contain the other). Context files must be regular, non-symlink files below that root and are structurally rejected if they overlap any configured Telegram export, enabled or disabled, by path or filesystem identity; queue rewrites replace exactly one unambiguous owned marker range whose markers occupy standalone lines outside Markdown fenced code, while inline or fenced marker-like human prose remains untouched, and fail closed on malformed or duplicate owned ranges.

- **2026-08-02:** Preserved digit-only Telegram `date_unixtime` and `edited_unixtime` metadata in persisted derivatives, while retaining phone-like-value redaction in message text and all non-timestamp metadata.

- **2026-08-02:** Hardened Phase 2A review queues: strict calendar-date selectors; distinct, non-overlapping archive/review roots with symlink-safe queue paths; bounded evidence/context; redacted/revalidated model candidates; and atomic queue-plus-audit rollback behavior. The CLI remains deliberately provider-free and fails closed.

## Exact verification command

```bash
PYTHONPATH=src python3.12 -m pytest
```

This fallback command is useful in offline environments where `uv sync` cannot download dependencies; the project itself is packaged for uv.
