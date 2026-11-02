# Operations: launch, supervise, verify, integrate

This is the long form of Phases 5–7 in `SKILL.md`.

## 1. Choose the deployment mechanism

| Need | Mechanism |
|---|---|
| Cross-provider model | `delegate_task` with `target` |
| Non-default model options | `delegate_task` with `target.options` |
| Separate checkout or PR | `delegate_task` with `isolation: "worktree"` |
| Background work | `delegate_task` with `mode: "async"` |
| Read-only research/review | `delegate_task` with `isolation: "shared"` |
| Same provider/model/settings and finishes in this turn | Native `subagent` tool, if available |
| User-requested new top-level thread | `t3_thread_launch`, not a subagent |

Do not use `t3_thread_launch` as a subagent. It creates a top-level thread, not a child tied to this task.

## 2. Isolation

### Shared

Use when the child is read-only or the work is small and coordinated:

- research
- review
- read-only investigation
- one implementation child when no other child is writing

Risks: two shared children can edit the same files and race. If more than one child writes, switch to worktrees.

### Worktree

Use for:

- implementation
- tests that create or modify files
- any child that will open its own PR
- parallel units with overlapping adjacent code

Rules:

- A worktree starts from the parent commit. **Uncommitted parent edits are not copied.** Commit or stash before launching if the child needs them.
- Give each child a distinct branch name.
- The parent integrates the worktrees; do not assume the children can see each other's changes.
- If two units must share a change, sequence them or put both in one worktree.

## 3. Wait mode

- `mode: "async"` is the default. It lets the parent continue and wakes it on completion.
- `mode: "wait"` only when this turn cannot proceed without the result.
- A timeout does not cancel the child. Do not launch a duplicate because a wait timed out.
- A question or approval ends a wait. Use `t3_pending_request` on `childThreadId` to answer a question; approvals must be granted by the user in the UI.

## 4. Concurrency

Start with the smallest number that exposes real parallelism:

| Work | Suggested starting concurrency |
|---|---|
| Implementation units | 2–4 |
| Research questions | 3–6 |
| Reviews | 1–2 |
| Test environments | 2–4 |
| Max allowed by `maxBatchThreads` | Do not use it just because it exists |

More children mean more token burn, more review, and more merge conflicts. The parent is the bottleneck; do not outsource integration.

## 5. A supervision table

Keep a table in the parent's working notes:

| Unit | Task id | Child thread | Target | Isolation | Status | PR |
|---|---|---|---|---|---|---|
| api-auth | task_… | … | codex:gpt-6.1-sol | worktree | running | — |
| web-auth | task_… | … | opencode:… | worktree | running | — |
| review | — | — | … | shared | blocked on both | — |

Update it on each wake. Do not poll.

## 6. Handling child events

### Completion

Call `task_status` with the `taskId`. Read:

- `status` — completed, failed, cancelled, interrupted.
- `workState` — `working`, `waiting_for_children`, `result_available`. A finished turn with live child work is not done.
- `summary` — final result.
- `hasPendingQuestion` / `pendingApproval`.
- `latestTerminalStatus`.

Then read the child thread with `t3_thread_read` if you need the full result. Reading marks it delivered.

### Question

Call `t3_pending_request` with `action: "list"`, `threadId: childThreadId`, then `action: "read"` and `action: "respond"`. Answers are for questions, not permission approvals.

If the question invalidates the unit, cancel the task instead of answering.

### Failure

1. Read the error and the child's last output.
2. Decide: narrow the brief, raise the model, or cancel.
3. Retry once with a corrected brief. Do not respawn the same brief repeatedly.
4. If the failure is environmental, fix the environment in the parent and relaunch.

### Rate limit

Mark the target cooling down. Choose the next eligible target. Do not immediately retry the same model; a 429 usually means wait, not retry.

### Wrong scope

Reject or revert out-of-scope files. Do not merge a child that ignored its write scope. If the scope was wrong, rewrite the brief and relaunch.

## 7. PR handling

- The child opens the PR in its worktree. Its last line must be:

  ```text
  PR <full URL>, head <SHA>, checks and reviews <state>.
  ```

- The parent calls `link_pull_request` for each PR it owns, as soon as the PR exists. One call per PR, full URL; do not batch them.
- A child never watches a PR, even if the brief says "babysit." The parent watches only when asked or when the remaining work is waiting on checks/reviews.
- If a PR is stacked, describe the base branch and order. Do not rebase a child's branch without telling it.
- If multiple PRs conflict, merge in dependency order and run the full test suite after each merge.

## 8. Integration checklist

Before accepting a unit:

- [ ] Diff limited to the declared write scope.
- [ ] Acceptance criteria met and independently checked.
- [ ] Tests run by the parent or exact child output inspected.
- [ ] No secrets, debug artifacts, or unrelated formatting.
- [ ] Commit message and branch name are sensible.
- [ ] PR base is correct and checks are passing or explained.
- [ ] Risks and follow-ups are recorded.
- [ ] Remaining usage updated if the provider reports actual usage; otherwise the snapshot is marked stale.

Before declaring the whole task done:

- [ ] All units integrated or explicitly dropped.
- [ ] Full suite passes on the integrated result.
- [ ] PRs linked and watched if needed.
- [ ] Final summary includes targets used, units, tests, PRs, and risks.

## 9. Cancellation

Cancel when:

- the unit is obsolete,
- the child is duplicating another unit,
- the child is stuck on a question the user cannot answer,
- the PR was closed or superseded.

Use `task_cancel` with the `taskId`. A finished task keeps its result; cancel does not erase it.

Do not cancel a child just because a wait timed out. It may still be working.

## 10. Security and privacy

- Do not put credentials, tokens, or private keys in a brief.
- Use `request_secret` in the parent; pass a secret reference or a file path to the child only if the tool supports it safely.
- Do not ask a child to read `.env`, `~/.ssh`, cloud credential files, or browser profiles.
- Do not paste production data into a child task. Use a fixture or a redacted sample.
- Review a child's diff for accidental secret material before pushing.

## 11. Anti-patterns

| Anti-pattern | Consequence | Fix |
|---|---|---|
| One subagent per file | Token burn, merge conflicts, no coherence | One unit per outcome |
| Multiple writers in one shared checkout | Races, lost edits | Worktrees or sequence |
| "Look at the repo" brief | Guessing, scope creep | Objective + scope + acceptance |
| Polling `task_status` | Wasted turns, blocked parent | Wait for the completion wake |
| Nested subagents by default | Unbounded fan-out | Child does the work itself |
| Child watches a PR | Duplicate watchers, stale decisions | Parent watches |
| Premium model for mechanical edits | Cost with no quality gain | Cost mode, lower effort |
| Cheap model for a migration | Rework, risk | Raise depth/model |
| Trusting a summary | False completion | Read diff, run tests |
| Max threads because allowed | Review bottleneck | Start with 2–4 |
| Hardcoded model IDs | Breaks when providers change | Read capabilities |
| Treating unknown as available | Failed runs | Filter unknown |

## 12. Minimal parent loop

```text
1. Classify the task. If it is small, do it.
2. Read orchestrator_capabilities.
3. Split into 2–4 units with dependencies and write scopes.
4. Select one target per unit.
5. Write one brief per unit.
6. Launch worktree implementations and shared research/review.
7. Continue local work until a completion wake.
8. On wake: task_status -> read result -> verify diff/tests.
9. Integrate, link PRs, run full suite.
10. Report targets, units, tests, PRs, risks.
```
