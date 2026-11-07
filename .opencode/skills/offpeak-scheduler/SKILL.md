---
name: offpeak-scheduler
description: Gate DeepSeek API work during peak-price windows and defer it to the next discounted off-peak window without polling or burning tokens. Use for DeepSeek targets, long delegated units, or parent orchestration that must resume DeepSeek children later.
---

# Off-peak Scheduler

Run `python3 scripts/offpeak.py gate --target provider:model` before launching matching work. Exit 0 means run; exit 3 means defer; exit 2 means invalid input; exit 1 means invalid scheduler data. Unknown targets are never gated.

## Single agent

During a peak window (for example, 00:00 Phoenix), run `gate`. On exit 3, do not poll or loop. Prefer a host one-time scheduler: run `schedule-hint --target provider:model --host t3` for T3 `schedule_task`, or `--host claude-code` for a one-shot CronCreate/`/schedule`, then end the turn. Use `--host cron` for a crontab hint. If the shell has no scheduler, run `wait --target provider:model` in a background shell. `wait` sleeps in chunks of at most ten minutes and recomputes the time.

For long units, pass `--estimate-minutes N`; the gate defers if the current off-peak window is too short. Children cannot be paused mid-run.

## Orchestrating parent

Route units as usual. For every gated DeepSeek child, call `defer --plan plan.json --unit ID`; launch non-gated units now. Schedule one wake at the earliest `defer_until_utc` using `schedule-hint`. When resumed, run `due`, spawn eligible units through `delegate_task`, verify them, then append `done --unit ID`. Never poll while waiting. State is append-only JSONL at `data/deferred.jsonl` by default; add `offpeak-scheduler/data/deferred.jsonl` to `.gitignore` in the consuming repository.

OpenCode Go and Zen rules require `--opt-in` or `OFFPEAK_OPT_IN=1`: those plans have no time-of-day discount, so waiting saves nothing unless you want to shift load. `--force` runs now and reports `forced`.

The windows are unverified research (`verified: false`) and should be re-checked before relying on pricing. See [window data and updates](references/windows.md) and the linked DeepSeek, OpenCode, and OpenRouter sources there.
