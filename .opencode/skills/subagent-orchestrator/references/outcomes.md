# Outcomes: evidence for routing decisions

`data/routing-policy.json` is a starter heuristic. The outcome ledger is how you replace guesswork with your own evidence without letting a script silently retune the router.

The ledger is **append-only** and **local**. It records metadata about completed units, never prompts, child transcripts, or secrets.

## Record a unit

```bash
python3 scripts/outcome_ledger.py record \
  --unit auth-core \
  --role implementation \
  --depth 3 \
  --provider codex \
  --model gpt-6.1-sol \
  --option reasoningEffort=high \
  --isolation worktree \
  --mode async \
  --outcome success \
  --verification parent \
  --estimated-units 50000 \
  --actual-units 41200 \
  --duration 615 \
  --retries 0 \
  --review-findings 2 \
  --pr https://github.com/org/repo/pull/42
```

Omit `--actual-units` when the provider does not report usage. Do not guess. The summary keeps unknown usage separate from zero.

### Fields

| Field | Required | Meaning |
|---|---|---|
| `unit` | yes | Stable unit id from the delegation plan. |
| `role` | yes | `research`, `design`, `implementation`, `test`, `review`, or `general`. |
| `depth` | yes | Task depth 1–5. Compare like with like. |
| `target.providerInstanceId`, `target.model` | yes | The exact target used. |
| `target.options` | no | Repeated `--option id=value`; `true`/`false` become booleans. |
| `isolation` | no | `shared` or `worktree`. |
| `mode` | no | `async` or `wait`. |
| `outcome` | yes | `success`, `failure`, `partial`, or `cancelled`. |
| `verification` | no | `parent` when the parent read the diff and ran or inspected checks; `child-report` when only the child claimed success; `none` otherwise. Defaults to `none`. |
| `estimated_units` | no | The budget estimate used before launch. |
| `actual_units` | no | Reported usage after the run, if available. |
| `duration_seconds` | no | Wall-clock duration. |
| `retries` | no | Number of retries for this unit. |
| `review_findings` | no | Count of findings that required a change. |
| `pr` | no | Pull request URL. |
| `notes` | no | Short, non-sensitive note. Never paste a child summary or secret. |
| `recorded_at` | no | ISO 8601 timestamp; defaults to now (UTC). |

The record is one JSON object per line in `data/outcomes.jsonl`. The file is git-ignored because it can contain private usage and PR data.

## Summarize

```bash
# Detailed target groups: model + options + depth
python3 scripts/outcome_ledger.py summarize

# Coarse rollup for one model across depths and options
python3 scripts/outcome_ledger.py summarize --by model

# Only implementation runs at depth 3
python3 scripts/outcome_ledger.py summarize --role implementation --depth 3

# Per role, only parent-verified records
python3 scripts/outcome_ledger.py summarize --by role --verified-only

# Since a date, machine-readable
python3 scripts/outcome_ledger.py summarize --since 2026-11-01 --json

# Fail when there is nothing to report (useful in CI)
python3 scripts/outcome_ledger.py summarize --strict-empty
```

Grouping:

- `--by target` (default) separates model, option values, and depth. A low-effort depth-2 run is not pooled with a max-effort depth-5 run.
- `--by model` pools all depths and options for one `provider:model`.
- `--by role` pools by unit role.

Use `--role`, `--depth`, `--since`, and `--verified-only` to narrow the evidence before grouping.

Per group, the summary reports:

- runs and outcome counts;
- raw success rate;
- verified runs and verified success rate;
- retries and review findings;
- known duration totals and averages;
- known estimated/actual usage totals;
- paired estimated and actual totals, and `usage_ratio` from records that have **both** an estimate and actual usage;
- the latest record timestamp, compared chronologically rather than as a string.

## How to use it without fooling yourself

1. **Compare comparable units.** Filter by role and depth, and prefer records on similar repositories. A depth-2 rename is not evidence about a depth-5 migration.
2. **Trust verified records more.** A child that says "tests pass" is a claim. `verification: parent` means someone checked. Use `--verified-only` for routing decisions.
3. **Require a sample.** One run is an anecdote. Do not change a policy from a single success or failure.
4. **Separate cost from quality.** Usage and duration say nothing about correctness. Use outcome and review findings for quality; use units and seconds for cost.
5. **Change policy manually.** The ledger exports evidence; it does not rewrite `routing-policy.json`. Review the summary, adjust one tier or score, and state why in the policy's `source` or a commit message.
6. **Keep the ledger append-only.** Do not edit past records to make a target look better. If a record was wrong, append a corrected record with a note and use timestamps to distinguish them.
7. **Record failures, not just successes.** A target you avoid because of repeated failures is as useful as one you prefer.
8. **Do not record secrets or transcripts.** The ledger is metadata. Notes should be short and non-sensitive.

## Privacy and retention

- The ledger stays local; do not commit it unless the team explicitly wants a shared, sanitized ledger.
- It contains no prompts, no diffs, and no child messages.
- PR URLs and unit ids can still be sensitive. Treat the file like internal operations data.
- Delete or rotate the ledger when it is no longer useful; it is evidence, not an audit system.

## Limits

- Aggregation is descriptive, not causal. A better success rate can come from easier tasks.
- Unknown usage stays unknown; totals are not extrapolated.
- `review_findings` counts findings, not severity.
- The ledger does not track prompt quality, brief quality, or reviewer calibration.
- There is no concurrency lock; append-only writes are one line each, but do not run two writers against the same file on a network share.
