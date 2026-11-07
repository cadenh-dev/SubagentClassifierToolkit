# SubagentClassifierToolkit

A T3 Code toolkit for deciding **when** to use subagents, **which** provider/model to use, **how** to deploy and supervise them, and **which project tools/languages** they should use.

Eleven skills ship in the box:

- [`subagent-orchestrator`](.opencode/skills/subagent-orchestrator/) — routing, briefs, supervision, verification, multi-repo plans, a decision journal, and an outcome ledger.
- [`project-classifier`](.opencode/skills/project-classifier/) — generates a project-local tool router from repository evidence.
- [`skill-linter`](.opencode/skills/skill-linter/) — read-only packaging checks for Agent Skills.
- [`toolkit-doctor`](.opencode/skills/toolkit-doctor/) — read-only health report: runtimes, agent CLIs, auth, git remote reachability, MCP server names, installed-skill drift.
- [`repo-guardrails`](.opencode/skills/repo-guardrails/) — generates a managed `AGENTS.md` section from a project-classifier profile.
- [`worktree-janitor`](.opencode/skills/worktree-janitor/) — finds stale worktrees, branches, and PRs; cleans up only confirmed, safe items.
- [`benchmark-runner`](.opencode/skills/benchmark-runner/) — runs one fixed brief on several targets and compares verified, human-reviewed results.
- [`secret-preflight`](.opencode/skills/secret-preflight/) — checks required credentials are available without reading values; scans tracked files for leaked secrets.
- [`compat-matrix`](.opencode/skills/compat-matrix/) — joins live provider capabilities with sourced model constraints and filters eligible targets.
- [`offpeak-scheduler`](.opencode/skills/offpeak-scheduler/) — runs DeepSeek work only in DeepSeek's off-peak (discounted) hours and defers it otherwise, with no polling.
- [`session-kickoff`](.opencode/skills/session-kickoff/) — gives the main agent a role at thread start (planner, builder, reviewer, solo), saves it per project, and supplies the start-of-thread rule.

The main skill is [`.opencode/skills/subagent-orchestrator/`](.opencode/skills/subagent-orchestrator/). It is not just a prompt that prints a model name. It is an operating procedure for:

- classifying a task into independent work units,
- selecting the cheapest capable T3 target, or the strongest target when `IGNORE_COST` is set,
- writing self-contained subagent briefs,
- launching delegates with the right role, isolation, and wait mode,
- supervising questions, failures, and cancellations,
- verifying diffs and tests before accepting a child's summary.

It also includes a route-only mode for callers that only want a canonical target.

## Why this exists

Subagents are useful when work is genuinely parallel, cross-provider, isolated, or needs an independent review. They are harmful when they duplicate work, write to the same files, or turn a one-line edit into a token-burning committee.

This toolkit encodes that distinction. The default is **do it yourself** when you leave the approach open. Delegation is opt-in, with a brief and an acceptance test for every unit. An explicit request to delegate, to keep the parent from coding, or to pin a provider/model/settings is a hard constraint the default cannot override.

## Install

The repository is itself a T3/OpenCode project. Clone it and open it, or copy the skill into your project or user skill directory.

Project scope:

```bash
mkdir -p .opencode/skills
cp -R /path/to/SubagentClassifierToolkit/.opencode/skills/* .opencode/skills/
```

User scope:

```bash
cp -R /path/to/SubagentClassifierToolkit/.opencode/skills/* ~/.config/opencode/skills/
```

Skill names match their directories: `subagent-orchestrator`, `project-classifier`, and `skill-linter`.

## Use

Ask for the outcome, not the mechanism:

- `Refactor the auth flow across api/, web/, and db/. Use subagents where it helps.`
- `Delegate this end to end; do not write code in the parent.`
- `Use exactly gpt-6.1-sol with high reasoning for this change.`
- `Compare three caching approaches and recommend one.`
- `Have one agent implement this and a different one review the diff.`
- `Run the test matrix on Linux and macOS in parallel.`
- `Ignore cost and use the strongest available model for this migration.`
- `Route only: which target should handle a multi-file security review?`
- `Generate a project router for this repo.`
- `Lint the skills in this repo.`

The skill will first decide whether delegation is required or justified. If you required it or pinned a target, it honors that constraint or explains honestly why it cannot. If you left the approach open and delegation would not help, it does the work directly.

### Route-only mode

If you explicitly ask for a route, the skill returns exactly one line:

```text
codex:gpt-6.1-sol#reasoningEffort=high,serviceTier=default
```

or:

```text
UNAVAILABLE
```

No Markdown, JSON, backticks, or explanation. The canonical form is:

```text
<providerInstanceId>:<model>[#<option>=<value>,...]
```

Preserve `/`, `.`, `-`, `_`, `:`, `@`, and `#`; they are part of real model IDs.

### Pinned targets

If you name a provider, model, or option, the selector uses it exactly instead of re-ranking it:

```bash
python3 .opencode/skills/subagent-orchestrator/scripts/select_target.py \
  --capabilities caps.json --role review --depth 4 \
  --provider codex --model gpt-6.1-sol --option reasoningEffort=high
```

A pin that is missing, disabled, undeclared, over budget, cooling down, or disallowed by local policy returns `UNAVAILABLE` or `BUDGET_EXCEEDED`. It never falls back to another target or option value.

## Budget and usage left

T3 does not expose a uniform "remaining usage" API for every provider. The skill therefore supports a user-maintained snapshot:

```bash
cp .opencode/skills/subagent-orchestrator/data/usage.example.json \
  .opencode/skills/subagent-orchestrator/data/usage.json
# edit remaining units from your provider/T3 usage screen
```

Then route with a budget check:

```bash
python3 .opencode/skills/subagent-orchestrator/scripts/select_target.py \
  --capabilities caps.json \
  --usage .opencode/skills/subagent-orchestrator/data/usage.json \
  --role implementation --depth 4 --estimate-units 220000
```

If no eligible target fits, the selector returns `BUDGET_EXCEEDED` instead of launching a task that cannot finish. `IGNORE_COST=true` still respects the remaining-usage check. Unknown providers are not treated as unlimited. A user-pinned target that does not fit stops with `BUDGET_EXCEEDED`; it is never silently replaced.

## Outcome ledger

The routing policy starts as a heuristic. The outcome ledger records what actually happened so you can tune it from evidence instead of vibes:

```bash
python3 .opencode/skills/subagent-orchestrator/scripts/outcome_ledger.py record \
  --unit auth-core --role implementation --depth 3 \
  --provider codex --model gpt-6.1-sol \
  --outcome success --verification parent \
  --estimated-units 50000 --actual-units 41200 \
  --duration 615 --review-findings 2

python3 .opencode/skills/subagent-orchestrator/scripts/outcome_ledger.py summarize --verified-only
```

The ledger is append-only JSON Lines in `data/outcomes.jsonl` (git-ignored). It distinguishes a parent-verified success from a child's self-report, keeps unknown usage unknown, and never rewrites the routing policy automatically. See [`references/outcomes.md`](.opencode/skills/subagent-orchestrator/references/outcomes.md).

## Verification harness

A child's summary is a claim. `verify_unit.py` makes the machine-checkable part of "verify, don't trust" executable for one unit:

```bash
python3 .opencode/skills/subagent-orchestrator/scripts/verify_unit.py \
  --plan plan.json --unit api-auth \
  --repo /work/tree --base 4f2c1a \
  --timeout 600 --allow-commands
```

It validates the plan first, runs the unit's declared commands in the repository with a bounded timeout and bounded logs, and compares the committed `base..HEAD` diff against the unit's `write_scope`. Out-of-scope paths, invalid scopes, timeouts, and failed commands produce a failing JSON verdict. A passing verdict says only that the declared commands passed and the committed diff is in scope; acceptance prose is never marked verified.

Commands are trusted code, inherit the caller's environment, and execute with the caller's privileges. Without `--allow-commands` the harness refuses to run anything. Without `--shell` it parses commands as argv and refuses unquoted shell operators; `--shell` is an explicit opt-in. It never reads credential files or secret stores, does not reset or clean the repository, and leaves no log artifacts. After reviewing the report, use `record_verification.py` to append its plan metadata to the ledger alongside the depth, outcome, verification level, retries, and review-findings count you provide. Recording success also requires `--acceptance-reviewed` and a passing report. See [`references/operations.md`](.opencode/skills/subagent-orchestrator/references/operations.md) §8.

```bash
python3 .opencode/skills/subagent-orchestrator/scripts/record_verification.py \
  --report verify.json --depth 3 --outcome partial --verification parent \
  --retries 0 --review-findings 1
```

For linked Git worktrees, pass `--expected-git-dir` and `--expected-git-common-dir` from the trusted parent. Git preflight ignores inherited `GIT_*` overrides and disables repository `core.fsmonitor` hooks. The plan is loaded and validated as one snapshot; the harness reports post-command working-tree state and fails if a command moves `HEAD`.

## Project classifier

The second skill builds a **project-local tool router** from repository evidence. It answers competing language/engine/tool questions and "which test command?" without guessing from the model's preferences.

```bash
python3 .opencode/skills/project-classifier/scripts/build_project_classifier.py --root . --dry-run
python3 .opencode/skills/project-classifier/scripts/build_project_classifier.py \
  --root . --out .opencode/skills/my-project-tool-router
```

The generated skill lives inside the project and includes:

- language/file/manifest/framework evidence,
- installed-vs-used tool distinctions,
- build/test commands,
- generic routing rules for multiple languages, engines, and build tools,
- a machine-readable `data/project-profile.json`,
- an evidence file.

Refresh it after a stack change with `--force`. A refresh overwrites only the managed artifacts (`SKILL.md`, `data/project-profile.json`, `references/evidence.md`); unrelated files in the generated folder are preserved, and an unrecognized destination is refused. Python commands use the first interpreter that passes a fixed version probe, so a broken Windows `python3` alias is reported as `NOT USABLE` rather than baked into the router.

## Skill linter

The third skill is a deterministic, read-only packaging check for Agent Skills:

```bash
python3 .opencode/skills/skill-linter/scripts/lint_skills.py .opencode/skills
python3 .opencode/skills/skill-linter/scripts/lint_skills.py .opencode/skills --strict
```

It validates frontmatter, name/directory agreement, description length, local links, Python helper syntax, high-confidence secrets, and leaked absolute user paths. It never executes inspected code, imports inspected modules, fetches links, or writes files. Exit `0` means clean, `1` means errors (or warnings with `--strict`), and `2` means bad usage.

## Operations skills

```bash
# Health check: tools, auth, remote reachability, MCP servers, installed-skill drift
python3 .opencode/skills/toolkit-doctor/scripts/doctor.py --repo .

# AGENTS.md guardrails from a project-classifier profile (--check for CI drift)
python3 .opencode/skills/repo-guardrails/scripts/generate_agents_md.py \
  --profile path/to/project-profile.json --repo . --check

# Stale worktrees/branches/PRs (read-only), then confirmed cleanup by id
python3 .opencode/skills/worktree-janitor/scripts/janitor.py scan --repo .
python3 .opencode/skills/worktree-janitor/scripts/janitor.py clean --repo . --id wt:abc123 --apply

# Credentials present? (never prints values; commands need --allow-commands)
python3 .opencode/skills/secret-preflight/scripts/preflight.py check --manifest .agent-secrets.json
python3 .opencode/skills/secret-preflight/scripts/preflight.py scan-repo --manifest .agent-secrets.json

# Targets that meet requirements, from a saved orchestrator_capabilities snapshot
python3 .opencode/skills/compat-matrix/scripts/compat_matrix.py filter \
  --capabilities caps.json --require tool_calling,image_input --min-context 400000

# Why was a target chosen? Record and query routing decisions
python3 .opencode/skills/subagent-orchestrator/scripts/decision_journal.py record \
  --from-select selection.json --unit api-auth
python3 .opencode/skills/subagent-orchestrator/scripts/decision_journal.py show --unit api-auth
```

The benchmark runner is a plan generator and scorer; the parent agent launches each unit. Flow: `benchmark.py plan` → launch each unit → `score` → review → `compare` → `record`. See its [SKILL.md](.opencode/skills/benchmark-runner/SKILL.md).

Multi-repo delegation plans add a top-level `repos` map, a per-unit `repo`, and an optional `integration.stack`. See [`references/multi-repo.md`](.opencode/skills/subagent-orchestrator/references/multi-repo.md).

DeepSeek off-peak gating (times shown for America/Phoenix):

```bash
python3 .opencode/skills/offpeak-scheduler/scripts/offpeak.py gate --target deepseek:deepseek-v4.1-flash
# exit 0 = run now, 3 = defer; then print a one-time wake instruction for the host:
python3 .opencode/skills/offpeak-scheduler/scripts/offpeak.py schedule-hint --target deepseek:deepseek-v4.1-flash --host t3
```

The discount applies to DeepSeek's direct API (and OpenRouter's DeepSeek route). OpenCode Go and Zen have no time-of-day pricing, so their rules are opt-in (`--opt-in` or `OFFPEAK_OPT_IN=1`). Window data is unverified research; see the skill's `references/windows.md`.

Thread and project roles:

```bash
python3 .opencode/skills/session-kickoff/scripts/kickoff.py set --preset planner --repo . --agents-md
python3 .opencode/skills/session-kickoff/scripts/kickoff.py brief --repo .      # what the main agent adopts
python3 .opencode/skills/session-kickoff/scripts/kickoff.py onboard --repo .    # new-project checklist
python3 .opencode/skills/session-kickoff/scripts/kickoff.py install-rules --file ~/.claude/CLAUDE.md
```

Resolution order: `<repo>/.agent-role.json` > `~/.config/agent-role/default.json` > built-in `planner`. `install-rules` manages only its marked block in a global instruction file.

The compatibility matrix's local data (`data/constraints.json`) is web research marked `verified: false`; live capabilities always win. Run `compat_matrix.py stale` to list entries that need a human re-check.

## What is in the box

```text
.opencode/skills/subagent-orchestrator/
├── SKILL.md                     # entry point and operating loop
├── data/routing-policy.json     # editable cost/reasoning tiers and budget estimates
├── data/usage.example.json      # user-maintained remaining-usage snapshot
├── data/outcomes.jsonl          # created on first record; git-ignored
├── references/
│   ├── routing.md               # depth, domains, pins, options, budget
│   ├── briefs.md                # role templates, recheck brief, child rules
│   ├── operations.md            # launch, supervise, verify, integrate
│   ├── outcomes.md              # outcome-ledger schema and evidence rules
│   ├── decisions.md             # decision-journal schema
│   └── multi-repo.md            # multi-repo plans and PR stacks
├── assets/
│   ├── brief-template.md        # copy-paste brief
│   ├── plan.example.json        # example delegation plan
│   └── plan.schema.json         # delegation plan contract
└── scripts/
    ├── select_target.py         # deterministic route-only selector
    ├── validate_plan.py         # plan and dependency validator
    ├── verify_unit.py           # bounded command + write-scope verifier
    ├── record_verification.py   # reviewed verification report → ledger record
    ├── decision_journal.py      # why a target was chosen; rejected alternatives
    └── outcome_ledger.py        # append-only outcome records and summaries
.opencode/skills/project-classifier/
├── SKILL.md                     # project-classifier workflow
├── references/detection.md      # detection heuristics and confidence
├── assets/profile.example.json  # profile shape
└── scripts/build_project_classifier.py
.opencode/skills/skill-linter/
├── SKILL.md                     # linter workflow and limits
├── references/checks.md         # diagnostic codes and YAML subset
└── scripts/lint_skills.py       # read-only checker
.opencode/skills/toolkit-doctor/scripts/doctor.py
.opencode/skills/repo-guardrails/scripts/generate_agents_md.py
.opencode/skills/worktree-janitor/scripts/janitor.py
.opencode/skills/benchmark-runner/scripts/benchmark.py
.opencode/skills/secret-preflight/scripts/preflight.py
.opencode/skills/compat-matrix/scripts/compat_matrix.py  # + data/constraints.json
tests/
├── fixtures/                    # capability and usage snapshots
├── test_toolkit.py              # selector and plan-validator tests
├── test_project_classifier.py   # project-classifier tests
├── test_skill_linter.py         # skill-linter tests
├── test_outcome_ledger.py       # outcome-ledger tests
├── test_verify_unit.py          # verification-harness tests
├── test_record_verification.py  # verification-to-ledger bridge tests
└── test_<skill>.py              # one test module per added skill
docs/
├── requirements.md              # requirement-to-implementation mapping
└── roadmap.md                   # other useful toolkit pieces
```

## Design principles

1. **Live capabilities beat cached registries.** T3 already exposes `orchestrator_capabilities`; the skill reads it once per thread before the first delegate instead of pretending to fetch prices and benchmarks in the background. A listing is not proof of authentication or quota.
2. **Availability and budget before cost.** A disabled provider, a rate-limited model, or a unit that does not fit the remaining usage is not a candidate.
3. **Cheapest capable by default for open choices.** `IGNORE_COST=false` prefers an eligible inherited target, then the lowest cost tier that meets the reasoning threshold. A user-pinned provider, model, or option bypasses ranking and is never silently replaced; a pin that conflicts with a hard local-policy gate is reported, not overridden.
4. **Budget before launch.** If a remaining-usage snapshot is available, a unit that does not fit is not launched.
5. **Max reasoning is explicit.** `IGNORE_COST=true` raises the effort and chooses the strongest available target. It does not ignore availability or remaining usage.
6. **One brief per independent unit.** Each child gets a self-contained task, write scope, acceptance criteria, and reporting contract.
7. **Worktree for writes, shared for read-only.** Any child that writes uses a worktree by default; worktrees come from delegate isolation, not shell setup in the brief. A parent-checkout edit requires a direct user instruction and explicit scope confirmation.
8. **Verify, don't trust.** A child's summary is a claim; the parent reads the diff and runs the checks. The outcome ledger records whether verification actually happened.
9. **No nested delegation by default.** A child does the work itself unless the brief explicitly allows otherwise.
10. **User scope wins over defaults.** If the user requires delegation or forbids parent coding, an unavailable target is reported and questioned, not quietly replaced by parent implementation. A user-pinned target is used exactly or reported as unavailable/over-budget/conflicted. Hard local policy gates are surfaced, not silently bypassed.

## Requirements

- T3 Code with the delegation tools:
  - `orchestrator_capabilities`
  - `delegate_task`
  - `task_status`
  - `task_cancel`
  - `t3_thread_read`, `t3_thread_send`, `t3_pending_request`
  - `link_pull_request`, `link_pull_requests`, `watch_pull_request`
  - the native `subagent` tool when the same provider/model/settings can finish in this turn
- Python 3.10+ for the optional scripts. No third-party packages.
- A local editor if you want to adjust `data/routing-policy.json`.

If your runtime does not expose these tools, the classification and brief templates still apply, but replace the T3 calls with your platform's equivalent.

## Development

Run the tests:

```bash
python3 -m unittest discover -s tests -v
```

Validate a delegation plan:

```bash
python3 .opencode/skills/subagent-orchestrator/scripts/validate_plan.py \
  --plan plan.json
```

Select a target from a saved `orchestrator_capabilities` snapshot:

```bash
python3 .opencode/skills/subagent-orchestrator/scripts/select_target.py \
  --capabilities caps.json \
  --role implementation \
  --depth 3
```

Lint every skill after editing:

```bash
python3 .opencode/skills/skill-linter/scripts/lint_skills.py .opencode/skills --strict
```

Record and summarize a run:

```bash
python3 .opencode/skills/subagent-orchestrator/scripts/outcome_ledger.py record \
  --unit example --role review --depth 4 \
  --provider codex --model gpt-6.1-sol \
  --outcome success --verification parent
python3 .opencode/skills/subagent-orchestrator/scripts/outcome_ledger.py summarize
```

The scripts are optional helpers. The skill itself uses the live tools. CI runs the full test suite and the skill linter.

See [`docs/requirements.md`](docs/requirements.md) for the requirement-to-implementation mapping and [`docs/roadmap.md`](docs/roadmap.md) for other useful toolkit pieces.

## Status

Eleven skills: subagent orchestration (with decision journal, verification harness, and multi-repo plans), project toolchain classification, a read-only skill linter, and operations skills for health checks, guardrails, worktree cleanup, benchmarking, secret preflight, model compatibility, DeepSeek off-peak scheduling, and session roles. All roadmap items 1–15 are implemented. The routing policy is a starter heuristic, not a benchmark. Adjust `data/routing-policy.json` to match your own quality and cost measurements, using the outcome ledger and benchmark runner as evidence. The project classifier is conservative by design; low-confidence evidence is reported, not hidden.
