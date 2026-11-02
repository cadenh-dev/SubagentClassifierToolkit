# SubagentClassifierToolkit

A T3 Code skill for deciding **when** to use subagents, **which** provider/model to use, and **how** to deploy, supervise, and integrate them.

The skill is in [`.opencode/skills/subagent-orchestrator/`](.opencode/skills/subagent-orchestrator/). It is not just a prompt that prints a model name. It is an operating procedure for:

- classifying a task into independent work units,
- selecting the cheapest capable T3 target, or the strongest target when `IGNORE_COST` is set,
- writing self-contained subagent briefs,
- launching delegates with the right role, isolation, and wait mode,
- supervising questions, failures, and cancellations,
- verifying diffs and tests before accepting a child's summary.

It also includes a route-only mode for callers that only want a canonical target.

## Why this exists

Subagents are useful when work is genuinely parallel, cross-provider, isolated, or needs an independent review. They are harmful when they duplicate work, write to the same files, or turn a one-line edit into a token-burning committee.

This toolkit encodes that distinction. The default is **do it yourself**. Delegation is opt-in, with a brief and an acceptance test for every unit.

## Install

The repository is itself a T3/OpenCode project. Clone it and open it, or copy the skill into your project or user skill directory.

Project scope:

```bash
mkdir -p .opencode/skills
cp -R /path/to/SubagentClassifierToolkit/.opencode/skills/subagent-orchestrator .opencode/skills/
```

User scope:

```bash
cp -R /path/to/SubagentClassifierToolkit/.opencode/skills/subagent-orchestrator \
  ~/.config/opencode/skills/
```

The skill name is `subagent-orchestrator`; it matches the directory name and the `skill` tool identifier.

## Use

Ask for the outcome, not the mechanism:

- `Refactor the auth flow across api/, web/, and db/. Use subagents where it helps.`
- `Compare three caching approaches and recommend one.`
- `Have one agent implement this and a different one review the diff.`
- `Run the test matrix on Linux and macOS in parallel.`
- `Ignore cost and use the strongest available model for this migration.`
- `Route only: which target should handle a multi-file security review?`

The skill will first decide whether delegation is justified. If it is not, it will do the work directly.

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

If no eligible target fits, the selector returns `BUDGET_EXCEEDED` instead of launching a task that cannot finish. `IGNORE_COST=true` still respects the remaining-usage check. Unknown providers are not treated as unlimited.

## What is in the box

```text
.opencode/skills/subagent-orchestrator/
├── SKILL.md                     # entry point and operating loop
├── data/routing-policy.json     # editable cost/reasoning tiers and budget estimates
├── data/usage.example.json      # user-maintained remaining-usage snapshot
├── references/
│   ├── routing.md               # task depth, domains, model selection, budget
│   ├── briefs.md                # role-specific brief templates
│   └── operations.md            # launch, supervise, verify, integrate
├── assets/
│   ├── brief-template.md        # copy-paste brief
│   └── plan.schema.json         # delegation plan contract
└── scripts/
    ├── select_target.py         # deterministic route-only selector
    └── validate_plan.py         # plan and dependency validator
tests/
└── test_toolkit.py              # CLI tests, no third-party dependencies
docs/
└── requirements.md              # requirement-to-implementation mapping
```

## Design principles

1. **Live capabilities beat cached registries.** T3 already exposes `orchestrator_capabilities`; the skill reads it instead of pretending to fetch model prices and benchmarks in the background.
2. **Availability and budget before cost.** A disabled provider, a rate-limited model, or a unit that does not fit the remaining usage is not a candidate.
3. **Cheapest capable by default.** `IGNORE_COST=false` prefers the inherited target, then the lowest cost tier that meets the reasoning threshold.
4. **Budget before launch.** If a remaining-usage snapshot is available, a unit that does not fit is not launched.
5. **Max reasoning is explicit.** `IGNORE_COST=true` raises the effort and chooses the strongest available target. It does not ignore availability or remaining usage.
6. **One brief per independent unit.** Each child gets a self-contained task, write scope, acceptance criteria, and reporting contract.
7. **Worktree for writes, shared for read-only.** Parallel implementation children do not share a checkout.
8. **Verify, don't trust.** A child's summary is a claim; the parent reads the diff and runs the checks.
9. **No nested delegation by default.** A child does the work itself unless the brief explicitly allows otherwise.

## Requirements

- T3 Code with the delegation tools:
  - `orchestrator_capabilities`
  - `delegate_task`
  - `task_status`
  - `task_cancel`
  - `t3_thread_read`, `t3_thread_send`, `t3_pending_request`
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

The scripts are optional helpers. The skill itself uses the live tools.

See [`docs/requirements.md`](docs/requirements.md) for the requirement-to-implementation mapping and the deliberate deviations from the original daily-sync design.

## Status

Initial implementation. The routing policy is a starter heuristic, not a benchmark. Adjust `data/routing-policy.json` to match your own quality and cost measurements.
