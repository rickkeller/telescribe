# Optional review

This is off by default. `validate`, `process`, `reprocess`, and `status` do not call a model.

`review` writes proposals to a review queue. It does not edit the daily notes, project notes, or the index. A live call needs `intelligence.enabled`, `provider: deepseek`, `model: deepseek-v4-flash`, and `--dispatch`. `--dry-run` cannot be combined with `--dispatch`. The key is `TELESCRIBE_DEEPSEEK_API_KEY` in the environment. It is not a config field.
