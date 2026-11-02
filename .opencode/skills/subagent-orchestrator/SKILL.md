---
name: subagent-orchestrator
description: Classify work and deploy T3 subagents. Use when a task has independent parallel units, needs a cross-provider model, a separate checkout or PR, background execution, or independent review. Chooses the cheapest capable target by default, or the strongest available target when IGNORE_COST is true, then writes self-contained briefs, launches delegates, and verifies and integrates their results.
---

# Subagent Orchestrator

A delegation operating procedure for T3 Code. It decides whether a task should be split, which target should do each unit, how to brief it, and how to verify the result.

The default is **do it yourself**. Delegation is a tool, not a ritual.

## Non-negotiables

- **Do not delegate trivial work.** One small edit, one question, or a tightly coupled change is faster in the parent.
- **Read live capabilities.** Call `orchestrator_capabilities` before the first delegation in a thread. Do not invent model names, prices, or availability.
- **Availability beats cost.** A disabled provider, a model that cannot run child tasks, or a cooling-down target is not a candidate — even with `IGNORE_COST=true`.
- **Budget before launch.** Never start a unit whose estimated usage exceeds the remaining budget. If remaining usage is unknown, ask for a cap or use a conservative limit; do not assume it is infinite.
- **One frame, one call; one unit, one brief.** A subagent gets a self-contained task, not the parent's entire history.
- **Worktree for writes, shared for read-only.** Parallel implementations never share a checkout.
- **Verify the work.** A child's summary is a claim. Read the diff, run the tests, and check the acceptance criteria.
- **No nested delegation by default.** A child does the work itself unless the brief explicitly allows subagents.
- **Do not poll.** A completed delegate wakes the parent. Use `task_status` when woken; do not sleep or loop.

## Local policy (this machine)

These override the generic routing guidance below.

- **Claude models are read-and-reason only.** Any `claude-*` model (including `cursor:claude-opus-5-5`, `claudeAgent:*`) may be used for `research`, `design`, and `review` units only. Never route `implementation` or `test` work to Claude — it is too expensive for coding.
- Coding routes to non-Claude targets: `opencode:opencode-go/deepseek-v4.1-flash`, `codex:gpt-6-*`, `cursor:composer-2.5`, `cursor:grok-*`.
- If no eligible non-Claude target meets the required depth, return `UNAVAILABLE` or ask. Do not silently fall back to Claude for code.
- Enforced by `data/routing-policy.json` → `role_restrictions` and checked in `scripts/select_target.py` (`role_allowed`).
- **One frame, one call; one unit, one brief.** A subagent gets a self-contained task, not the parent's entire history.
- **Worktree for writes, shared for read-only.** Parallel implementations never share a checkout.
- **Verify the work.** A child's summary is a claim. Read the diff, run the tests, and check the acceptance criteria.
- **No nested delegation by default.** A child does the work itself unless the brief explicitly allows subagents.
- **Do not poll.** A completed delegate wakes the parent. Use `task_status` when woken; do not sleep or loop.

## Phase 1 — Decide whether to delegate

Delegate only when at least one is true:

| Situation | Delegate? | Why |
|---|---|---|
| Two or more independent units with separate outputs | Yes | Real parallelism |
| A cross-provider model is materially better | Yes | Capability, not ceremony |
| The work needs a separate checkout or PR | Yes | Isolation |
| The work is long-running and can proceed in the background | Yes | Parent can keep working |
| An independent review or red-team pass is useful | Yes | Different perspective |
| One small edit, one file, one question | No | Parent is faster |
| Tightly coupled changes that constantly touch the same files | No, or one unit | Coordination cost exceeds gain |
| The child would need the parent's interactive credentials | No | The child has no parent history |

If the answer is no, do the work directly and stop reading this skill.

## Phase 2 — Classify into work units

A unit is one independently verifiable deliverable. Split by **outcome and write scope**, not by file count.

Each unit gets:

- `id` — short stable name, e.g. `api-auth`.
- `role` — `research | design | implementation | test | review | general`.
- `objective` — one sentence.
- `depends_on` — other unit ids, or empty.
- `write_scope` — files/directories it may change; empty for read-only.
- `isolation` — `shared` for read-only, `worktree` for writes.
- `acceptance` — observable criteria, not "looks good".
- `target` — provider instance, model, options.
- `brief` — self-contained instructions.

Rules:

1. **No overlapping writes.** Two children must not edit the same file in a shared checkout. If they must, sequence them or give each a worktree and integrate.
2. **Prefer 2–4 units.** Start small. More children multiply token burn, merge conflicts, and review load.
3. **Keep read-only work shared.** Research, review, and investigation do not need a worktree.
4. **Put a dependency edge where the output is consumed**, not just because the phases sound sequential.
5. **Include integration work in the parent.** Someone must merge, run the full suite, and resolve conflicts.

## Phase 3 — Route each unit

Call `orchestrator_capabilities` once per thread before the first delegation. Re-read it only after an error or a user-requested provider change.

### Filter

A target is eligible only when:

- its provider has `canRunChildTask: true`;
- it has no blocking `constraints` (for example, "Provider instance is disabled");
- the model id is present;
- if the parent uses a different provider, the provider supports cross-provider child tasks;
- the target is not rate-limited or cooling down;
- the target has enough remaining usage for the estimated unit, when a usage snapshot is available.

### Score the task

Use a 1–5 depth:

1. Lookup, format, single command.
2. One-file edit, known pattern.
3. Multi-file change, debugging with tests.
4. Cross-module refactor, architecture or migration tradeoff.
5. Novel design, high-risk migration, ambiguous requirements.

Domain defaults by role:

| Role | Domain |
|---|---|
| `implementation` | code |
| `research` | research |
| `review` | review |
| `design` | architecture |
| `test` | test |
| `general` | general |

Round up when ambiguous. A wrong cheap route is more expensive than the right one.

### Select

**Default (`IGNORE_COST=false`):**

1. The inherited parent target, if it is eligible and its reasoning score meets the depth.
2. Otherwise, the lowest cost tier whose reasoning score meets the depth.
3. Tie-break: higher reasoning, larger context, higher remaining quota, lower latency.
4. If no target meets the depth, do the work in the parent or ask; do not silently under-route.

**Max reasoning (`IGNORE_COST=true`):**

1. The highest reasoning score for the domain among eligible targets.
2. Tie-break: larger context, higher remaining quota, lower latency.
3. Choose the highest supported effort option (`max`, `xhigh`, `ultra`, or the provider's equivalent).
4. Ignore price and soft quota. Do **not** ignore availability, cooldowns, or the remaining-usage check.
5. If no eligible target exists, return `UNAVAILABLE`; do not fabricate one.

### Options

Read option ids and values from `orchestrator_capabilities`; they differ by provider. Common examples:

- Codex: `reasoningEffort` (`low`…`max`, `ultra`), `serviceTier`.
- Claude: `effort`, `fastMode`.
- OpenCode: `variant` (`low`, `high`, `max`).
- Cursor: `reasoning_effort` or `effort`, `contextWindow`, `fastMode`.
- Antigravity: `thinking_level`.

Pass only options the target declares. For cross-provider delegation, pass the full selected options rather than inheriting the parent's options.

### Budget and usage left

T3 does not expose a uniform "remaining usage" API for every provider. Use a user-maintained snapshot when the user has one:

```bash
cp data/usage.example.json data/usage.json
# edit remaining units for the providers/models you can actually use
```

The selector compares `remaining` against an estimate by depth. Override the estimate for unusually large or small units:

```bash
python3 scripts/select_target.py --capabilities caps.json --usage data/usage.json \
  --role implementation --depth 4 --estimate-units 220000
```

Rules:

1. A target with `remaining < estimate` is ineligible.
2. A provider/model missing from the snapshot is **unknown**, not unlimited. Skip it unless the user explicitly opts in with `--allow-unknown-budget`.
3. If every eligible target fails the budget, return `BUDGET_EXCEEDED`; do not launch and do not silently pick a more expensive target.
4. `IGNORE_COST=true` ignores price tiers, not the remaining usage check. If the max-reasoning target does not fit, use the strongest target that does or stop.
5. After the unit finishes, update the snapshot with actual usage if the provider reports it. If it does not, mark the snapshot stale rather than pretending it is exact.

Use `data/routing-policy.json` and `scripts/select_target.py` when you want a deterministic route. The policy is a starter heuristic; edit it to match your own measurements.

## Phase 4 — Write the brief

A delegate receives only `task`; it has no parent history. The brief must stand alone.

Use [`assets/brief-template.md`](assets/brief-template.md). The minimum is:

```text
ROLE: implementation
OBJECTIVE: Add refresh-token rotation to the auth service.
CONTEXT: Repo /path. Base branch main at abc123. Read AGENTS.md.
WRITE SCOPE: services/auth/**, tests/auth/** only.
DO NOT TOUCH: web/**, infra/**.
ACCEPTANCE:
- refresh tokens rotate once and old tokens are rejected
- existing login/logout tests pass
- new tests cover replay and expiry
COMMANDS: pnpm test --filter auth
DELIVERABLE: commit, push, PR against main; report tests and risks.
REPORT: changed files, commands, results, open questions.
```

Role-specific rules:

- **research** — answer specific questions, cite sources or file paths, no writes.
- **design** — produce a plan/tradeoff memo, no implementation unless asked.
- **implementation** — one write scope, one branch, tests, PR; no unrelated cleanup.
- **test** — reproduce, add or run tests, report exact commands and results; do not fix unrelated failures.
- **review** — findings only, severity, file:line, no edits unless explicitly asked.
- **general** — use sparingly; specify whether writes are allowed.

Do not put secrets in a brief. If a child needs a secret, stop and ask the parent to use `request_secret`; never paste tokens into a task.

## Phase 5 — Launch

Use `delegate_task` for cross-provider targets, non-default model options, separate checkouts, background work, or work that will outlive this turn.

```text
delegate_task({
  title: "auth-core",
  role: "implementation",
  task: "<the full brief>",
  target: {
    providerInstanceId: "codex",
    model: "gpt-6.1-sol",
    options: { reasoningEffort: "high" }
  },
  isolation: "worktree",
  mode: "async"
})
```

Rules:

- `isolation: "worktree"` for implementation, tests that write files, or any separate PR. Uncommitted parent edits are not copied; commit first if they are required.
- `isolation: "shared"` for read-only research and review.
- `mode: "async"` is the default. Use `mode: "wait"` only when this turn cannot proceed without the result. A timeout does not cancel the child.
- Keep `taskId`, `childThreadId`, and `link`.
- Do not launch more children than you can review. A practical starting point is 2–4 for code and 3–6 for research.
- Do not use `t3_thread_launch` for subagents. It creates top-level threads for user-requested parallel work. Subagents use `delegate_task`.
- Use the native `subagent` tool only when the child can inherit the same provider, model, and settings and finish in this turn. Otherwise use `delegate_task`.

## Phase 6 — Supervise

Keep a small table:

| Unit | Task id | Target | Status | PR |
|---|---|---|---|---|
| auth-core | … | codex:gpt-6.1-sol | running | — |

- **Do not poll.** Completion wakes the parent.
- On wake, call `task_status` for the task. Read `workState`, `summary`, `hasPendingQuestion`, and `pendingApproval`.
- If the child asks a question, answer with `t3_pending_request` on `childThreadId`. That tool cannot grant a permission approval; the user must do that in the UI.
- If the child is waiting on another child, it is not done.
- Cancel obsolete or duplicate work with `task_cancel`. Do not leave zombie delegates.
- Use `t3_thread_send` to steer a running child or start a fresh task on a finished child. Do not spam it.
- Use `t3_thread_read` to inspect the child's final result; reading marks it delivered.

## Phase 7 — Verify and integrate

Do not accept "done" as done.

1. **Read the diff.** Check scope: only the declared write scope changed.
2. **Run the acceptance commands yourself** when feasible. At minimum, inspect the child's exact commands and output.
3. **Run the full suite** after merging or applying multiple units.
4. **Review the review.** A review child's findings are inputs; verify each one before acting.
5. **Link the PR in the parent** with `link_pull_request`. The parent owns the PR relationship. A child never watches a PR.
6. **Watch only when asked or when the remaining work is waiting on checks/reviews.** Then call `watch_pull_request` and end the turn.
7. **Report** the target used, units completed, tests run, PR links, and any unresolved risks.
8. **Update the usage snapshot** if the user maintains one and actual usage is available. If the provider does not report it, mark the snapshot stale.

For a child that created a PR, its last line should be:

```text
PR <full URL>, head <SHA>, checks and reviews <state>.
```

## Failure handling

| Failure | Response |
|---|---|
| Child times out | The child keeps running. Do not duplicate it; check status later. |
| Child fails | Read the error, cancel if needed, narrow the brief or raise the target, then retry once. |
| Child asks a question | Answer via `t3_pending_request`, or cancel if the question invalidates the unit. |
| Child produces scope creep | Reject or revert out-of-scope files; do not merge. |
| Two children conflict | Stop one, serialize the units, or rebuild the change in one worktree. |
| Rate limit / 429 | Mark the target as cooling down and choose the next eligible target. Do not hammer it. |
| No eligible target | Do the work in the parent or ask the user. Return `UNAVAILABLE` in route-only mode. |
| Child summary disagrees with tests | Tests win. Re-open the unit. |

## Route-only mode

When the user asks only for a target — "which model", "route this", "classify this task" — do not deploy.

1. Read `orchestrator_capabilities`.
2. Score the task.
3. Select one eligible target.
4. Output exactly one line:

```text
<providerInstanceId>:<model>[#<option>=<value>,...]
```

or:

```text
UNAVAILABLE
```

or:

```text
BUDGET_EXCEEDED
```

No Markdown, JSON, backticks, quotes, or commentary. Preserve `/`, `.`, `-`, `_`, `:`, `@`, and `#` because they are part of the identifier. Do not output a display name. Validate the target against the capabilities you just read. Use `BUDGET_EXCEEDED` only when a capability-eligible target exists but none fits the remaining usage.

Use `scripts/select_target.py` if a deterministic result is preferred:

```bash
python3 scripts/select_target.py --capabilities caps.json \
  --role implementation --depth 3 --ignore-cost
```

## Anti-patterns

- One subagent per file.
- Several children writing the same files in a shared checkout.
- A brief that says "look at the repo" with no objective, scope, or acceptance test.
- Delegating a task that needs the parent's interactive credentials.
- Launching the maximum number of threads because the platform allows it.
- Polling a running delegate.
- Letting a child start its own subagents without explicit permission.
- Letting a child watch a PR.
- Accepting a summary without reading the diff.
- Using a premium model for mechanical work, or a cheap model for a high-risk migration.
- Hardcoding a stale model name from an old snapshot.
- Treating an unknown cost or unknown availability as free or available.

## References

- [`references/routing.md`](references/routing.md) — depth rubric, domains, target scoring, options.
- [`references/briefs.md`](references/briefs.md) — role templates and examples.
- [`references/operations.md`](references/operations.md) — isolation, lifecycle, supervision, integration.
- [`assets/brief-template.md`](assets/brief-template.md) — copy-paste brief.
- [`assets/plan.schema.json`](assets/plan.schema.json) — delegation plan contract.
- [`data/routing-policy.json`](data/routing-policy.json) — cost/reasoning tiers and budget estimates.
- [`data/usage.example.json`](data/usage.example.json) — remaining-usage snapshot shape.
- [`scripts/select_target.py`](scripts/select_target.py) — deterministic route-only selector.
- [`scripts/validate_plan.py`](scripts/validate_plan.py) — plan validator.
