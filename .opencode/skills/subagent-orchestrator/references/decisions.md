# Decision journal

`scripts/decision_journal.py` records the routing decision before delegation. The default path is `data/decisions.jsonl` next to this skill, which resolves to `.opencode/skills/subagent-orchestrator/data/decisions.jsonl` in this repository. Add this exact line to the repository `.gitignore`:

```gitignore
.opencode/skills/subagent-orchestrator/data/decisions.jsonl
```

The journal is append-only JSON Lines. Each record has these fields:

| Field | Meaning |
| --- | --- |
| `decision_id` | Decision identifier, generated when the selector does not provide one. |
| `timestamp` | UTC time when the decision was recorded. |
| `unit_id` | Delegated unit identifier. |
| `plan_goal` | Optional goal from the plan. |
| `classification` | Role, depth, domain, and whether cost was ignored. |
| `selected` | Chosen target, including provider, model, and options. |
| `reason` | Routing rationale. |
| `alternatives` | Rejected targets, each with a `target` object and `rejected_because` explanation. |
| `budget` | Budget snapshot object. |
| `pinned` | Whether the selected target was pinned. |
| `constraints_applied` | Constraints applied during selection. |

`record` accepts selector JSON with `--from-select`, a record JSON file with `--json-file`, or explicit `--target` JSON. `show --unit ID`, `list [--since ISO] [--provider ID]`, and `join --ledger PATH` inspect decisions and outcomes. Exit status is `0` for success and `2` for invalid input, missing decisions, or malformed JSON. Secret-like values are rejected before append, and the refusal message does not include the value.

Outcome joining matches the unit id. If an outcome record has a `decision_id`, it must also match; the outcome ledger schema is unchanged.
