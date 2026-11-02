# Briefs: self-contained instructions for subagents

A delegate starts with **no parent history**. If it is not in the brief, it does not exist.

## The contract

Every brief must answer:

1. **Role** — what kind of work is this?
2. **Objective** — one sentence, one outcome.
3. **Context** — repository, branch, base commit, relevant docs, constraints.
4. **Write scope** — exactly what may change; empty for read-only work.
5. **Do not touch** — adjacent areas that are out of scope.
6. **Acceptance** — observable criteria the parent can verify.
7. **Commands** — the exact checks to run.
8. **Deliverable** — commit/push/PR, patch, memo, or report.
9. **Reporting** — what to return, in what shape.
10. **Escalation** — when to ask instead of guessing.

## Copy-paste template

```text
ROLE: implementation
OBJECTIVE: <one sentence>

CONTEXT
- Repository: <abs path or URL>
- Base branch: <branch> at <commit SHA>
- Read first: <AGENTS.md / design doc / failing test>
- Environment: <runtime, package manager, services>
- Constraints: <compatibility, performance, API contracts>

WRITE SCOPE
- <path/**> only.

DO NOT TOUCH
- <path/**>
- Do not reformat, rename, or clean up unrelated code.

ACCEPTANCE
- <observable criterion 1>
- <observable criterion 2>
- <command> passes.

COMMANDS
- <setup>
- <test/lint/build>

DELIVERABLE
- <commit + push + PR against main / patch / memo>
- Include the PR URL, head SHA, and check status.

REPORT
- Changed files
- Commands run and results
- Risks or follow-ups
- Open questions

RULES
- Do not start subagents unless this brief explicitly says so.
- Do not watch a PR.
- Do not touch secrets or credentials.
- If blocked, ask one specific question with the options you considered.
```

## Role templates

### Research

```text
ROLE: research
OBJECTIVE: Determine <question> and recommend <decision>.

WRITE SCOPE: none.

QUESTIONS
1. <question 1>
2. <question 2>

SOURCES
- Prefer <official docs / source code / benchmarks>.
- Cite file paths or URLs for every claim.

DELIVERABLE
- A short comparison table: option, mechanism, strengths, weaknesses, operational cost, failure modes.
- A recommendation with confidence and the evidence behind it.

REPORT
- Findings, citations, uncertainty, recommendation.
```

Research agents must not edit files. If they need to write a scratch file, keep it outside the repo or declare a scratch path.

### Design

```text
ROLE: design
OBJECTIVE: Produce an implementation plan for <change>.

WRITE SCOPE: none, unless explicitly asked for a design doc.

DELIVERABLE
- Proposed modules and interfaces.
- Data flow and migration steps.
- Failure modes and rollback.
- Tests and observability.
- Open questions.

RULES
- Do not implement unless the brief says "implement".
- Prefer the smallest design that meets the acceptance criteria.
```

### Implementation

```text
ROLE: implementation
OBJECTIVE: <change> without changing <contract>.

WRITE SCOPE
- <files/directories>.

ACCEPTANCE
- <behavior>
- <existing tests pass>
- <new test covers the failure>

DELIVERABLE
- Branch <name>, commit, push, PR against <base>.
- Report the PR URL, head SHA, and check status.

RULES
- Keep the diff limited to the write scope.
- Do not fix unrelated failures.
- If the acceptance criteria are impossible, stop and explain why.
```

### Test

```text
ROLE: test
OBJECTIVE: Reproduce <bug> and add the smallest test that fails before the fix.

WRITE SCOPE
- <test paths> only.

DELIVERABLE
- A failing test, exact reproduction command, and the expected failure.
- Do not change production code unless explicitly asked.

REPORT
- Command, output, root-cause hypothesis, whether the test is deterministic.
```

### Review

```text
ROLE: review
OBJECTIVE: Review <PR/diff/commit> for correctness, risk, and missing tests.

WRITE SCOPE: none.

INPUTS
- PR: <URL>
- Diff: <base>..<head>
- Context: <design doc / issue>

DELIVERABLE
- Findings only, ordered by severity.
- For each: file:line, what is wrong, why it matters, and a concrete fix.
- Distinguish confirmed bugs from questions and style nits.
- Do not make code changes unless the brief explicitly asks for a patch.

RULES
- Verify claims against the diff; do not trust the author's summary.
- Call out missing tests and unhandled failure modes.
- If there are no findings, say so explicitly and list what you checked.
```

### General

Use `general` only when the task does not fit the other roles. State whether writes are allowed and give the same acceptance/reporting structure.

## Writing acceptance criteria

Good:

- "`pnpm test --filter auth` passes."
- "A replay of a rotated refresh token returns 401."
- "The new endpoint returns 400 for a missing `id` and 404 for an unknown `id`."
- "The PR diff touches only `services/auth/**` and `tests/auth/**`."

Bad:

- "Code is clean."
- "Looks good."
- "Improve performance."
- "Make it robust."

If the parent cannot verify it, the child cannot be accepted.

## Context hygiene

- Include the **base commit**. A worktree is created from the parent commit; uncommitted parent edits are not copied.
- Include the **package manager and runtime**. Do not make the child rediscover them.
- Include the **failing test or reproduction command** when there is one.
- Link the design doc or issue; do not paste the entire document unless the child has no file access.
- State the **non-goals**. "Do not refactor" is a valid acceptance constraint.
- Keep secrets out. Use `request_secret` in the parent and pass a path or reference, never a token value.

## Reporting contract

Every implementation child should return:

```text
Summary: <what changed>
Files: <list>
Commands: <command -> result>
Tests: <passed/failed/skipped, with names>
Risks: <known risks and follow-ups>
PR: <URL>, head <SHA>, checks <state>
```

Every review child should return:

```text
Verdict: <approve / request changes / no findings>
Findings:
- <severity> <file>:<line> <issue> -> <fix>
Checked: <what was inspected>
Open questions: <list>
```

## Good and bad examples

### Good implementation brief

```text
ROLE: implementation
OBJECTIVE: Add refresh-token rotation to the auth service.

CONTEXT
- Repo /work/app, branch main at 4f2c1a.
- Read AGENTS.md and services/auth/README.md.
- Runtime Node 22, pnpm 9.

WRITE SCOPE
- services/auth/**
- tests/auth/**

DO NOT TOUCH
- web/**, infra/**, shared/**

ACCEPTANCE
- A used refresh token cannot be replayed.
- Rotation preserves the session for a valid token.
- `pnpm test --filter auth` passes.
- New tests cover replay, expiry, and concurrent refresh.

DELIVERABLE
- Branch auth-refresh-rotation, commit, push, PR against main.
- Report PR URL, head SHA, and check status.
```

### Bad implementation brief

```text
Fix auth. Look at the repo. Make it better.
```

Why it fails: no objective, no scope, no acceptance, no base commit, no reporting contract. The child will guess, and the parent will have to redo the work.

### Good review brief

```text
ROLE: review
OBJECTIVE: Review PR #42 for correctness and missing tests.

INPUTS
- PR: https://github.com/org/repo/pull/42
- Base: main at 4f2c1a
- Design: docs/auth-rotation.md

WRITE SCOPE: none.

DELIVERABLE
- Findings by severity with file:line and concrete fixes.
- Check replay, expiry, concurrent refresh, and error paths.
- If no findings, list what you checked.

RULES
- Do not edit the branch.
- Do not trust the PR description; read the diff.
```

## Child rules

Include these in every brief unless the task explicitly says otherwise:

- Do the work yourself. Do not start subagents.
- Do not watch PRs. The parent owns the watch.
- Do not modify files outside the write scope.
- Do not paste secrets or credentials into your report.
- If blocked, ask one specific question with the options you considered.
