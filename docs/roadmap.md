# Toolkit roadmap

Useful additions beyond the current skills, ordered by leverage. All items below are implemented in this repository.

## Implemented

### 1. Subagent orchestrator
Classify work into units, choose a model/provider, write briefs, deploy, supervise, verify, and integrate. Explicit user pins and parent-never-codes constraints are handled as hard constraints.

### 2. Usage-budget guard
User-maintained remaining-usage snapshot; `BUDGET_EXCEEDED` instead of launching an over-budget unit. Pinned targets stop rather than falling back.

### 3. Delegation plan validator
Validate unit ids, dependencies, cycles, integration order, acceptance criteria, isolation, and overlapping shared writes.

### 4. Project classifier
Inspect a repository and generate a project-local tool router. Detects languages, frameworks, engines, build tools, commands, runtimes, and ambiguity:

- competing languages
- competing engines/tools
- Maven vs Gradle
- npm vs pnpm vs yarn
- monorepo/polyglot path routing

Refreshes preserve unrelated files and refuse unrecognized destinations. Python runtime probes distinguish a working interpreter from a broken PATH alias.

### 5. Outcome ledger / routing feedback
Append-only per-unit records:

- target and options
- estimated vs actual usage
- duration
- success/failure
- review findings
- retries
- parent-verified vs child-claimed

`summarize` groups comparable runs. It never edits the routing policy automatically.

### 6. Skill linter
Deterministic, read-only checks for Agent Skills:

- `name` matches directory and regex
- description length
- local links exist
- referenced scripts compile
- no high-confidence secrets or absolute user paths
- supported frontmatter subset parses

### 7. Verification harness
`scripts/verify_unit.py` runs one unit's declared commands with a bounded timeout and bounded stdout/stderr capture, then compares the committed `base..HEAD` diff against the unit's declared write scope. It reports command failures, timeouts, out-of-scope paths, invalid scopes, and path errors as JSON with an explicit verdict scope and limitations. Commands require `--allow-commands`; shell syntax requires a separate `--shell` opt-in. The harness never treats passing commands as proof of prose acceptance criteria and does not append to the ledger automatically. After parent review, `scripts/record_verification.py` transfers plan metadata to the ledger while requiring the caller to supply depth, outcome, verification level, retries, and review-findings count.

### 8. Capability registry / doctor
`toolkit-doctor` reports runtimes and versions, agent CLIs, `gh` auth, git remote reachability (with an HTTPS hint when SSH fails but `gh` is authenticated), ahead/behind vs upstream, MCP server names (never values), missing commands a project profile expects, and installed-skill drift.

### 9. Repo guardrail generator
`repo-guardrails` writes a marked `AGENTS.md` section from a project-classifier profile plus overrides (do-not-edit paths, boundaries, review expectations). Content outside the markers is preserved byte-for-byte; `--check` reports drift without writing.

### 10. Worktree / PR janitor
`worktree-janitor scan` is read-only and lists stale worktrees, merged or orphaned branches, and stale PRs with stable ids. `clean --apply --id …` re-checks each item and refuses dirty, unmerged, locked, current, or main worktrees; it never force-removes, never uses `branch -D`, and never touches remote branches or PRs.

### 11. Benchmark runner
`benchmark-runner` generates a validated plan with one isolated unit per target, scores each with `verify_unit.py`, compares pass rate, scope, diff size, duration, usage, and review findings, and records only human-reviewed units to the ledger. It never edits the routing policy.

### 12. Decision journal
`scripts/decision_journal.py` appends the classification, selected target, rejected alternatives with reasons (ranking or budget), budget snapshot, and constraints; `join` links decisions to ledger outcomes. `select_target.py --json` now emits `rejected_candidates`.

### 13. Secret-safe bootstrap
`secret-preflight` checks a credential manifest (env presence, file existence and permissions without reading, opt-in `--allow-commands` checks) and scans tracked files for secrets, reporting location and pattern only. Missing secrets are requested through the host's `request_secret`, never pasted in chat.

### 14. Compatibility matrix
`compat-matrix` joins live `orchestrator_capabilities` output with sourced local constraints (context, output, tools, images, structured output, streaming), filters eligible targets, treats `unknown` as not satisfying a requirement, and lists unverified or stale entries.

### 15. Multi-repo orchestrator
Plans accept a `repos` map, a per-unit `repo`, and an `integration.stack`. The validator checks per-repo scope overlap, cross-repo cycles, stack order and PR bases, and forbids child-owned PR watches; `verify_unit.py` checks the worktree's origin matches the unit's repo.

### 16. Skill-text evals
`evals/run_skill_evals.py` sends pressure scenarios (pinned target over budget, child claims success while tests fail, rate-limited pin, no eligible coder, and others) to any agent CLI with and without the skill, and grades the JSON decision deterministically. A trigger suite checks whether the skill loads at all. CI checks the grader and the scenarios' rule anchors without calling a model.

### 17. Fix loop and bootstrap
Phase 7 runs three gates per unit (machine check, spec check, quality review). Failures go to a fix round built by `scripts/fix_brief.py`, with a two-round limit before escalation. `scripts/install_bootstrap.py` adds a delegation check to a project's `AGENTS.md` so the skill loads without the task wording having to match its description.
## Not worth building yet

- A fully automatic model benchmark that runs without human review.
- A universal language detector that parses every build system. The project classifier's conservative heuristic is enough until a real monorepo needs more.
- Automatic usage scraping from private app caches. Use a snapshot or a stable API; do not make the skill depend on internal file formats.
