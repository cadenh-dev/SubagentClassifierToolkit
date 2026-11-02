# Routing: task depth, domains, and targets

This is the long form of Phase 3 in `SKILL.md`.

## 1. Read live capabilities

Call `orchestrator_capabilities` once per thread before the first delegation:

```text
orchestrator_capabilities()
```

The useful fields:

- `providers[].providerInstanceId`
- `providers[].canRunChildTask`
- `providers[].canRunCrossProviderChildTask`
- `providers[].constraints`
- `providers[].models[].id`
- `providers[].models[].options[]` (`id`, `values`, `default`)
- `features.appOwnedSubagents`, `maxBatchThreads`

Do not cache this across days and do not scrape pricing. The live tool is the source of truth for availability and options.

## 2. Classify the task

### Depth

| Depth | Task shape | Examples | Target posture |
|---|---|---|---|
| 1 | Lookup, formatting, a single command | "What does this flag do?", rename a variable | Cheapest eligible; low effort |
| 2 | One-file edit, known pattern | Add a field, fix a small bug, add one test | Cheap/mid; default effort |
| 3 | Multi-file change, debugging with tests | Refactor a module, trace a failing integration test | Strong code target; high effort |
| 4 | Cross-module refactor, architecture tradeoff | Migrate auth, split a service, change a public API | Top reasoning; high/max effort |
| 5 | Novel design, high-risk migration, ambiguous requirements | New subsystem, security-sensitive redesign | Strongest available; max effort |

Round **up** when the task is ambiguous. Under-routing is usually more expensive than over-routing because a failed child costs a retry plus review.

### Domain

Use the role default unless the task clearly belongs elsewhere:

| Role | Default domain | Notes |
|---|---|---|
| `implementation` | `code` | Most coding work |
| `research` | `research` | Breadth, source gathering, option comparison |
| `review` | `review` | Correctness, risk, security, maintainability |
| `design` | `architecture` | Plans, interfaces, migration strategy |
| `test` | `test` | Reproduction, coverage, environment matrices |
| `general` | `general` | Fallback; specify writes explicitly |

Optional domains: `docs`, `security`, `data`. Use one domain per unit; split the unit if it genuinely spans two.

## 3. Filter targets

A target is eligible only when all are true:

1. `canRunChildTask == true`.
2. `constraints` is empty. Treat any "disabled" constraint as a hard stop.
3. The model id is non-empty.
4. If the parent provider differs, `canRunCrossProviderChildTask == true`.
5. The target is not rate-limited or cooling down.
6. If `min_context` is required, the model's context is known and large enough. Unknown is not enough.

Do not use a disabled provider just because it has the highest score. Do not use a model that just returned a 429.

## 4. Select

### Default: cheapest capable

1. **Parent preference.** If the inherited provider/model is eligible and its reasoning score for the domain is at least the required depth, use it. This avoids a provider hop and is usually the cheapest correct choice. It can lose to a cheaper provider by design; exclude the parent explicitly if you want a pure cost race.
2. **Cheapest capable.** Otherwise choose the lowest cost tier whose reasoning score is at least the depth.
3. **Tie-breaks.** Higher reasoning, larger context, higher remaining quota, lower latency, earlier in the policy order.
4. **No candidate.** Do the work in the parent, or ask the user. Do not quietly route to an under-qualified model.

### `IGNORE_COST=true`: max reasoning

1. Highest reasoning score for the domain among eligible targets.
2. Tie-break: larger context, higher remaining quota, lower latency.
3. Select the highest supported effort option.
4. Ignore price and soft quota. **Do not** ignore availability, disabled providers, or cooldowns.
5. If no eligible target exists, return `UNAVAILABLE`.

### Cost tiers vs. exact prices

Exact per-token prices are not exposed uniformly across T3 providers, and subscriptions do not map cleanly to API prices. Use a cost tier:

| Tier | Meaning |
|---|---|
| 0 | Free / included |
| 1 | Very low |
| 2 | Low |
| 3 | Medium |
| 4 | High |
| 5 | Premium |

`data/routing-policy.json` is a starter mapping. It is a policy, not a benchmark. Edit it when your own measurements disagree.

### Reasoning scores

Scores are 1–5 per domain. They are a routing policy, not a published benchmark. If a model has no score, treat it as depth 2 (general) and cost tier 3; do not let an unknown model win max-reasoning mode over a scored model.

## 5. Options

Options are provider-specific. Read them from capabilities; never assume an option id from another provider.

| Provider | Common option ids |
|---|---|
| `codex` | `reasoningEffort`, `serviceTier` |
| `claudeAgent` | `effort`, `fastMode` |
| `opencode` | `variant` |
| `cursor` | `reasoning_effort`, `effort`, `contextWindow`, `fastMode`, `thinking` |
| `antigravity` | `thinking_level` |

Selection rules:

- **Max mode:** highest reasoning value (`ultra` > `max` > `xhigh` > `high` > `medium` > `low`); `fastMode=false`; `serviceTier=default`; `contextWindow` largest.
- **Cost mode:** choose the lowest effort that fits the depth:
  - depth 1–2: the model default, or the lowest effort;
  - depth 3: `high` when available;
  - depth 4: `xhigh`, then `high`;
  - depth 5: the highest effort.
  `fastMode=true`; `serviceTier=default`; `contextWindow` default/smallest.
- Pass only declared option ids. For cross-provider targets, pass the selected options explicitly instead of relying on inherited parent options.

## 6. Route-only output

Canonical form:

```text
<providerInstanceId>:<model>[#<option>=<value>,...]
```

Examples:

```text
opencode:opencode-go/deepseek-v4.1-flash#variant=max
codex:gpt-6.1-sol#reasoningEffort=high,serviceTier=default
cursor:grok-4.7#reasoning_effort=high,contextWindow=500k,fastMode=false
```

Rules:

- One line. No Markdown, JSON, backticks, quotes, or explanation.
- Validate the target against the capabilities you just read.
- Never output a display name such as "DeepSeek V4.1 Flash".
- `UNAVAILABLE` is the only non-target output.
- Preserve `/`, `.`, `-`, `_`, `:`, `@`, and `#` inside the identifier.

## 7. Worked examples

These use hypothetical scores. Read the live capability list and policy before acting.

### A. Fix a small bug

- Task: "Fix the null check in `parse_config`."
- Depth 2, role implementation.
- Parent target is eligible and scored 3 for code.
- Result: do it in the parent. No delegate.

### B. Refactor auth across three packages

- Units:
  1. `api-auth` — implementation, worktree, depth 4.
  2. `web-auth` — implementation, worktree, depth 3.
  3. `migration-review` — review, shared, depth 4, depends on both.
- Cost mode: `api-auth` and `web-auth` use the cheapest targets with code >= 3/4; review uses a target with review >= 4, preferably a different provider.
- Parent integrates the worktrees, runs the full suite, and links the PRs.

### C. Compare three caching approaches

- Units: three research agents, one per approach, shared isolation, depth 3–4.
- Each returns: mechanism, tradeoffs, failure modes, operational cost, recommendation.
- Parent synthesizes; no code changes.

### D. Security review of a migration

- `IGNORE_COST=true`.
- Depth 5, role review.
- Select the highest review score among eligible providers; choose max effort.
- If the best provider is disabled, choose the next highest; do not route to it anyway.

### E. No eligible target

- All high-reasoning providers are disabled or cooling down.
- Cost mode: no target meets depth 4.
- Result: do the work in the parent or ask; route-only output `UNAVAILABLE`.

## 8. Failure and cooldown policy

- **429 / rate limit:** mark the target cooling down for the provider's reset window if known; otherwise use a short backoff. Choose the next eligible target.
- **Auth failure:** remove the target for this thread; do not retry.
- **Invalid option:** drop the option, not the model; retry once with defaults.
- **Model disappears:** re-read `orchestrator_capabilities` and re-route.
- **Repeated child failure:** narrow the brief before raising the model.
