# Multi-repository plans

Define each checkout under top-level `repos` with its canonical remote and base branch. Give every implementation unit with writes exactly one `repo`; write scopes are relative to that repository. Units in separate repositories have independent worktrees and path scopes.

Use `integration.integration_branch` for a shared integration branch when needed. `integration.stack` is an ordered list of `{repo, units, pr_base}` entries. Put dependencies before consumers across repositories. `pr_base` must name that repository's base/integration branch or a branch produced by an earlier stack entry (represented by its unit id). The parent owns integration and PR coordination; children must not watch or babysit PRs.

`scripts/verify_unit.py` can verify a unit's checkout with `--repo`; for repo-assigned units it checks that `origin` points to the declared remote, accepting GitHub SSH and HTTPS spellings as equivalent.
