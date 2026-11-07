---
name: worktree-janitor
description: Find stale Git worktrees, local branches, and pull requests, then offer safe cleanup after explicit confirmation. Use when reviewing repository worktree or branch hygiene.
---

# Worktree Janitor

Run `scripts/janitor.py scan --repo PATH` to inspect a repository. Scanning is strictly read-only. Each finding has a stable ID suitable for a later clean command.

```sh
python3 scripts/janitor.py scan --repo . --json
python3 scripts/janitor.py clean --repo . --id <finding-id>
python3 scripts/janitor.py clean --repo . --apply --id <finding-id>
```

Review the scan output and explicitly confirm the IDs to clean. `clean` without `--apply` only prints proposed operations. Apply rechecks state and refuses dirty, locked, current, or unmerged/unpushed work. It uses ordinary `git worktree remove` and `git branch -d`; it never force-deletes, changes remote branches, or closes pull requests. PR findings only print a suggested human-run `gh` command.

Use `--base REF` and `--stale-days N` to tune comparisons. See [the safety reference](references/safety.md) for ID stability and action rules.
