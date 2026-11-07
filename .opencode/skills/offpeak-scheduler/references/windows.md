# Window data and updates

`data/windows.json` has `schema_version: 1` and a `rules` array. Each rule has a unique `id`, `match` (`providerInstanceId` allowlist and `model_patterns` globs), `enforce` (`default` or `opt-in`), `peak_windows_utc` (24-hour `start`/`end` and weekday names; `end` before `start` means the peak interval crosses midnight, while equal times are invalid), `holidays` (IANA timezone and ISO dates), `discount`, `sources`, `retrieved`, `verified`, and notes. Holiday dates use that rule's timezone; a matching holiday suppresses peak hours for the whole local calendar date. Leave unknown holiday dates empty and label that limitation: missing holidays can cause conservative extra waiting.

To update windows, check primary provider pricing and changelog pages, update the UTC ranges and `retrieved`, add dates for confirmed Chinese statutory holidays, and set `verified: true` only after human verification. DeepSeek research sources: [pricing](https://api-docs.deepseek.com/quick_start/pricing), [changelog](https://api-docs.deepseek.com/changelog). OpenCode references: [Go](https://opencode.ai/docs/go), [models](https://opencode.ai/docs/models). OpenRouter routing: [provider integration](https://openrouter.ai/docs/provider-integration). Current entries are unverified (retrieved 2026-11-07).

## Phoenix translation (America/Phoenix, UTC-7)

| UTC peak | Phoenix peak |
| --- | --- |
| Mon–Fri 01:00–04:00 | Sun–Thu 18:00–21:00 |
| Mon–Fri 06:00–10:00 | Sun–Thu 23:00–03:00 next day |

Thus a Phoenix Tuesday at 00:00 is in Tuesday 07:00 UTC peak; the next off-peak starts Tuesday 03:00 Phoenix (10:00 UTC). Weekends are off-peak, and adjacent off-peak periods merge across the weekend (Friday 03:00 through Sunday 18:00 Phoenix).
