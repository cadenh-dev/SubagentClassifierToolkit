---
name: benchmark-runner
description: Compare multiple provider and model targets on one identical task brief using isolated delegation worktrees, verification reports, human review, and the outcome ledger.
---

# Benchmark Runner

This skill generates plans and scores results; the parent launches each unit through the host `delegate_task` tool. Python does not launch agents. Every target gets the same objective, acceptance criteria, commands, and write scope, in a separate worktree and branch.

## Human reviewed flow

1. `plan` creates a valid delegation plan with a benchmark id and distinct unit ids/branch instructions.
2. The parent launches each unit via `delegate_task` using its target and `isolation: worktree`.
3. `score` runs the orchestrator's `verify_unit.py` against each returned worktree and base SHA.
4. The parent reviews each diff and acceptance criteria.
5. `compare` presents verification, scope, findings, duration, usage, and diff size. Rows are unreviewed unless the parent supplies `--reviewed <id>`.
6. `record` calls `record_verification.py` only for an explicitly reviewed unit. Success recording also requires the recorder's acceptance-review confirmation.

No unattended benchmark is complete: verification checks declared commands and write scope, while review findings and acceptance prose require a human. The runner never changes `routing-policy.json`.

## Commands

```bash
python3 scripts/benchmark.py plan --task task.json --targets targets.json --out bench-plan.json
python3 scripts/benchmark.py score --plan bench-plan.json --unit UNIT --repo /work/tree --base SHA --allow-commands
python3 scripts/benchmark.py compare --plan bench-plan.json --reviewed UNIT --review-findings UNIT=1 --duration UNIT=120 --usage UNIT=500
python3 scripts/benchmark.py record --plan bench-plan.json --unit UNIT --reviewed UNIT --depth 3 --outcome partial --verification parent --retries 0 --review-findings 1
```

Usage remains unknown unless supplied. See [`references/flow.md`](references/flow.md) and the orchestrator's `references/outcomes.md` for ledger semantics.
