# Requirements traceability

This maps the original classifier requirements to the implementation.

| Requirement | Implementation | Notes |
|---|---|---|
| Inspect authenticated harnesses and active models | `orchestrator_capabilities` in Phase 3; `select_target.py` accepts the same JSON snapshot | Live tool, not a stale daily cache |
| `IGNORE_COST=false` routes to the lowest-cost capable target | Phase 3 "Default"; `data/routing-policy.json` cost tiers; `select_target.py` | Cost tiers, not false-precision API prices |
| `IGNORE_COST=true` maximizes reasoning | Phase 3 "Max reasoning"; `--ignore-cost`; highest effort option | Availability is still enforced |
| Rate-limit awareness | Phase 6/operations "429" policy; cooldown | T3 capabilities do not expose live quota for every provider, so treat unknown as unknown |
| Daily `LAST_SYNC_DATE` background probe | Replaced by live `orchestrator_capabilities` per thread | A skill cannot reliably run a background daemon; a live read is fresher and simpler |
| Task domain + reasoning depth | `references/routing.md`; depth 1–5 rubric; role→domain map | Round up on ambiguity |
| Silent scoring | Phase 2–3; `select_target.py` logs only with `--explain` | No chain-of-thought in normal output |
| Output only the exact identifier | "Route-only mode" | Canonical `<providerInstanceId>:<model>[#option=value]` or `UNAVAILABLE` |
| Exact identifier examples | `references/routing.md` | Preserves `/ . - _ : @ #`; display names are forbidden |
| Deploy subagents | Phase 5; `delegate_task` with target/role/isolation/mode | Worktree for writes, shared for reads |
| Use subagents | Phases 1–2 and 6–7; `references/operations.md`; patterns and anti-patterns | Decide, classify, brief, supervise, verify, integrate |
| Self-contained briefs | `references/briefs.md`; `assets/brief-template.md` | No parent history |
| One frame/one call style efficiency | "one unit, one brief"; no one-subagent-per-file | Avoids token burn and duplicate review |
| Verification | Phase 7; acceptance criteria; diff and test checks | Summaries are claims, not evidence |
| Failure handling | Phase 7 table; operations §6 | Timeout, failure, question, scope creep, rate limit |
| PR handling | Phase 7; operations §7 | Parent links/watches; child never watches |
| No nested delegation | Non-negotiables; child rules | Child does the work itself unless explicitly allowed |
| No polling | Non-negotiables; Phase 6 | Completion wakes the parent |
| Testable routing | `tests/test_toolkit.py`; CI workflow | Cost, max, disabled, unavailable, cycles, strict warnings |

## Deliberate deviations

1. **No daily background sync.** T3's `orchestrator_capabilities` is the live source of truth. A `SKILL.md` cannot guarantee a background process finishes before routing, and a stale cache is worse than a live read.
2. **Cost tiers instead of fetched prices.** Prices differ across API and subscription billing and are not uniformly exposed. A tier is honest; a fabricated precise price is not.
3. **Canonical provider-qualified targets.** The original examples (`Deepseek v4.1 flash`, `claude`, `codex`) mix display names, harnesses, and models. The implementation uses the target shape `delegate_task` actually accepts.
4. **The main mode deploys, not just prints.** The strict one-line output is preserved as an explicit route-only mode so it remains useful to callers that only want a classifier.
5. **No secret probing.** The skill never reads credential files. It uses T3's capability tool and, when a child needs a secret, the parent uses `request_secret`.
