# Subagent Orchestrator — Case Test

> Snapshot from 2026-11-01, run against the Nov 1 installed skill (before the outcome ledger, verification harness, and skill linter). Targets and budgets reflect that day's capability snapshot.

Tested with the installed skill at `~/.config/opencode/skills/subagent-orchestrator/`, using the live T3 capability snapshot from this thread. Parent target: `opencode:opencode-go/deepseek-v4.1-flash`. Budget snapshots: normal = 2,000,000 units per provider; low = 100,000 units per provider.

## Summary

| Case | Task | Chars | Role / depth | Delegate? | Provider | Target / result |
|---|---|---:|---|---|---|---|
| case-01 | CLI help typo | 736 | implementation / 1 | no (parent) | opencode | `opencode:opencode-go/deepseek-v4.1-flash#variant=low` |
| case-02 | Null retry limit bug | 750 | implementation / 2 | no (parent) | opencode | `opencode:opencode-go/deepseek-v4.1-flash#variant=low` |
| case-03 | Config loader refactor | 914 | implementation / 3 | yes | opencode | `opencode:opencode-go/deepseek-v4.1-flash#variant=high` |
| case-04 | Auth migration across services | 904 | implementation / 5 | yes | codex | `codex:gpt-6.1-sol#reasoningEffort=ultra,serviceTier=default` |
| case-05 | Security review of auth migration | 834 | review / 5 | yes | codex | `codex:gpt-6.1-sol#reasoningEffort=ultra,serviceTier=default` |
| case-06 | Caching strategy comparison | 845 | research / 4 | yes | codex | `codex:gpt-6-astra#reasoningEffort=xhigh,serviceTier=default` |
| case-07 | Cross-runtime test matrix | 794 | test / 4 | yes | cursor | `cursor:composer-2.5#fastMode=true` |
| case-08 | Docs update for new flags | 713 | general / 1 | no (parent) | opencode | `opencode:opencode-go/deepseek-v4.1-flash#variant=low` |
| case-09 | Greenfield sync design | 836 | design / 5 | yes | codex | `codex:gpt-6.1-sol#reasoningEffort=ultra,serviceTier=default` |
| case-10 | Small PR review | 727 | review / 3 | yes | codex | `codex:gpt-6-luna#reasoningEffort=high,serviceTier=default` |
| case-11 | Budget-constrained migration | 755 | implementation / 5 | yes | — | `BUDGET_EXCEEDED` |

Provider counts: opencode 4, codex 5, cursor 1, budget-blocked 1. The three `no (parent)` cases are intentionally not delegated; the canonical target shown is the parent target, not a child.

## Cases and results

### case-01 — CLI help typo

**Classification:** role `implementation`, depth `1`, domain `docs`; ignore_cost=`false`; delegate=`false`; chars=`736`.

Once more, the nightly build is red because the CLI help text for the `--dry-run` flag says "simulate the run" instead of "simulates the run." The typo is in one file, `src/cli/help.ts`, and it does not affect runtime behavior. Please update only that string, keep the existing line wrapping consistent with the surrounding help entries, and run `pnpm test --filter cli-help` plus `pnpm lint src/cli/help.ts` to confirm nothing else moved. Do not reformat the file, do not rename the flag, and do not change the option description beyond the grammar fix. This is a single-file, low-risk cleanup that I would normally do inline rather than delegating, but I want the classifier to tell me which target should own it if it were delegated.

**Result:** `opencode:opencode-go/deepseek-v4.1-flash#variant=low` (provider `opencode`; reasoning 3.0, cost tier 1.0, remaining 2000000.0, budget estimate 2500.0).

**Deployment note:** the skill would do this in the parent; the provider shown is the parent, not a subagent.

### case-02 — Null retry limit bug

**Classification:** role `implementation`, depth `2`, domain `code`; ignore_cost=`false`; delegate=`false`; chars=`750`.

The config parser throws `TypeError: Cannot read properties of null` when a YAML file sets `retry_limit: null`. The failure is isolated to `src/config/parse.ts`, where the parser assumes every numeric field is a number before applying a default. I want a small fix that treats null and undefined the same way, preserves the existing behavior for zero and negative values, and adds one regression test in `tests/config/parse.test.ts` that exercises null, undefined, and a valid positive value. The public config schema must not change. Run the focused config test and the full unit suite if the focused test passes. This is a one-file edit with a known pattern, so it does not need a separate worktree; I mainly want the skill's target recommendation.

**Result:** `opencode:opencode-go/deepseek-v4.1-flash#variant=low` (provider `opencode`; reasoning 3.0, cost tier 1.0, remaining 2000000.0, budget estimate 12000.0).

**Deployment note:** the skill would do this in the parent; the provider shown is the parent, not a subagent.

### case-03 — Config loader refactor

**Classification:** role `implementation`, depth `3`, domain `code`; ignore_cost=`false`; delegate=`true`; chars=`914`.

Our configuration loader is duplicated across `src/config/load.ts`, `src/server/config.ts`, `src/worker/config.ts`, and two test helpers. The copies have drifted: the server path supports environment overrides, the worker path does not, and the test helpers hardcode a temporary directory in three different ways. I want one shared loader with a clear precedence order (file, environment, explicit override), a documented error type for malformed input, and no behavior change for existing valid configs. The change will touch roughly five source files and four test files, so I want a single worktree unit rather than several children editing the same module. Acceptance is the existing config tests plus new precedence and malformed-input tests, and the final diff should not include unrelated formatting. I am asking the classifier which target should own this multi-file refactor under the current cost policy.

**Result:** `opencode:opencode-go/deepseek-v4.1-flash#variant=high` (provider `opencode`; reasoning 3.0, cost tier 1.0, remaining 2000000.0, budget estimate 50000.0).


### case-04 — Auth migration across services

**Classification:** role `implementation`, depth `5`, domain `code`; ignore_cost=`false`; delegate=`true`; chars=`904`.

We are migrating authentication from a shared session cookie to short-lived access tokens plus rotating refresh tokens. The change spans the API gateway, the web client, the worker that refreshes background jobs, and the database migration that adds token families and revocation timestamps. Old sessions must remain valid during a two-week cutover, refresh-token replay must be rejected, and a failed refresh must log the user out cleanly instead of retrying forever. This is high-risk, cross-module work with a migration and a rollback path, so I want a separate worktree and a self-contained brief. The acceptance criteria are replay rejection, rotation on use, concurrent-refresh safety, a database migration that can run forward and backward, and the full auth plus integration suites passing. I expect this to be a depth-5 implementation unit; I want the classifier to pick the provider and effort.

**Result:** `codex:gpt-6.1-sol#reasoningEffort=ultra,serviceTier=default` (provider `codex`; reasoning 5.0, cost tier 4.0, remaining 2000000.0, budget estimate 400000.0).


### case-05 — Security review of auth migration

**Classification:** role `review`, depth `5`, domain `review`; ignore_cost=`true`; delegate=`true`; chars=`834`.

Act as an independent security reviewer for the authentication migration described in the previous unit. I want a read-only review of the diff from `main` to the auth branch: refresh-token rotation, replay detection, family revocation, concurrent refresh, cookie and CSRF behavior, error paths that might leak whether an account exists, and the database migration's forward and backward safety. Do not edit the branch. Report findings by severity with `file:line`, a concrete exploit or failure scenario, and a specific fix. If there are no findings, list exactly what you checked and what remains unverified. This is a high-risk, novel security review, so I want max reasoning and no cost pressure. The parent will verify each finding before acting on it, and the review must not be treated as a substitute for the integration tests.

**Result:** `codex:gpt-6.1-sol#reasoningEffort=ultra,serviceTier=default` (provider `codex`; reasoning 5.0, cost tier 4.0, remaining 2000000.0, budget estimate 400000.0).


### case-06 — Caching strategy comparison

**Classification:** role `research`, depth `4`, domain `research`; ignore_cost=`false`; delegate=`true`; chars=`845`.

Compare three caching strategies for the public product catalog: a read-through Redis cache with explicit invalidation, a Postgres materialized view refreshed on a schedule, and an in-process LRU with a short TTL backed by the database. The catalog has roughly 200,000 products, frequent price updates, and a read-heavy traffic pattern; stale prices for more than 60 seconds are unacceptable. I want a decision memo that covers consistency, failure modes, operational complexity, memory cost, cold-start behavior, and the migration path from today's direct database reads. Cite the relevant code paths in `services/catalog` and any benchmarks you rely on; do not change code. This is a research unit that should run read-only and shared, and I want the classifier to choose a cost-effective target with enough reasoning depth for the comparison.

**Result:** `codex:gpt-6-astra#reasoningEffort=xhigh,serviceTier=default` (provider `codex`; reasoning 4.0, cost tier 3.0, remaining 2000000.0, budget estimate 150000.0).


### case-07 — Cross-runtime test matrix

**Classification:** role `test`, depth `4`, domain `test`; ignore_cost=`false`; delegate=`true`; chars=`794`.

Run the integration test matrix for the new order-export pipeline across Node 20 and Node 22, with both Postgres 15 and MySQL 8 as the backing store, and with the feature flag on and off. The pipeline currently passes on the developer's Node 22 plus Postgres setup but has not been exercised on Node 20 or MySQL. I want a test unit that reproduces the matrix, records exact commands and environment variables, and reports failures with the smallest reproducible case. Do not change production code; limit writes to `tests/matrix/**` and a short report under `docs/qa/`. If a failure is environmental, say so instead of masking it. This is a test unit with moderate reasoning depth but a lot of mechanical execution, so I want the cost-effective target rather than the strongest model available.

**Result:** `cursor:composer-2.5#fastMode=true` (provider `cursor`; reasoning 4.0, cost tier 2.0, remaining 2000000.0, budget estimate 150000.0).


### case-08 — Docs update for new flags

**Classification:** role `general`, depth `1`, domain `docs`; ignore_cost=`false`; delegate=`false`; chars=`713`.

Update the CLI reference page and the quick-start guide to document the new `--profile` and `--no-color` flags. The flags already exist and are tested; this is a documentation-only change in `docs/cli.md` and `docs/quickstart.md`. Keep the existing heading hierarchy, add one example per flag, and make sure the examples match the current `--help` output. Do not change code, do not rewrite unrelated sections, and do not add a new tutorial. Run the docs link checker if it is available, otherwise report that it was not run. This is a mechanical single-pass docs edit, so it should stay in the parent unless there is a strong reason to delegate; the classifier should still identify the target that would own it.

**Result:** `opencode:opencode-go/deepseek-v4.1-flash#variant=low` (provider `opencode`; reasoning 3.0, cost tier 1.0, remaining 2000000.0, budget estimate 2500.0).

**Deployment note:** the skill would do this in the parent; the provider shown is the parent, not a subagent.

### case-09 — Greenfield sync design

**Classification:** role `design`, depth `5`, domain `architecture`; ignore_cost=`true`; delegate=`true`; chars=`836`.

Design a greenfield offline-sync layer for the mobile client. The requirements are still ambiguous: users may edit the same note on two devices, the server may be unavailable for hours, conflicts must be visible rather than silently overwritten, and the first release must work without a new backend service. I want a design memo that compares an operation log, a CRDT, and a last-writer-wins queue with server reconciliation; covers data model, merge semantics, conflict UI, migration, observability, and rollout; and ends with a recommendation and a list of decisions that need product input. Do not implement. This is novel architecture work with high ambiguity, so I want max reasoning and a strong target even though the output is a memo rather than code. The parent will use the memo to write separate implementation briefs later.

**Result:** `codex:gpt-6.1-sol#reasoningEffort=ultra,serviceTier=default` (provider `codex`; reasoning 5.0, cost tier 4.0, remaining 2000000.0, budget estimate 400000.0).


### case-10 — Small PR review

**Classification:** role `review`, depth `3`, domain `review`; ignore_cost=`false`; delegate=`true`; chars=`727`.

Review PR #184, which adds a cancellation token to the report export job and changes the retry loop to stop after the first user cancellation. The diff is about 180 lines across `services/reports/export.ts`, `services/reports/retry.ts`, and two tests. Focus on whether cancellation can be lost between retries, whether the job marks itself failed after a user cancellation, and whether the new test actually exercises the race. Do not edit the branch. Report findings by severity with `file:line` and a concrete fix; if the diff is clean, say what you checked. This is a small, well-scoped review, so I do not need the strongest model or max reasoning; I want the cheapest target that can still catch a real concurrency defect.

**Result:** `codex:gpt-6-luna#reasoningEffort=high,serviceTier=default` (provider `codex`; reasoning 3.0, cost tier 2.0, remaining 2000000.0, budget estimate 50000.0).


### case-11 — Budget-constrained migration

**Classification:** role `implementation`, depth `5`, domain `code`; ignore_cost=`true`; delegate=`true`; chars=`755`.

We need the same authentication migration as case 4, but this run is near the end of the billing period and I only have a small amount of usage left across all providers. The work is still high-risk: rotating refresh tokens, replay rejection, a database migration, and a two-week compatibility window. I want a separate worktree and a strong model, but I do not want the skill to launch a unit that will burn through the remaining budget and fail halfway. Estimate the unit, compare it with the remaining usage snapshot, and if no eligible target fits, stop with a clear budget outcome instead of quietly choosing a cheaper model that cannot handle the migration. This case is explicitly testing the remaining-usage guardrail, not the normal routing path.

**Result:** `BUDGET_EXCEEDED` — estimated 400000.0 units; no eligible target fit the low usage snapshot; 3 targets rejected by budget.

