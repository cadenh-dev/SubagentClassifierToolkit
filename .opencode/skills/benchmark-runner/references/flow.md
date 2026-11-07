# Benchmark flow

Task JSON fields: `objective`, `acceptance` (nonempty string array), `commands` (string array), and `write_scope` (path array). Targets JSON is an array of `{ "providerInstanceId", "model", "options" }` objects.

Plan units share task metadata and have unique ids. Branch naming instructions include the benchmark id and target ordinal. The parent owns launch, worktree association, diff review, and supplying human measurements to compare/record. Results are stored in `.benchmark-results/<plan-stem>/<unit>.json`.

`score` delegates all verification semantics to `subagent-orchestrator/scripts/verify_unit.py`. `record` delegates ledger semantics to `record_verification.py`; provide required ledger fields explicitly. Unknown usage is omitted, never treated as zero.
