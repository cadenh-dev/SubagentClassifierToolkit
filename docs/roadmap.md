# Toolkit roadmap

Useful additions beyond the current skills, ordered by leverage. Items marked **implemented** are in this repository.

## Implemented

### 1. Subagent orchestrator
Classify work into units, choose a model/provider, write briefs, deploy, supervise, verify, and integrate.

### 2. Usage-budget guard
User-maintained remaining-usage snapshot; `BUDGET_EXCEEDED` instead of launching an over-budget unit.

### 3. Delegation plan validator
Validate unit ids, dependencies, cycles, integration order, acceptance criteria, and isolation.

### 4. Project classifier
Inspect a repository and generate a project-local tool router. Detects languages, frameworks, engines, build tools, commands, runtimes, and ambiguity:

- Python vs Java
- Blender vs Godot
- Maven vs Gradle
- npm vs pnpm vs yarn
- monorepo/polyglot path routing

## Next

### 5. Capability registry / doctor
One command that reports:

- languages and runtime versions
- installed build tools
- authenticated providers/models
- MCP servers and CLIs
- missing commands the project expects

The project classifier's profile is a good starting point; this adds versions and health checks.

### 6. Outcome ledger / routing feedback
Record per-unit results:

- target and options
- estimated vs actual usage
- duration
- success/failure
- review findings
- retries

Then tune `data/routing-policy.json` from evidence instead of static heuristics. This is the missing feedback loop for cost/reasoning tiers.

### 7. Skill linter
Validate Agent Skills before install:

- `name` matches directory and regex
- description length
- local links exist
- referenced scripts exist and compile
- no secrets or absolute user paths
- frontmatter parses

This would have caught the original `Tool Router` name issue immediately.

### 8. Repo guardrail generator
Generate or update `AGENTS.md` from the project profile:

- build/test/lint commands
- package manager
- architecture boundaries
- files not to edit
- review expectations

Pairs naturally with the project classifier.

### 9. Verification harness
A small runner that:

- takes a unit's acceptance commands
- runs them in a clean environment
- captures logs and exit codes
- compares changed files with the declared write scope
- returns a pass/fail verdict

This makes "verify, don't trust" executable instead of a checklist.

### 10. Worktree / PR janitor
Find stale worktrees, orphan branches, unlinked PRs, and abandoned delegated tasks. Offer cleanup only after confirmation; never delete unmerged work silently.

### 11. Benchmark runner
Run the same task on multiple providers/models with a fixed brief, then compare:

- test pass rate
- review findings
- duration
- usage
- diff size

The output feeds the routing policy.

### 12. Decision journal
Append-only record of:

- task classification
- selected target and why
- alternatives rejected
- budget snapshot
- outcome

Useful for debugging "why did it pick Cursor for that?" and for auditing cost.

### 13. Secret-safe bootstrap
A preflight that checks required credentials through `request_secret` and never writes values to the repo or logs.

### 14. Compatibility matrix
Provider/model/option matrix with known constraints:

- tool calling
- image input
- context limits
- max output
- streaming
- structured output
- rate-limit behavior

Read from live capabilities where possible; keep only stable constraints locally.

### 15. Multi-repo orchestrator
For work that spans repositories:

- one unit per repo/worktree
- cross-repo dependency tracking
- integration branch and PR stack
- no child-owned PR watches

## Not worth building yet

- A fully automatic model benchmark that runs without human review.
- A universal language detector that parses every build system. The project classifier's conservative heuristic is enough until a real monorepo needs more.
- Automatic usage scraping from private app caches. Use a snapshot or a stable API; do not make the skill depend on internal file formats.
