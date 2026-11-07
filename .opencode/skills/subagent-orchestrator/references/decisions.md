# Decision journal

`scripts/decision_journal.py` records the routing decision before delegation. By default it appends to `data/decisions.jsonl`, alongside the outcome ledger. Each JSONL line is an immutable decision with a UTC timestamp, unit id, optional plan goal, classification, selected target/options, rationale, rejected alternatives and reasons, budget snapshot, pin state, and applied constraints. `record` accepts selector JSON with `--from-select`, or a record JSON file with `--json-file`; `show --unit ID`, `list [--since ISO] [--provider ID]`, and `join --ledger PATH` inspect decisions and outcomes. The journal is local runtime data; add `data/decisions.jsonl` to `.gitignore`.

The journal never updates or removes an existing line. Secret-like values are rejected before append. Outcome joining uses unit id, and checks decision id only when an outcome record carries that optional free-form field; the ledger schema is unchanged.
