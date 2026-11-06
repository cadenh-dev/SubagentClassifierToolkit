# Operations: launch, supervise, verify, integrate

This is the long form of Phases 5–7 in `SKILL.md`.

## 1. Choose the deployment mechanism

Choose in this order:

1. **Native child tool** only when all are true:
   - the child runs on the parent's provider;
   - that provider exposes a native child mechanism;
   - the child needs no target, model, or option override;
   - the work finishes in this turn.
2. **`delegate_task`** otherwise:
   - another provider;
   - any requested target or options;
   - a worktree or separate checkout;
   - background/async work;
   - work that will outlive this turn.
3. **`t3_thread_launch`** only when the user explicitly asks for a new top-level thread.

Do not infer native support from `features.appOwnedSubagents`; that field describes app support, not a callable tool. Do not use `t3_thread_launch` as a subagent: it creates a top-level thread, not a child tied to this task.

## 2. Isolation

### Shared

Shared isolation is **read-only**:

- research
- review
- read-only investigation

Any child that writes — including a single implementation child — uses a worktree. A shared writer can mix edits into the parent checkout and is unsafe when the parent has uncommitted work. A direct instruction to edit the parent checkout is an explicit exception: confirm the scope and risk first, and never infer it from "use subagents."

Risks: two shared children can edit the same files and race. If any child writes, use worktrees.

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

More children mean more token burn, more review, and more merge conflicts. The parent is the bottleneck; keep integration in the parent unless parent writes are forbidden, in which case delegate integration explicitly as its own worktree unit.

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

After launching async work, continue independent local work; when nothing remains, end the turn. When a wake arrives, follow its `next` instruction. Call `task_status` only when the wake asks for status detail or the result is otherwise unread; if the wake reports completion or says to end, process the result and end the turn. Do not poll, sleep, schedule a wakeup, or set a watcher to wait for a child.

A status read returns:

- `status` — completed, failed, cancelled, interrupted.
- `workState` — `working`, `waiting_for_children`, `result_available`. A finished turn with live child work is not done.
- `summary` — final result.
- `hasPendingQuestion` / `pendingApproval`.
- `latestTerminalStatus`.

Then read the child thread with `t3_thread_read` if you need the full result. Reading marks it delivered.

### Review recheck

A new review or recheck is a new `delegate_task`, never a message to a finished reviewer thread. The recheck brief includes:

- the prior findings with their status;
- the fix commit and diff range;
- the original acceptance criteria;
- the same write scope (normally none).

This keeps the recheck independent and prevents stale context from being mistaken for a fresh review.

### Question

Call `t3_pending_request` with `action: "list"`, `threadId: childThreadId`, then `action: "read"` and `action: "respond"`. Answers are for questions, not permission approvals.

If the question invalidates the unit, cancel the task instead of answering.

### Failure

1. Read the error and the child's last output.
2. Decide: narrow the brief, raise the model, or cancel.
3. Retry once with a corrected brief. Do not respawn the same brief repeatedly.
4. If the failure is environmental, fix the environment in the parent and relaunch.

### Rate limit

Mark the target cooling down. If it was user-pinned, stop and report `UNAVAILABLE`; otherwise choose the next eligible target. Do not immediately retry the same model; a 429 usually means wait, not retry.

### Authentication failure

A 401, 403, or sign-in prompt is not the same as an unavailable target.

- Report `authentication required for <provider>`.
- Stop that unit; do not silently reroute or describe the target as unavailable.
- Retry only after the user authenticates, or after the user explicitly authorizes a different target.
- If the target was user-pinned, keep it pinned across the retry.

### Wrong scope

Reject or revert out-of-scope files. Do not merge a child that ignored its write scope. If the scope was wrong, rewrite the brief and relaunch.

## 7. PR handling

- The child opens the PR in its worktree. Its last line must be:

  ```text
  PR <full URL>, head <SHA>, checks and reviews <state>.
  ```

- The parent links each PR it owns as soon as the PR exists. Use `link_pull_request` for one PR or `link_pull_requests` for several; every entry is a full URL.
- A child never watches a PR, even if the brief says "babysit." The parent watches only when asked or when the remaining work is waiting on checks/reviews.
- If a PR is stacked, describe the base branch and order. Do not rebase a child's branch without telling it.
- If multiple PRs conflict, the parent or a delegated integration unit merges in dependency order and runs the full test suite after each merge.

## 8. Verification harness

`scripts/verify_unit.py` makes the two machine-checkable parts of Phase 7
repeatable:

```bash
python3 scripts/verify_unit.py \
  --plan plan.json --unit api-auth \
  --repo /work/tree --base 4f2c1a \
  --timeout 600 --allow-commands
```

It runs the selected unit's declared `commands` in the repository and compares
the committed diff `base..HEAD` with that unit's `write_scope`.

What it does:

1. Loads the plan once and runs the shipped `validate_plan.py` rules against
   that same parsed object, then strictly checks the selected unit.
2. Resolves the repository path to its real path and requires it to be the git
   worktree top level. It rejects redirected `.git` metadata. For linked
   worktrees, pass both trusted paths `--expected-git-dir` and
   `--expected-git-common-dir`. Detached `HEAD` is supported.
3. Resolves `--base` to a commit and reads changed paths with
   `git diff --name-only -z --no-renames <base> <head>`. External diff and
   textconv drivers are disabled.
4. Matches each changed path against `write_scope`: literal entries name a file
   or directory subtree; in glob entries, `*`, `?`, and character classes stay
   within one path segment, while `**` crosses directory levels. Absolute
   scopes, drive paths, and `..` traversal are invalid scopes.
5. Runs commands sequentially with `cwd` set to the resolved repository. Each
   command has its own timeout and bounded stdout/stderr capture.

Safety and trust:

- **Commands are trusted code.** With `--allow-commands` they execute with the
  caller's privileges, inherit the caller's environment, and can modify the
  working tree. The harness does not sandbox them, reset, clean, or discard
  changes. Git checks ignore inherited `GIT_*` overrides and disable
  `core.fsmonitor` so repository hooks cannot execute during preflight.
- Without `--allow-commands` execution is refused and the verdict is `blocked`.
- Without `--shell`, commands are parsed as an argv vector and run without a
  shell. Unquoted shell operators are refused. Use `--shell` only for a trusted
  plan whose commands genuinely need shell syntax.
- The harness never reads credential files, `.env` files, or secret stores. It
  reads only the plan, git metadata, and output from commands it was explicitly
  allowed to run.
- The JSON includes plan text, command strings, paths, and captured output, which
  may contain sensitive data. Keep reports local and do not paste raw reports
  into a shared ledger.

Output and limits:

- JSON contains `verdict`, command results (`status`, exit code, timeout,
  duration, bounded stdout/stderr, truncation flags), `changed_paths`,
  `scope_violations`, `invalid_write_scope`, `path_errors`, total duration,
  `verdict_scope`, and `limitations`.
- `pass` means only that the declared commands passed and the committed
  `base..HEAD` paths are in scope. Acceptance prose is not machine-executable
  and is never marked verified. A unit with no commands is `unverified`, not
  `pass`.
- Only committed changes in the initial `base..HEAD` snapshot are compared.
  The harness reports tracked/untracked state before and after commands, and
  fails if a command moves `HEAD`.
- Exit codes: `0` pass, `1` fail/unverified, `2` error/blocked.
- The harness never writes to the outcome ledger by itself. After reviewing the
  report, use `record_verification.py` to carry the unit, role, target, options,
  isolation, and mode into a ledger record. The caller must provide depth,
  outcome, verification level, retry count, and review-finding count. A
  `success` outcome also requires `--acceptance-reviewed` and a `pass` report.
  Usage is left unknown unless supplied. The verification verdict is evidence
  for the parent, not an automatic acceptance gate.

```bash
python3 scripts/record_verification.py --report verify.json \
  --depth 3 --outcome partial --verification parent \
  --retries 0 --review-findings 1
```

## 9. Integration checklist

Before accepting a unit:

- [ ] Diff limited to the declared write scope.
- [ ] Acceptance criteria met and independently checked.
- [ ] Tests run by the parent or a delegated integrator, or exact child output inspected.
- [ ] No secrets, debug artifacts, or unrelated formatting.
- [ ] Commit message and branch name are sensible.
- [ ] PR base is correct and checks are passing or explained.
- [ ] Risks and follow-ups are recorded.
- [ ] Remaining usage updated if the provider reports actual usage; otherwise the snapshot is marked stale.
- [ ] Outcome record appended with `verification: "parent"` only if the parent actually verified the result.
- [ ] Any user-named target was used as requested, or the conflict was reported instead of silently substituted.

Before declaring the whole task done:

- [ ] All units integrated or explicitly dropped.
- [ ] Full suite passes on the integrated result.
- [ ] PRs linked and watched if needed.
- [ ] Final summary includes targets used, units, tests, PRs, and risks.

## 10. Cancellation

Cancel when:

- the unit is obsolete,
- the child is duplicating another unit,
- the child is stuck on a question the user cannot answer,
- the PR was closed or superseded.

Use `task_cancel` with the `taskId`. A finished task keeps its result; cancel does not erase it.

Do not cancel a child just because a wait timed out. It may still be working.

## 11. Security and privacy

- Do not put credentials, tokens, or private keys in a brief.
- Use `request_secret` in the parent; pass a secret reference or a file path to the child only if the tool supports it safely.
- Do not ask a child to read `.env`, `~/.ssh`, cloud credential files, or browser profiles.
- Do not paste production data into a child task. Use a fixture or a redacted sample.
- Review a child's diff for accidental secret material before pushing.

## 12. Anti-patterns

| Anti-pattern | Consequence | Fix |
|---|---|---|
| One subagent per file | Token burn, merge conflicts, no coherence | One unit per outcome |
| Multiple writers in one shared checkout | Races, lost edits | Worktrees or sequence |
| "Look at the repo" brief | Guessing, scope creep | Objective + scope + acceptance |
| Polling `task_status` | Wasted turns, blocked parent | Wait for the completion wake and follow its `next` |
| Sending a recheck to a finished reviewer | Stale context, no fresh review | New `delegate_task` with findings and fixes |
| Rerouting a user-pinned target silently | User gets a different model | Stop and report the exact reason |
| Nested subagents by default | Unbounded fan-out | Child does the work itself |
| Child watches a PR | Duplicate watchers, stale decisions | Parent watches |
| Premium model for mechanical edits | Cost with no quality gain | Cost mode, lower effort |
| Cheap model for a migration | Rework, risk | Raise depth/model |
| Trusting a summary | False completion | Read diff, run tests |
| Max threads because allowed | Review bottleneck | Start with 2–4 |
| Hardcoded model IDs | Breaks when providers change | Read capabilities |
| Treating unknown as available | Failed runs | Filter unknown |

## 13. Minimal parent loop

```text
1. Classify the task. If it is small and parent execution is allowed, do it.
2. Read orchestrator_capabilities once for this thread.
3. Split into 2–4 units with dependencies and write scopes.
4. Select one target per unit, honoring any user pin exactly.
5. Write one brief per unit.
6. Launch worktree implementations and shared read-only research/review.
7. Continue independent work; end the turn when nothing remains; do not poll.
8. On wake: follow next; read status/result if directed; verify diff/tests.
9. Integrate, link PRs, run full suite. Delegate integration if parent writes are forbidden.
10. Record the outcome and report targets, units, tests, PRs, risks.
```
