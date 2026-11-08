# Requirements traceability

This maps the original classifier requirements to the implementation.

| Requirement | Implementation | Notes |
|---|---|---|
| Inspect available harnesses and active models | `orchestrator_capabilities` in Phase 3; `select_target.py` accepts the same JSON snapshot | Live tool, not a stale daily cache; a listing is not proof of authentication or quota |
| Explicit provider/model/settings are hard constraints | Precedence; Phase 3 "Exact pins"; `select_target.py --provider/--model/--option`; `references/routing.md` | Use the pinned target and option values exactly; ranking applies only to open choices; local policy gates are surfaced, not silently bypassed |
| User-required delegation or parent-never-codes | Precedence; Phase 1; Phase 2 rule 5; failure handling; Phase 7 | Parent fallback only when the user leaves the approach open; otherwise report unavailability and ask |
| `IGNORE_COST=false` routes to the lowest-cost capable target | Phase 3 "Default"; `data/routing-policy.json` cost tiers; `select_target.py` | An eligible inherited parent wins first; otherwise the lowest cost tier. Cost tiers, not false-precision API prices; applies to open choices |
| `IGNORE_COST=true` maximizes reasoning | Phase 3 "Max reasoning"; `--ignore-cost`; highest effort option | Availability is still enforced; pinned targets are not reranked |
| Rate-limit awareness | Phase 6/operations "429" policy; cooldown | For open choices, switch targets; for pinned targets, report and ask |
| Remaining usage / budget | `data/usage.example.json`; `select_target.py --usage`; `BUDGET_EXCEEDED`; Phase 3 budget rules | User-maintained snapshot; unknown is not unlimited; pinned targets are still budget-checked |
| Daily `LAST_SYNC_DATE` background probe | Replaced by live `orchestrator_capabilities` per thread | A skill cannot reliably run a background daemon; a live read is fresher and simpler |
| Task domain + reasoning depth | `references/routing.md`; depth 1–5 rubric; role→domain map | Round up on ambiguity |
| Silent scoring | Phase 2–3; `select_target.py` logs only with `--explain` | No chain-of-thought in normal output |
| Output only the exact identifier | "Route-only mode" | Canonical `<providerInstanceId>:<model>[#option=value]`, `UNAVAILABLE`, or `BUDGET_EXCEEDED` |
| Exact identifier examples | `references/routing.md` | Preserves `/ . - _ : @ #`; display names are forbidden |
| Deploy subagents | Phase 5; `references/operations.md` §1 | Native child only for same provider/supported model/no extra settings; `delegate_task` for cross-provider, requested settings, isolation, or background |
| Use subagents | Phases 1–2 and 6–7; `references/operations.md`; patterns and anti-patterns | Decide, classify, brief, supervise, verify, integrate |
| Self-contained briefs | `references/briefs.md`; `assets/brief-template.md` | No parent history; no worktree setup in the brief |
| One frame/one call style efficiency | "one unit, one brief"; no one-subagent-per-file | Avoids token burn and duplicate review |
| Verification | Phase 7; acceptance criteria; diff and test checks; `outcome_ledger.py` | Summaries are claims, not evidence; only parent-checked runs are recorded as verified |
| Failure handling | Phase 7 table; operations §6 | Timeout is not failure; auth is distinct from unavailability; scope creep, rate limit |
| PR handling | Phase 7; operations §7 | Link immediately on create/takeover/push; `link_pull_requests` for a stack; child never watches; parent watches only when asked or awaiting checks |
| No nested delegation | Non-negotiables; child rules | Child does the work itself unless explicitly allowed |
| No polling | Non-negotiables; Phase 6; operations §3 | Continue independent work after launch; end the turn when nothing remains. Follow `next`; never poll, sleep, or set a watcher for a child |
| New reviews and rechecks | Phase 5–7; operations §6; briefs "Review recheck" | New `delegate_task`, never `t3_thread_send` on a finished child |
| New top-level threads | Phase 5; operations §1 | Only on explicit user request; not a subagent mechanism |
| Testable routing | `tests/test_toolkit.py`; CI workflow | Cost, max, disabled, unavailable, pins, budget, malformed input, cycles, strict warnings |
| Outcome feedback | `.opencode/skills/subagent-orchestrator/scripts/outcome_ledger.py`; `references/outcomes.md`; `tests/test_outcome_ledger.py` | Append-only metadata; distinguishes verified from claimed; never auto-tunes policy |
| Skill packaging checks | `.opencode/skills/skill-linter/`; `tests/test_skill_linter.py` | Read-only frontmatter, name, links, syntax, secret, and path checks |
| Project-local tool classifier | `.opencode/skills/project-classifier/`; `build_project_classifier.py`; `tests/test_project_classifier.py` | Detects languages, frameworks, engines, commands; safe refresh preserves unrelated files; probes Python runtime usability |
| Trigger-only skill descriptions | Every `SKILL.md` `description`; `tests/test_skill_evals.py` `DescriptionTests` | Descriptions start with "Use when" and never summarize the workflow, so agents read the skill instead of following a summary |
| Skill loads when it should | `assets/bootstrap.md`; `scripts/install_bootstrap.py`; `tests/test_install_bootstrap.py`; `evals/.../triggers.json` | Opt-in AGENTS.md snippet with the load conditions and an explicit "otherwise do it yourself" |
| Skill text is tested, not just scripts | `evals/run_skill_evals.py`; `evals/subagent-orchestrator/scenarios.json`; `tests/test_skill_evals.py` | Pressure scenarios graded deterministically; CI checks the grader and that every rule a scenario relies on still exists in SKILL.md; live runs are manual |
| Fix loop and review cadence | Phase 7 gates; operations §8a; briefs "Fix round"; `scripts/fix_brief.py`; `tests/test_fix_brief.py` | Machine check, spec check, quality review per unit; fix rounds are new delegates; two rounds then escalate |
| Agents do not rationalize past rules | "Excuses that break Phase 1/5/7" tables in SKILL.md | Rows come from observed eval failures where possible |

## Deliberate deviations

1. **No daily background sync.** T3's `orchestrator_capabilities` is the live source of truth. A `SKILL.md` cannot guarantee a background process finishes before routing, and a stale cache is worse than a live read.
2. **Cost tiers instead of fetched prices.** Prices differ across API and subscription billing and are not uniformly exposed. A tier is honest; a fabricated precise price is not.
3. **User-maintained remaining usage instead of a fake quota API.** T3 does not expose a uniform remaining-usage endpoint for every provider. The skill accepts a snapshot and refuses to treat unknown as unlimited; `BUDGET_EXCEEDED` is an explicit outcome.
4. **Canonical provider-qualified targets.** The original examples (`Deepseek v4.1 flash`, `claude`, `codex`) mix display names, harnesses, and models. The implementation uses the target shape `delegate_task` actually accepts.
5. **The main mode deploys, not just prints.** The strict one-line output is preserved as an explicit route-only mode so it remains useful to callers that only want a classifier.
6. **No secret probing.** The skill never reads credential files. It uses T3's capability tool and, when a child needs a secret, the parent uses `request_secret`.
7. **User scope outranks ranking defaults.** A pinned provider, model, or option is honored or reported as unavailable/over-budget. Local policy remains a hard eligibility gate; a conflicting pin produces a visible conflict rather than a silent override or a silent substitution.
8. **No automatic parent takeover.** When the user requires delegation or forbids parent coding, an unavailable target produces an honest explanation and a question, not a silent return to parent implementation.
9. **Routing evidence is descriptive, not automatic.** The outcome ledger records what happened and summarizes comparable runs. A human changes `routing-policy.json`; the tool does not promote a model from a small or unverified sample.
