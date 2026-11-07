# Safety and finding IDs

Finding IDs are deterministic hashes of the finding kind and its repository-local identity (worktree path, branch, or PR number). Use IDs printed by a recent scan; cleanup validates the identity again at apply time.

Scan reports conditions and suggested actions but performs no cleanup. A worktree can be removed only when clean, unlocked, not current, and its branch has no unique commits relative to the base. Branch deletion uses `git branch -d`, which protects unmerged commits. The janitor does not delete remote refs or close PRs. It lists pull request suggestions only; a person must decide whether to run any suggested `gh` command.
