# TeleScribe

TeleScribe turns a Telegram Desktop JSON export into one Markdown note per day. It runs locally. It is a vault archive, not chat memory. It does not write project notes or a task list, and it does not modify the export.

Archive text stays as written unless `archive_redaction` is set. Logs and model payloads stay redacted. A message belongs to the day of `date_unixtime` in the configured timezone. A Desktop date with no timezone is used only when that field is missing and `export_timezone` is set. A later export that omits an indexed message does not remove it. Text outside the daily-record markers survives a rewrite.

## Requirements

- Python 3.12 or newer.
- [uv](https://docs.astral.sh/uv/).
- A Telegram Desktop export in JSON. In Telegram Desktop, open a chat, choose **⋮ → Export chat history**, and set *Format* to **Machine-readable JSON**.

## Install

```bash
git clone https://github.com/rickkeller/telescribe
cd telescribe
uv sync --python 3.12
uv run telescribe --help
```

## Quickstart

The chat name at the top of `result.json` must be listed in `telegram_names`, or the run is refused.

```yaml
archive_root: /path/to/ObsidianVault
state_path: /path/to/telescribe-state/state.sqlite3
timezone: America/New_York
sources:
  - id: my_chat
    telegram_names: ["My Chat"]
    export_path: /path/to/export/result.json
    attachment_dir: /path/to/export/files
    enabled: true
```

```bash
uv run telescribe validate --config telescribe.yaml
uv run telescribe process --config telescribe.yaml --dry-run
uv run telescribe process --config telescribe.yaml
uv run telescribe status --config telescribe.yaml
```

Notes go to `archive_root/01 Daily Archives/Telegram/YYYY/YYYY-MM/YYYY-MM-DD.md`. The state file stays outside the vault. Attachments are recorded, not copied. The configuration does not scan a folder of exports, and a key that looks like a secret is refused.

To try it without a real export, use `tests/fixtures/string_text_result.json`. Its chat name is `Example Bot`.

## Commands

- `validate` reads the enabled exports and checks that the output parents are writable. It writes nothing.
- `process` archives messages that are not already in the index. `--dry-run` plans the same write and changes nothing.
- `reprocess` rebuilds selected days from the index. Use exactly one of `--date`, `--source`, or `--all`. It does not insert messages.
- `status` prints the indexed count. It does not write.
- `review` writes proposals to a review queue. It does not edit the daily notes. It is off unless `intelligence.enabled` is set. A live model call also needs `--dispatch`, and that cannot be combined with `--dry-run`. The only direct provider is `deepseek` / `deepseek-v4-flash`. The key is `TELESCRIBE_DEEPSEEK_API_KEY` in the environment, not a config field. See [docs/intelligence.md](docs/intelligence.md).

`archive_redaction` defaults to false. `export_timezone` is optional and is used only when `date_unixtime` is missing. The default `state_path` is `.hermes-telegram-archive/state.sqlite3`. That name is not a Hermes profile path. Keep the file outside the vault.

## Development

```bash
uv sync --python 3.12
uv run pytest
```

See [AGENTS.md](AGENTS.md) and [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md).

## License

MIT. See [LICENSE](LICENSE).
