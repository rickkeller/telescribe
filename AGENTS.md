# TeleScribe

This repository is a local Telegram export archiver. It is a vault archive, not chat memory. Do not write project notes or a task list from it. Do not commit an export, a vault, or a state database.

## Layout

```
src/telescribe/
  cli.py              validate, process, reprocess, status, review
  config.py           config models; rejects secret-like YAML keys
  telegram_parser.py  reads result.json
  pipeline.py         plan, dry-run, commit
  daily_archiver.py   one Markdown note per local day
  state_store.py      SQLite index
  paths.py            note paths and marker ranges
  transaction.py      staged file replacement
  models.py           normalized message models
  redaction.py        opt-in archive redaction; logs stay redacted
  audit.py            redacted audit records
  intelligence.py     optional review queue, off by default
tests/                pytest suite and synthetic fixtures
```

## Run

```bash
uv sync --python 3.12
uv run pytest
```

Use a temporary vault, a temporary state file, and a synthetic export. Do not point a test at a real vault or a real export.

## Rules

- Keep archive text verbatim unless `archive_redaction` is set. Logs and model payloads stay redacted.
- Prefer `date_unixtime`. A Desktop date with no timezone is used only when that field is missing and `export_timezone` is set.
- A narrower export must not drop an indexed message. An untouched day is not rewritten.
- Text outside `<!-- telescribe:BEGIN daily-records -->` and `<!-- telescribe:END daily-records -->` survives.
- Do not modify the export. Do not add secrets, host paths, or chat ids.
- Credentials come only from the environment, never from YAML.
