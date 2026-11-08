# TeleScribe

This is the current document for version 0.2.0.

TeleScribe reads a Telegram Desktop `result.json` and writes one Obsidian note per local day. It is a vault archive. It is not chat memory.

## What it does

`validate` reads the enabled exports and checks that the output parents are writable. It writes nothing.

`process` archives messages that are not already in the index. `--dry-run` plans the same write and changes nothing.

`reprocess` rebuilds selected days from the index. Use exactly one of `--date`, `--source`, or `--all`. It does not insert messages.

`status` prints the indexed count. It does not write.

`review` writes proposals to a review queue. It does not edit the daily notes, project notes, or the index. It is off unless `intelligence.enabled` is set. A live model call also needs `--dispatch`, and that cannot be combined with `--dry-run`. The only direct provider is `deepseek` / `deepseek-v4-flash`. The key stays in the environment. It is not a config field.

## Configuration

One YAML file, passed with `--config`. It names `archive_root`, `state_path`, `timezone`, and one or more sources. Each source names `id`, `telegram_names`, `export_path`, and `attachment_dir`. A key that looks like a secret is refused. The file does not scan a folder of exports. The chat name inside `result.json` must match `telegram_names`.

The note path is `archive_root/01 Daily Archives/Telegram/YYYY/YYYY-MM/YYYY-MM-DD.md`. The state file stays outside the vault. Attachments are recorded, not copied. The export is not changed.

## Archive rules

A message belongs to the day of `date_unixtime` in the configured timezone. A Desktop date with no timezone is used only when `date_unixtime` is missing and `export_timezone` is set.

Archive text stays as written unless `archive_redaction` is set. Logs and model payloads stay redacted.

A later export that omits an indexed message does not remove it. A day with no new or changed message is not rewritten. An edit is a change even when the visible text matches, because the index hash includes the edited time and the original object.

Generated records stay between `<!-- telescribe:BEGIN daily-records -->` and `<!-- telescribe:END daily-records -->`. Text outside those markers survives a rewrite.

## What it does not do

It does not write project notes or a task list. A review queue is not those notes.
