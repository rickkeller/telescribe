# TeleScribe

This repository is a local Telegram export archiver. It is a vault archive, not chat memory. Do not write canonical project notes from it. Do not start an updater or a watcher from this repository. Do not add secrets, host paths, or chat ids.

## Layout

```
src/telescribe/
  cli.py              Typer app: process, reprocess, review, status, validate
  config.py           Pydantic config models; rejects secret-like YAML keys
  telegram_parser.py  reads Telegram Desktop result.json and normalizes messages
  pipeline.py         ArchiveService: plan, dry-run, commit
  daily_archiver.py   renders one Markdown note per local day
  state_store.py      SQLite message index (the checkpoint)
  transaction.py      staged file replacement with rollback
  models.py           immutable normalized message models
  redaction.py        secret/PII redaction applied before anything is persisted
  audit.py            per-run redacted audit records
  intelligence.py     optional review-only queue and the single opt-in DeepSeek adapter (off by default)
tests/                pytest suite; fixtures/ holds small synthetic exports
docs/intelligence.md  the optional review step in detail
```

## Run and test

```bash
uv sync --python 3.12
uv run pytest
PYTHONPATH=src python3.12 -m pytest     # offline fallback
```

For manual runs, use a temporary `archive_root` and `state_path` and a synthetic export, such as the files in `tests/fixtures/`. Never point a test at a real vault, real exports or a real state database.

## Conventions

- The same input always gives the same output. Reprocessing an unchanged export must change nothing.
- The SQLite index is the checkpoint. Never mark a message archived before its note is committed.
- Never modify the source export. Redact before anything is persisted.
- No network access by default. Remote review stays behind explicit config plus `--dispatch`.
- Line length is 100 (ruff).
- Behaviour changes need tests, plus an entry in the README's Change Log.

## Secrets: never commit them

- Never commit API keys (including `TELESCRIBE_DEEPSEEK_API_KEY`), bot tokens, `.env` files, real Telegram exports, state databases, archives, review queues or audit files.
- Don't put real chat names, chat or user ids, phone numbers, emails or home paths in code, tests, fixtures or docs. Fixtures must stay synthetic.
- Credentials come only from environment variables, never from YAML.
