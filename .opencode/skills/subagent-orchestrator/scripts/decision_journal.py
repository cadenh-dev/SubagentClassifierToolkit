#!/usr/bin/env python3
"""Append-only decision journal for orchestrator target choices."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

DEFAULT_JOURNAL = Path(__file__).resolve().parent.parent / "data" / "decisions.jsonl"
TOKEN = re.compile(r"(?i)(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{20,}|Bearer\s+\S+|(?:api[_-]?key|access[_-]?token|secret)\s*[:=]\s*\S+)")


class InputError(Exception):
    pass


def load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise InputError(f"could not read JSON from {path}: {exc}") from None


def check_secrets(value, where="record"):
    if isinstance(value, dict):
        for key, child in value.items():
            check_secrets(key, where)
            check_secrets(child, where)
    elif isinstance(value, list):
        for child in value:
            check_secrets(child, where)
    elif isinstance(value, str) and TOKEN.search(value):
        raise InputError(f"secret-looking value refused in {where}")


def make_record(args):
    source = load(Path(args.from_select)) if args.from_select else (load(Path(args.json_file)) if args.json_file else {})
    if args.from_select and source.get("status") != "ok":
        raise InputError("selector output must have status=ok")
    classification = source.get("classification") or {
        "role": source.get("role", args.role), "depth": source.get("depth", args.depth),
        "domain": source.get("domain"), "ignore_cost": source.get("ignore_cost"),
    }
    target = source.get("target") or (json.loads(args.target) if args.target else None)
    if not isinstance(target, dict):
        raise InputError("selected target is required (use --from-select, --json-file, or --target JSON)")
    alternatives = source.get("alternatives", source.get("rejected_candidates", []))
    record = {
        "decision_id": source.get("decision_id", args.decision_id or f"{args.unit}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}"),
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "unit_id": source.get("unit_id", source.get("unit", args.unit)),
        "plan_goal": source.get("plan_goal", args.plan_goal),
        "classification": classification,
        "selected": target,
        "reason": source.get("reason", args.reason or "Selected by routing policy."),
        "alternatives": alternatives,
        "budget": source.get("budget_snapshot", source.get("budget", args.budget or {})),
        "pinned": source.get("pinned", args.pinned),
        "constraints_applied": source.get("constraints_applied", args.constraint or []),
    }
    if not isinstance(record["unit_id"], str) or not record["unit_id"].strip():
        raise InputError("unit_id is required (use --unit)")
    if not isinstance(record["alternatives"], list):
        raise InputError("alternatives must be a list")
    for alt in record["alternatives"]:
        if not isinstance(alt, dict) or not isinstance(alt.get("target"), dict) or not isinstance(alt.get("rejected_because"), str):
            raise InputError("each alternative needs target and rejected_because")
    check_secrets(record)
    return record


def read_jsonl(path):
    if not path.exists(): return []
    rows=[]
    for n, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if line.strip():
            try: rows.append(json.loads(line))
            except ValueError: raise InputError(f"{path}:{n}: invalid JSON") from None
    return rows


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--journal", default=str(DEFAULT_JOURNAL))
    sp=p.add_subparsers(dest="command", required=True)
    r=sp.add_parser("record"); r.add_argument("--from-select"); r.add_argument("--json-file"); r.add_argument("--unit"); r.add_argument("--decision-id"); r.add_argument("--plan-goal"); r.add_argument("--role"); r.add_argument("--depth", type=int); r.add_argument("--target"); r.add_argument("--reason"); r.add_argument("--budget"); r.add_argument("--constraint", action="append", default=[]); r.add_argument("--pinned", action="store_true"); r.add_argument("--json", action="store_true")
    s=sp.add_parser("show"); s.add_argument("--unit", required=True); s.add_argument("--json", action="store_true")
    l=sp.add_parser("list"); l.add_argument("--since"); l.add_argument("--provider"); l.add_argument("--json", action="store_true")
    j=sp.add_parser("join"); j.add_argument("--ledger", required=True); j.add_argument("--json", action="store_true")
    a=p.parse_args(argv)
    try:
        path=Path(a.journal)
        if a.command == "record":
            row=make_record(a); path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as f: f.write(json.dumps(row,separators=(",",":"))+"\n")
            out=[row]
        else:
            rows=read_jsonl(path)
            if a.command == "show": out=[x for x in rows if x.get("unit_id")==a.unit]
            elif a.command == "list":
                out=rows
                if a.since: out=[x for x in out if x.get("timestamp","") >= a.since]
                if a.provider: out=[x for x in out if x.get("selected",{}).get("providerInstanceId")==a.provider]
            else:
                outcomes=read_jsonl(Path(a.ledger)); out=[]
                for row in rows:
                    matches=[o for o in outcomes if o.get("unit")==row.get("unit_id") and (not o.get("decision_id") or o.get("decision_id")==row.get("decision_id"))]
                    out.append({"decision":row,"outcomes":matches})
        if a.command == "show" and not out: raise InputError(f"no decision found for unit {a.unit!r}")
        if a.json: print(json.dumps(out, indent=2))
        else:
            for row in out:
                if a.command == "record": print(f"recorded decision for {row['unit_id']} -> {row['selected'].get('providerInstanceId')}:{row['selected'].get('model')}")
                elif a.command == "show": print(json.dumps(row,indent=2))
                else: print(json.dumps(row))
        return 0
    except (InputError, OSError) as exc:
        print(str(exc), file=sys.stderr); return 2

if __name__ == "__main__": raise SystemExit(main())
