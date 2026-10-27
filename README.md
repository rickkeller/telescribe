# TeleScribe

TeleScribe turns **Telegram Desktop JSON chat exports** into **one Markdown note per day**, ready for an Obsidian vault. It runs locally and gives the same output for the same input. Run it on the same export as often as you like: a SQLite index remembers which messages are already archived, so only new or late messages change any notes.

What it does **not** do by default: send network requests, classify content, copy attachments, watch for new exports, write project notes, or modify your Telegram export. An optional model-assisted review step is off by default (see [docs/intelligence.md](docs/intelligence.md)).

It is a vault archive, not chat memory.

## Requirements

- Python 3.12 or newer.
- [uv](https://docs.astral.sh/uv/).
- A Telegram Desktop export in **JSON** format. In Telegram Desktop, open a chat, choose **⋮ → Export chat history**, and set *Format* to **Machine-readable JSON**. You get a folder with `result.json` (and `files/` if you exported media).

## Install

```bash
git clone https://github.com/rickkeller/telescribe
cd telescribe
uv sync --python 3.12
uv run telescribe --help
```

During install, uv may warn about the `uv_build` version pin. It's harmless when the sync succeeds.

## Quickstart

1. Note the chat name at the top of `result.json` (the `"name"` field). TeleScribe only processes an export whose name you list.
2. Create `telescribe.yaml`:

   ```yaml
   archive_root: /path/to/ObsidianVault          # where daily notes are written
   state_path: /path/to/telescribe-state/state.sqlite3
   timezone: America/New_York                    # used to split messages into days
   sources:
     - id: my_chat                               # your own stable ID (no spaces)
       telegram_names: ["My Chat"]               # must match "name" in result.json
       export_path: /path/to/export/result.json
       attachment_dir: /path/to/export/files
       enabled: true
   ```

3. Check it, preview it, then run it:

   ```bash
   uv run telescribe validate --config telescribe.yaml          # parses exports, checks paths, writes nothing
   uv run telescribe process  --config telescribe.yaml --dry-run
   uv run telescribe process  --config telescribe.yaml
   uv run telescribe status   --config telescribe.yaml
   ```

Notes are written to `<archive_root>/01 Daily Archives/Telegram/YYYY/YYYY-MM/YYYY-MM-DD.md`. When you get a newer export of the same chat, point `export_path` at it and run `process` again. Messages already in the index are skipped.

To try it without a real export, use the sample at `tests/fixtures/string_text_result.json`. Its chat name is `Example Bot`.

## Commands

| Command | What it does |
| --- | --- |
| `process [--dry-run]` | Archive every new message. `--dry-run` lists the notes that would change and writes nothing. |
| `reprocess --date YYYY-MM-DD` / `--source ID` / `--all` | Rebuild the chosen daily notes from the index. Pass exactly one selector. |
| `status` | Show how many messages are indexed. |
| `validate` | Parse every enabled export and check that output paths are writable, without writing. |
| `review …` | Optional model-assisted review. See [docs/intelligence.md](docs/intelligence.md). |

Every command needs `--config PATH`. Run `uv run telescribe <command> --help` for the options.

## Configuration reference

| Key | Default | Meaning |
| --- | --- | --- |
| `archive_root` | required | Where the daily notes go, usually your Obsidian vault. |
| `state_path` | `.hermes-telegram-archive/state.sqlite3` | SQLite index, which is the checkpoint. Keep it outside the vault, and back it up. |
| `timezone` | `America/New_York` | IANA zone used to group messages into calendar days. |
| `sources[].id` | required | Stable ID for this chat. No whitespace. Must be unique. |
| `sources[].telegram_names` | required | Accepted export names. An export with any other name is refused. |
| `sources[].export_path` | required | Path to `result.json`. |
| `sources[].attachment_dir` | required | The export's media folder. TeleScribe doesn't copy attachments. |
| `sources[].enabled` | `true` | Set to `false` to skip this source. |
| `intelligence.*` | disabled | Optional review settings. See [docs/intelligence.md](docs/intelligence.md). |

Secrets never go in YAML. A config file with a key named like a secret (`secret`, `password`, `credential`, `api_key`, `access_token`) is refused. The one optional credential, `TELESCRIBE_DEEPSEEK_API_KEY`, is read only from the environment and only for `review --dispatch`.

## Troubleshooting

- **`Invalid value for '--config': Path … does not exist`** (exit code 2): check the path you passed to `--config`.
- **A long traceback ending in `ValueError: …`.** Errors are reported as Python tracebacks, so read the last line:
  - `Telegram export name does not match configured names`: add the export's exact `"name"` value to `telegram_names`.
  - `configured output parent is not writable`: create the parent folder of `archive_root` or `state_path`, or fix its permissions.
  - `secrets are not permitted in YAML configuration`: remove any key whose name looks like a secret, such as `api_key`, `password` or `secret`.
  - `Telegram export must contain a messages list`: that file isn't a JSON chat export.
- **`uv sync` picks the wrong Python:** pass `--python 3.12` explicitly. If you're inside another virtualenv, uv warns that `VIRTUAL_ENV` doesn't match; run `deactivate` first.
- **Offline, so `uv sync` can't download:** with the dependencies already installed, run `PYTHONPATH=src python3.12 -m pytest`, or `PYTHONPATH=src python3.12 -m telescribe.cli`.
- **A note didn't change after a re-run:** that's expected when the export has no new messages. To force a rebuild, use `reprocess`.
- **Messages land on the "wrong" day:** days are split in the configured `timezone`, not in UTC.

## How it behaves

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

## Development

```bash
uv sync --python 3.12
uv run pytest
```

See [AGENTS.md](AGENTS.md) for the layout and rules.

## Change Log

- **2026-08-02:** Added Phase 2B's disabled-by-default direct DeepSeek Flash review adapter. Only exact `intelligence.provider: deepseek` and `intelligence.model: deepseek-v4-flash`, an environment-only `TELESCRIBE_DEEPSEEK_API_KEY`, and explicit `review --dispatch` can enable it; `--dry-run` cannot dispatch. The fixed stdlib-HTTPS endpoint receives only bounded/redacted indexed evidence and selective retrieval-only project context, never Hermes memory/session data, exports, paths, credentials, logs, or provider payload persistence. Valid proposals retain Phase 2A validation and may update only the review queue plus audit; no automatic or canonical-note updates were added.

- **2026-08-02:** Review context now requires an explicit `intelligence.project_context_root` when intelligence is enabled. It must be structurally disjoint from `archive_root` (neither may contain the other). Context files must be regular, non-symlink files below that root and are structurally rejected if they overlap any configured Telegram export, enabled or disabled, by path or filesystem identity; queue rewrites replace exactly one unambiguous owned marker range whose markers occupy standalone lines outside Markdown fenced code, while inline or fenced marker-like human prose remains untouched, and fail closed on malformed or duplicate owned ranges.

- **2026-08-02:** Preserved digit-only Telegram `date_unixtime` and `edited_unixtime` metadata in persisted derivatives, while retaining phone-like-value redaction in message text and all non-timestamp metadata.

- **2026-08-02:** Hardened Phase 2A review queues: strict calendar-date selectors; distinct, non-overlapping archive/review roots with symlink-safe queue paths; bounded evidence/context; redacted/revalidated model candidates; and atomic queue-plus-audit rollback behavior. The CLI remains deliberately provider-free and fails closed.

## License

MIT. See [LICENSE](LICENSE).
