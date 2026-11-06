# Toolkit roadmap

Useful additions beyond the current skills, ordered by leverage. Items marked **implemented** are in this repository.

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
`scripts/verify_unit.py` runs one unit's declared commands with a bounded timeout and bounded stdout/stderr capture, then compares the committed `base..HEAD` diff against the unit's declared write scope. It reports command failures, timeouts, out-of-scope paths, invalid scopes, and path errors as JSON with an explicit verdict scope and limitations. Commands require `--allow-commands`; shell syntax requires a separate `--shell` opt-in. The harness never treats passing commands as proof of prose acceptance criteria and does not append to the ledger automatically.

## Next

### 8. Capability registry / doctor
One command that reports:

- languages and runtime versions
- installed build tools
- authenticated providers/models
- MCP servers and CLIs
- missing commands the project expects

The project classifier's profile is a good starting point; this adds versions and health checks.

### 9. Repo guardrail generator
Generate or update `AGENTS.md` from the project profile:

- build/test/lint commands
- package manager
- architecture boundaries
- files not to edit
- review expectations

Pairs naturally with the project classifier.

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

Useful for debugging "why did it pick Cursor for that?" and for auditing cost. The outcome ledger covers the result; a decision journal would also capture the rejected alternatives.

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
