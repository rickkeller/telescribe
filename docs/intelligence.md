# Optional review (intelligence)

This is off by default. Ordinary `process`, `reprocess`, `validate` and `status` runs never contact a network service.

Phase 2B retains the disabled-by-default, review-only boundary and adds one direct opt-in adapter: `deepseek` / `deepseek-v4-flash`. It is used only with enabled matching configuration, the environment-only `TELESCRIBE_DEEPSEEK_API_KEY`, and an explicit `review --dispatch`; no credential, endpoint, transport, timeout, project list, or arbitrary provider setting can appear in YAML or the public factory. The factory constructs the fixed stdlib HTTPS destination internally. The model receives only bounded/redacted indexed evidence plus selectively retrieved approved project context. This retrieval-only context remains limited to regular, non-symlink files under the separate explicit `intelligence.project_context_root`, structurally disjoint from `archive_root` and excluding every configured Telegram export (enabled or disabled, including hardlinks). For remote dispatch, context is narrower still: only intentional `.md` and `.txt` project notes are allowed. Dotfiles, `.env`, config/settings, logs, SQLite/state/WAL/SHM databases, audit/run material, raw exports/results (including JSON), and all other file types are rejected rather than transformed. After redaction, any evidence or context containing an absolute path, secret assignment, operational-storage term, or raw-export indicator also rejects the request before transport. It never receives Hermes memory or session transcripts. The adapter cannot edit canonical project notes, tasks, ideas, decisions, archives, indexes, or checkpoints; successful review may write only its proposal queue and redacted audit.

## Configuration

```yaml
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

Other optional limits are `max_messages` (default 20, max 100), `max_context_notes` (default 3, max 10) and `max_context_chars` (default 4000, max 12000).

## Running a review

Direct review dispatch is deliberately separate from ordinary review and dry runs. Set the credential outside YAML, then use this preflight (it checks only whether the variable exists, never prints its value) before a live dispatch:

```bash
if [ -z "${TELESCRIBE_DEEPSEEK_API_KEY+x}" ]; then
  echo "TELESCRIBE_DEEPSEEK_API_KEY is not set" >&2
  exit 1
fi
uv run telescribe review --config telescribe.yaml --all --dispatch
```

`review` uses the same exactly-one `--date` / `--source` / `--all` selector rule and supports `--dry-run`. Remote review is never automatic: `--dispatch` is required, and it is mutually exclusive with `--dry-run`. The CLI otherwise fails closed with a controlled non-secret error. Direct responses must contain only a bounded JSON `proposals` object and still pass the injected-service deterministic redaction/lineage/schema validation. Prompts, requests, and responses are never logged or persisted. A rejected remote payload performs no transport call and creates no queue or audit. Queue and redacted audit files commit as one rollback-protected outcome. A dry run makes no queue, audit, state, archive, or canonical-note changes.
