---
name: subagent-orchestrator
description: Use when a task has two or more independent units that can run in parallel, needs a different provider's model, a separate checkout or PR, background execution, or an independent review; when the user asks to delegate or use subagents, forbids the parent from writing code, or names a provider, model, or setting; when a delegated task reports back and its result must be checked; or when asked only which target should handle a task.
---

# Subagent Orchestrator

A delegation operating procedure for T3 Code. It decides whether a task should be split, which target should do each unit, how to brief it, and how to verify the result.

The default is **do it yourself**. Delegation is a tool, not a ritual.

## Precedence

When instructions conflict, apply this order:

1. Platform and tool contracts.
2. Explicit user instructions.
3. This skill's defaults and local routing policy.

An explicit user instruction wins over a skill default and over ranking preferences. Local routing policy is still a hard gate for automatic routing: if a pin conflicts with it, report the conflict and ask. Never silently allow the conflict and never silently substitute a different target.

- **Named targets are hard constraints.** If the user names a provider, model, or option, validate it against live capabilities and use it when eligible, even when a cheaper or higher-scoring target exists. If it is unavailable, over budget, cooling down, or disallowed by local policy, report the exact reason and stop. In route-only mode, emit that target's canonical form, `UNAVAILABLE`, or `BUDGET_EXCEEDED`; never a different target.
- **Parent execution can be forbidden.** If the user says not to edit files or to delegate all code, the parent may read, run checks, and review, but must not edit, commit, merge, or otherwise modify tracked files. Skip the do-it-yourself path. If no eligible child exists, stop and ask or report `UNAVAILABLE`; do not take the work back. Integration that writes requires a delegated unit or explicit permission. Appending the local, git-ignored outcome ledger is metadata, not a tracked-file edit.
- **New top-level threads require an explicit request.** "Use subagents", "delegate", "run in parallel", and "multiple agents" mean child tasks, not new threads.
- **A new unit, review, or recheck is a new `delegate_task`.** `t3_thread_send` only steers a running child. Never use it to start fresh work on a finished child.

## Non-negotiables

- **Do not delegate trivial work.** One small edit, one question, or a tightly coupled change is faster in the parent.
- **Read live capabilities.** Call `orchestrator_capabilities` before the first delegation in a thread. Do not invent model names, prices, or availability.
- **Availability beats cost.** A disabled provider, a model that cannot run child tasks, or a cooling-down target is not a candidate — even with `IGNORE_COST=true`.
- **Budget before launch.** Never start a unit whose estimated usage exceeds the remaining budget. If remaining usage is unknown, ask for a cap or use a conservative limit; do not assume it is infinite.
- **One frame, one call; one unit, one brief.** A subagent gets a self-contained task, not the parent's entire history.
- **Worktree for writes, shared for read-only.** Parallel implementations never share a checkout.
- **Verify the work.** A child's summary is a claim. Read the diff, run the tests, and check the acceptance criteria.
- **No nested delegation by default.** A child does the work itself unless the brief explicitly allows subagents.
- **Do not poll.** A completed delegate wakes the parent. Follow the wake's `next`; do not sleep, loop, or set a watcher to wait for a child.

## Local policy (this machine)

These are defaults for automatic routing. An explicit user instruction still takes precedence: surface the conflict rather than silently following either one.

- **Claude models are read-and-reason only.** Any `claude-*` model (including `cursor:claude-opus-5-5`, `claudeAgent:*`) may be used for `research`, `design`, and `review` units only. Never route `implementation` or `test` work to Claude — it is too expensive for coding.
- **Web research goes to Gemini Flash.** Any delegated research that searches or fetches the web must use `antigravity:gemini-3.8-flash`. If a review or implementation unit needs a web lookup, split that lookup into a separate `research` unit instead of routing the whole unit to Gemini. If Gemini Flash is unavailable, say so; do not substitute silently.
- Coding routes to non-Claude targets: `opencode:opencode-go/deepseek-v4.1-flash`, `codex:gpt-6-*`, `cursor:composer-2.5`, `cursor:grok-*`.
- If no eligible non-Claude target meets the required depth, return `UNAVAILABLE` or ask. Do not silently fall back to Claude for code.
- Enforced by `data/routing-policy.json` → `role_restrictions` / `web_research`, checked in `scripts/select_target.py` (`role_allowed`, `--web`).

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

If the user requires delegation or forbids parent execution, delegation is mandatory even for a row that says "No". Skip the do-it-yourself path; if no eligible child exists, stop and ask.

If the answer is no and the user left the approach open, do the work directly and stop reading this skill.

### Excuses that break Phase 1

If you catch yourself thinking the left column, do the right column.

| Excuse | Reality |
|---|---|
| "The skill is loaded, so I should delegate." | Loading the skill is not a reason. The table above decides, and its default is no. |
| "The user said 'where it helps', so I should split it." | That leaves the approach open. A coupled change is one unit or no delegation. |
| "More children means a more thorough job." | Each child costs a brief, a review, and a merge. Start with 2–4. |
| "It's tiny. I'll just do it, even though they said delegate." | The user's instruction outranks the trivial-work default. Delegate it. |
| "No coder is available, so I'll write it myself just this once." | If parent writes are forbidden, that is taking the work back. Report `UNAVAILABLE` and ask. |
| "No coder is available, but Claude can code." | Local policy bars Claude from `implementation` and `test`. Report `UNAVAILABLE`. |
| "Parallel means new threads." | Parallel means child tasks. `t3_thread_launch` needs an explicit request for a new thread. |

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
5. **Assign integration explicitly.** Someone must merge, run the full suite, and resolve conflicts. The parent owns it by default; if the user forbids parent writes, create a delegated integration unit in a worktree instead.

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

1. A user-named target, if it is eligible. A user pin never falls through to another target.
2. The inherited parent target, if it is eligible and its reasoning score meets the depth.
3. Otherwise, the lowest cost tier whose reasoning score meets the depth.
4. Tie-break: higher reasoning, larger context, higher remaining quota, lower latency.
5. If no target meets the depth, do the work in the parent only if the user permits parent execution; otherwise ask or return `UNAVAILABLE`. Do not silently under-route.

**Max reasoning (`IGNORE_COST=true`):**

A user pin still wins. If it is ineligible, stop with the exact reason instead of choosing the strongest unpinned target.

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

### Exact pins

When the user names a target, pin it instead of letting policy re-rank it:

```bash
python3 scripts/select_target.py --capabilities caps.json \
  --role review --depth 4 --provider codex --model gpt-6.1-sol \
  --option reasoningEffort=high
```

`--provider`, `--model`, and repeated `--option id=value` are exact filters. They are hard constraints, not preferences. `true` and `false` in an option value are parsed as booleans:

- eligible pin — use it even when a cheaper or higher-scoring target exists;
- missing, disabled, undeclared option, disallowed, or cooling-down pin — `UNAVAILABLE`;
- otherwise eligible but over budget — `BUDGET_EXCEEDED`;
- never fall back to a different canonical target or a different option value.

Use `--parent-provider` / `--parent-model` for an inherited-target preference. That is a tie-break, not a user pin.

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
5. If the user pinned a target and it does not fit, stop with `BUDGET_EXCEEDED` and report the numbers. Do not substitute another target.
6. After the unit finishes, update the snapshot with actual usage if the provider reports it. If it does not, mark the snapshot stale rather than pretending it is exact.

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

Choose the mechanism in this order:

1. **Native child tool** only when all are true: the child runs on the parent's provider; that provider exposes a native child mechanism; the child needs no target, model, or option override; and the work finishes in this turn. Do not infer a tool from `features.appOwnedSubagents`; it describes app support, not a callable tool.
2. **`delegate_task`** otherwise: another provider, a requested target or options, a worktree or separate checkout, background/async work, or work that will outlive this turn.
3. **`t3_thread_launch`** only when the user explicitly asks for a new top-level thread. It is not a subagent mechanism.

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
- A child never starts another child unless the brief explicitly allows it. Even then, keep the fan-out bounded and make the child report its own children.
- If the user forbade parent writes, do not integrate by editing the parent checkout. Use a delegated integration unit in a worktree, or ask the user to relax the constraint.

### Excuses that break Phase 5

None of these is an exception.

| Excuse | Reality |
|---|---|
| "The pin is a hard constraint, so I'll launch and flag the budget risk." | The budget check runs before launch, pinned or not. Over budget is `BUDGET_EXCEEDED`, no launch. |
| "The launch will be rejected anyway if it's over budget." | Nothing checks the budget for you. Run the check before `delegate_task`; an over-budget unit gets no call at all. |
| "The pinned target hit a 429. I'll relaunch it after the cooldown." | Do not relaunch, now or later. You cannot schedule a launch. Report the limit and the retry time, then ask. The user decides whether to wait. |
| "It's only cooling down, so I'll retry it." | A cooling-down target is not a candidate, pinned or not. Pick another eligible target if it was not pinned; otherwise report and ask. |
| "A similar model is free, and the user wants progress." | Swapping a pinned target is the exact failure the pin guards against. Report and ask. |
| "The finished child has the context, so I'll message it the fix." | `t3_thread_send` only steers a running child. New work is a new `delegate_task` with the findings in the brief. |
| "The strongest model will research better." | Web research goes to `antigravity:gemini-3.8-flash` by local policy. |
| "Nothing to do while I wait, so I'll add a review or check status." | Do not review work still in flight, and do not poll. End the turn. |
| "If I can't launch, I'll still list the call and mark it blocked." | A call you would not make is not a next step. Report the block instead. |

## Phase 6 — Supervise

Keep a small table:

| Unit | Task id | Target | Status | PR |
|---|---|---|---|---|
| auth-core | … | codex:gpt-6.1-sol | running | — |

- **Do not poll.** After launching async work, continue independent local work; when nothing remains, end the turn. A completed delegate wakes the parent.
- When a wake arrives, follow its `next` instruction. Call `task_status` when the wake asks for status detail or the result is otherwise unread. If the wake reports completion or says to end, process the result and end the turn.
- Do not call `task_status` speculatively, sleep, schedule a wakeup, or set a watcher to wait for a delegate. A wait timeout does not cancel the child; do not duplicate it.
- On a status read, inspect `workState`, `summary`, `hasPendingQuestion`, and `pendingApproval`. If the child is waiting on another child, it is not done.
- If the child asks a question, answer with `t3_pending_request` on `childThreadId`. That tool cannot grant a permission approval; the user must do that in the UI.
- Cancel obsolete or duplicate work with `task_cancel`. Do not leave zombie delegates.
- Use `t3_thread_send` only to steer a running child: answer a blocking question, correct scope, or redirect. Never use it to start a new task on a finished child.
- Use `t3_thread_read` to inspect the child's final result; reading marks it delivered.

## Phase 7 — Verify, fix, and re-review

Do not accept "done" as done. Each implementation or test unit goes through three gates, in order, as soon as it reports back. Do not batch reviews until the end, and do not start a unit that depends on this one until it is accepted.

```text
child reports done
  -> Gate 1  machine check   verify_unit.py: declared commands + write scope
  -> Gate 2  spec check      does the diff meet each acceptance criterion?
  -> Gate 3  quality review  fresh review child at depth >= 3; parent reads the diff at depth <= 2
  -> accept the unit, record the outcome, release dependent units
any gate fails -> fix round -> back to Gate 1
```

1. **Gate 1: machine check.** Run [`scripts/verify_unit.py`](scripts/verify_unit.py) on the unit's branch (see [`references/operations.md`](references/operations.md) §8). Check scope: only the declared write scope changed. With no plan file, run the unit's acceptance commands yourself and compare `git diff --name-only <base>..<head>` with the scope. If it fails, go straight to a fix round. Do not spend a reviewer on a unit whose tests fail.
2. **Gate 2: spec check.** Read the diff against each acceptance criterion. Missing behavior, extra behavior, or a criterion the tests do not exercise is a gap. The parent can do this check; at depth 4–5, use a review child with the criteria in its brief.
3. **Gate 3: quality review.** At depth 3 or more, or for anything security-sensitive, launch a fresh review child. At depth 1–2 the parent reads the diff. Review the review: a review child's findings are inputs, so verify each one before acting on it.
4. **Fix round.** A failed gate re-opens the unit:
   - Launch a new `delegate_task`, never a message to the finished child. Use the same role, write scope, and branch. Base it on the unit's current head.
   - Build the brief with [`scripts/fix_brief.py`](scripts/fix_brief.py): it turns a failing `verify_unit` report and review findings into one self-contained fix brief.
   - Keep the same target unless the failure shows the depth was underrated. Raise it only when the target is not user-pinned.
   - The parent fixes it directly only when parent writes are allowed and the fix is trivial.
   - After the fix, start again at Gate 1. Then rerun every gate from the one that failed onward.
5. **Recheck with a new delegate.** After fixes land, a re-review is a fresh `delegate_task` whose brief includes the prior findings, the fix commits/diff, and the original acceptance criteria. Do not send the recheck to the finished reviewer thread.
6. **Two fix rounds, then escalate.** If a unit fails a gate after two fix rounds, stop. Report the history to the user (what failed, what was tried, the current diff) and ask whether to raise the target, re-split the unit, or take it another way. Do not start a third round on your own.
7. **Integrate.** After all units are accepted, merge and run the full suite. At depth 3 or more, run one final review child on the combined diff. If the parent may not write, a delegated integration unit does the merge and suite; the parent inspects the result.
8. **Link the PR in the parent** with `link_pull_request` (or `link_pull_requests` for several) as soon as it exists or after a takeover/push. The parent owns the PR relationship. A child never watches a PR.
9. **Watch only when asked or when the remaining work is waiting on checks/reviews.** Then call `watch_pull_request` and end the turn.
10. **Record the outcome** with `scripts/outcome_ledger.py record`, or `scripts/record_verification.py` from a reviewed `verify_unit` report, when actual usage, duration, or verification status is known. Pass the number of fix rounds as `--retries`. Mark whether the parent verified the result; do not record a child's self-report as verified. The ledger is local metadata, so appending it does not violate a no-parent-write constraint.
11. **Report** the target used, units completed, fix rounds, tests run, PR links, and any unresolved risks.
12. **Update the usage snapshot** if the user maintains one and actual usage is available. If the provider does not report it, mark the snapshot stale.

A user-pinned target stays pinned. If it becomes unavailable, report the exact reason and ask; do not quietly reroute to another target.

For a child that created a PR, its last line should be:

```text
PR <full URL>, head <SHA>, checks and reviews <state>.
```

### Excuses that break Phase 7

| Excuse | Reality |
|---|---|
| "The child says all tests pass." | That is a claim. Gate 1 runs the tests. If they fail, the tests win. |
| "The out-of-scope edit was minor." | Out-of-scope files fail Gate 1. Revert them, or widen the scope on purpose and say so. |
| "The fix passed verify_unit, so the unit is done." | A fix that came from review findings goes back through review. Recheck with a new delegate. |
| "A stronger model will fix it faster." | Keep the target unless the failure shows the depth was underrated. A scope violation or a missed test is a brief problem, not a model problem. |
| "One more fix round will get it." | After two rounds, escalate. A third blind retry usually means the brief or the split is wrong. |
| "I'll review everything once all units finish." | Review each unit as it lands. Late reviews make dependent units build on broken work. |

## Failure handling

| Failure | Response |
|---|---|
| Child times out | A wait timeout does not cancel the child. Do not duplicate it; process the eventual completion wake. |
| Child fails | Read the error, cancel if needed, narrow the brief or raise the target, then retry once. |
| Child asks a question | Answer via `t3_pending_request`, or cancel if the question invalidates the unit. |
| Child produces scope creep | Reject or revert out-of-scope files; do not merge. |
| Two children conflict | Stop one, serialize the units, or rebuild the change in one worktree. |
| Rate limit / 429 | Mark the target as cooling down. If it was user-pinned, stop and report `UNAVAILABLE`; otherwise choose the next eligible target. Do not hammer it. |
| Requested target unavailable | Sticky pin. Report the exact reason; ask whether to wait, change target, or relax the constraint. Never substitute silently. |
| Authentication failure (401/403/sign-in) | Report `authentication required for <provider>`; stop that unit. Do not describe it as unavailable or reroute silently. Retry only after the user authenticates or authorizes another target. |
| No eligible target | Do the work in the parent only if the user permits parent execution; otherwise ask or return `UNAVAILABLE`. |
| Child summary disagrees with tests | Tests win. Re-open the unit with a fix round (Phase 7). |

## Route-only mode

When the user asks only for a target — "which model", "route this", "classify this task" — do not deploy.

1. Reuse this thread's `orchestrator_capabilities` snapshot; re-read only after an error, a provider change, or a user-named provider change.
2. Score the task.
3. Select one eligible target, honoring any user pin exactly.
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

- [`references/routing.md`](references/routing.md) — depth rubric, domains, target scoring, pins, options.
- [`references/briefs.md`](references/briefs.md) — role templates and examples.
- [`references/operations.md`](references/operations.md) — isolation, lifecycle, supervision, integration.
- [`references/outcomes.md`](references/outcomes.md) — outcome-ledger schema and evidence rules.
- [`assets/brief-template.md`](assets/brief-template.md) — copy-paste brief.
- [`assets/plan.schema.json`](assets/plan.schema.json) — delegation plan contract.
- [`data/routing-policy.json`](data/routing-policy.json) — cost/reasoning tiers and budget estimates.
- [`data/usage.example.json`](data/usage.example.json) — remaining-usage snapshot shape.
- [`scripts/select_target.py`](scripts/select_target.py) — deterministic route-only selector with exact pins.
- [`scripts/validate_plan.py`](scripts/validate_plan.py) — plan validator.
- [`scripts/outcome_ledger.py`](scripts/outcome_ledger.py) — append-only outcome records and summaries.
- [`scripts/verify_unit.py`](scripts/verify_unit.py) — Gate 1: declared commands and write scope for one unit.
- [`scripts/record_verification.py`](scripts/record_verification.py) — reviewed verification report to outcome-ledger record.
- [`scripts/fix_brief.py`](scripts/fix_brief.py) — fix-round brief from a failing report and review findings.
- [`scripts/install_bootstrap.py`](scripts/install_bootstrap.py) — adds the delegation check in [`assets/bootstrap.md`](assets/bootstrap.md) to a project's `AGENTS.md`, so this skill loads when it should.
