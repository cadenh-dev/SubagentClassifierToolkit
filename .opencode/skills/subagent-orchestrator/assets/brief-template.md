# Subagent brief

Copy this into the `task` field of `delegate_task` or the native subagent tool. Replace every `<...>`.

```text
ROLE: <research | design | implementation | test | review | general>
OBJECTIVE: <one sentence>

CONTEXT
- Repository: <abs path or URL>
- Base branch: <branch> at <commit SHA>
- Read first: <AGENTS.md / design doc / failing test>
- Environment: <runtime, package manager, services>
- Constraints: <compatibility, performance, API contracts>

WRITE SCOPE
- <path/**> only.
- <or: none>

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
- Do the work yourself. Do not start subagents unless this brief says so.
- Use the checkout and isolation the parent provides; do not create a worktree or checkout.
- Do not watch a PR. The parent owns the watch.
- Do not modify files outside the write scope.
- Do not touch secrets or credentials.
- Do not request approval for work this brief already authorizes; ask only when a decision or permission is genuinely missing.
- If you create or update a PR, make your last line: `PR <full URL>, head <SHA>, checks and reviews <state>.`
- If blocked, ask one specific question with the options you considered.
```
