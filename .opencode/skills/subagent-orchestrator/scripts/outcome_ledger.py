#!/usr/bin/env python3
"""Append-only outcome ledger for delegated units.

The ledger records evidence for routing decisions. It never edits
`data/routing-policy.json` and never promotes a model automatically.

Usage:
  outcome_ledger.py record --unit auth-core --role implementation --depth 3 \
    --provider codex --model gpt-6.1-sol --outcome success \
    --verification parent --estimated-units 50000 --actual-units 41200 \
    --duration 615 --retries 0 --review-findings 2

  outcome_ledger.py summarize
  outcome_ledger.py summarize --by model --role implementation --depth 3
  outcome_ledger.py summarize --by role --since 2026-11-01 --json

Storage is JSON Lines: one object per line. The default ledger is
`data/outcomes.jsonl` next to this skill. Keep it out of version control.

Exit codes:
  0  recorded or summarized successfully
  1  no records matched when summarizing with --strict-empty
  2  invalid input or malformed ledger
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

SCHEMA_VERSION = 1
TOOL = "outcome-ledger"
ROLES = {"research", "design", "implementation", "test", "review", "general"}
OUTCOMES = {"success", "failure", "partial", "cancelled"}
VERIFICATIONS = {"parent", "child-report", "none"}
ISOLATIONS = {"shared", "worktree"}
MODES = {"async", "wait"}
DEFAULT_LEDGER = Path(__file__).resolve().parent.parent / "data" / "outcomes.jsonl"


class InputError(ValueError):
    """Raised when a record or command-line value is invalid."""


def finite_nonnegative(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InputError(f"{field} must be a finite non-negative number")
    try:
        number = float(value)
    except (OverflowError, ValueError):
        raise InputError(f"{field} must be a finite non-negative number") from None
    if not math.isfinite(number) or number < 0:
        raise InputError(f"{field} must be a finite non-negative number")
    return number


MAX_COUNT = 1_000_000


def nonnegative_int(value: Any, field: str, maximum: int | None = None) -> int:
    if type(value) is not int or value < 0 or (maximum is not None and value > maximum):
        if maximum is not None:
            raise InputError(f"{field} must be an integer from 0 to {maximum}")
        raise InputError(f"{field} must be a non-negative integer")
    return value


def nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{field} must be a non-empty string")
    return value.strip()


def parse_timestamp(value: str, field: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise InputError(f"{field} must be an ISO 8601 timestamp") from None


def parse_option(raw: str) -> tuple[str, Any]:
    if "=" not in raw:
        raise InputError(f"--option must be id=value, got {raw!r}")
    option_id, value = raw.split("=", 1)
    option_id = nonempty(option_id, "--option id")
    value = value.strip()
    if not value:
        raise InputError(f"--option {option_id!r} must have a value")
    lowered = value.lower()
    if lowered == "true":
        return option_id, True
    if lowered == "false":
        return option_id, False
    return option_id, value


def build_record(args: argparse.Namespace) -> dict[str, Any]:
    unit = nonempty(args.unit, "--unit")
    provider = nonempty(args.provider, "--provider")
    model = nonempty(args.model, "--model")

    if args.depth < 1 or args.depth > 5:
        raise InputError("--depth must be between 1 and 5")
    if args.role not in ROLES:
        raise InputError(f"--role must be one of: {', '.join(sorted(ROLES))}")
    if args.outcome not in OUTCOMES:
        raise InputError(f"--outcome must be one of: {', '.join(sorted(OUTCOMES))}")
    if args.verification not in VERIFICATIONS:
        raise InputError(
            f"--verification must be one of: {', '.join(sorted(VERIFICATIONS))}"
        )

    options: dict[str, Any] = {}
    for raw in args.option or []:
        option_id, value = parse_option(raw)
        if option_id in options:
            raise InputError(f"duplicate --option id {option_id!r}")
        options[option_id] = value

    record: dict[str, Any] = {
        "version": SCHEMA_VERSION,
        "recorded_at": parse_timestamp(
            args.recorded_at, "--recorded-at"
        ).isoformat().replace("+00:00", "Z")
        if args.recorded_at
        else datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "unit": unit,
        "role": args.role,
        "depth": args.depth,
        "target": {
            "providerInstanceId": provider,
            "model": model,
            "options": options,
        },
        "outcome": args.outcome,
        "verification": args.verification,
        "estimated_units": (
            finite_nonnegative(args.estimated_units, "--estimated-units")
            if args.estimated_units is not None
            else None
        ),
        "actual_units": (
            None
            if args.actual_units is None
            else finite_nonnegative(args.actual_units, "--actual-units")
        ),
        "duration_seconds": (
            finite_nonnegative(args.duration, "--duration")
            if args.duration is not None
            else None
        ),
        "retries": nonnegative_int(args.retries, "--retries", MAX_COUNT),
        "review_findings": nonnegative_int(
            args.review_findings, "--review-findings", MAX_COUNT
        ),
        "pr": nonempty(args.pr, "--pr") if args.pr else None,
        "notes": args.notes.strip() if args.notes and args.notes.strip() else None,
    }
    if args.isolation is not None:
        if args.isolation not in ISOLATIONS:
            raise InputError(
                f"--isolation must be one of: {', '.join(sorted(ISOLATIONS))}"
            )
        record["isolation"] = args.isolation
    if args.mode is not None:
        if args.mode not in MODES:
            raise InputError(f"--mode must be one of: {', '.join(sorted(MODES))}")
        record["mode"] = args.mode
    return record


def append_record(path: Path, record: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, separators=(",", ":"))
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
    except OSError as error:
        raise InputError(f"could not append to {path}: {error}") from None


def read_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as error:
        raise InputError(f"could not read {path}: {error}") from None

    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (ValueError, RecursionError) as error:
            detail = getattr(error, "msg", None) or str(error)
            raise InputError(f"{path}:{line_number}: invalid JSON: {detail}") from None
        if not isinstance(record, dict):
            raise InputError(f"{path}:{line_number}: record must be a JSON object")
        validate_record(record, f"{path}:{line_number}")
        records.append(record)
    return records


def validate_record(record: dict[str, Any], where: str) -> None:
    """Validate a record read from disk without repairing it."""
    try:
        if record.get("version") != SCHEMA_VERSION or type(record.get("version")) is not int:
            raise InputError(f"version must be {SCHEMA_VERSION}")
        nonempty(record.get("unit"), "unit")
        role = record.get("role")
        if not isinstance(role, str) or role not in ROLES:
            raise InputError(f"role must be one of: {', '.join(sorted(ROLES))}")
        depth = record.get("depth")
        if type(depth) is not int or depth not in (1, 2, 3, 4, 5):
            raise InputError("depth must be an integer from 1 to 5")
        target = record.get("target")
        if not isinstance(target, dict):
            raise InputError("target must be an object")
        nonempty(target.get("providerInstanceId"), "target.providerInstanceId")
        nonempty(target.get("model"), "target.model")
        options = target.get("options", {})
        if not isinstance(options, dict):
            raise InputError("target.options must be an object")
        for option_id, value in options.items():
            nonempty(option_id, "target.options key")
            if not isinstance(value, (str, bool)):
                raise InputError(f"target.options[{option_id!r}] must be a scalar")
        outcome = record.get("outcome")
        if not isinstance(outcome, str) or outcome not in OUTCOMES:
            raise InputError(f"outcome must be one of: {', '.join(sorted(OUTCOMES))}")
        verification = record.get("verification")
        if not isinstance(verification, str) or verification not in VERIFICATIONS:
            raise InputError(
                f"verification must be one of: {', '.join(sorted(VERIFICATIONS))}"
            )
        isolation = record.get("isolation")
        if "isolation" in record and (
            not isinstance(isolation, str) or isolation not in ISOLATIONS
        ):
            raise InputError("isolation must be shared or worktree")
        mode = record.get("mode")
        if "mode" in record and (not isinstance(mode, str) or mode not in MODES):
            raise InputError("mode must be async or wait")
        for field in ("estimated_units", "actual_units", "duration_seconds"):
            value = record.get(field)
            if value is not None:
                finite_nonnegative(value, field)
        nonnegative_int(record.get("retries", 0), "retries", MAX_COUNT)
        nonnegative_int(record.get("review_findings", 0), "review_findings", MAX_COUNT)
        recorded_at = record.get("recorded_at")
        if not isinstance(recorded_at, str):
            raise InputError("recorded_at must be an ISO 8601 string")
        parse_timestamp(recorded_at, "recorded_at")
    except InputError as error:
        raise InputError(f"{where}: {error}") from None


def target_key(record: dict[str, Any]) -> str:
    target = record["target"]
    return f"{target['providerInstanceId']}:{target['model']}"


def option_text(options: dict[str, Any]) -> str:
    parts: list[str] = []
    for option_id in sorted(options):
        value = options[option_id]
        if isinstance(value, bool):
            value = "true" if value else "false"
        parts.append(f"{option_id}={value}")
    return ",".join(parts)


def group_key(record: dict[str, Any], by: str) -> str:
    if by == "role":
        return str(record["role"])
    base = target_key(record)
    if by == "model":
        return base
    options = option_text(record["target"].get("options") or {})
    suffix = f"#{options}" if options else ""
    return f"{base}{suffix}@depth={record['depth']}"


def rounded(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(value, digits)


def build_summary(records: Iterable[dict[str, Any]], by: str) -> dict[str, Any]:
    groups: dict[str, dict[str, Any]] = {}
    for record in records:
        key = group_key(record, by)
        group = groups.setdefault(
            key,
            {
                "key": key,
                "runs": 0,
                "outcomes": {name: 0 for name in sorted(OUTCOMES)},
                "verified_runs": 0,
                "verified_successes": 0,
                "retries": 0,
                "review_findings": 0,
                "duration_runs": 0,
                "duration_seconds": 0.0,
                "estimated_runs": 0,
                "estimated_units": 0.0,
                "actual_runs": 0,
                "actual_units": 0.0,
                "usage_pairs": 0,
                "paired_estimated_units": 0.0,
                "paired_actual_units": 0.0,
                "last_recorded_at": None,
                "_last_recorded_dt": None,
            },
        )
        group["runs"] += 1
        group["outcomes"][record["outcome"]] += 1
        if record["verification"] == "parent":
            group["verified_runs"] += 1
            if record["outcome"] == "success":
                group["verified_successes"] += 1
        group["retries"] += int(record.get("retries", 0))
        group["review_findings"] += int(record.get("review_findings", 0))
        if record.get("duration_seconds") is not None:
            group["duration_runs"] += 1
            group["duration_seconds"] += float(record["duration_seconds"])
        estimated = record.get("estimated_units")
        actual = record.get("actual_units")
        if estimated is not None:
            group["estimated_runs"] += 1
            group["estimated_units"] += float(estimated)
        if actual is not None:
            group["actual_runs"] += 1
            group["actual_units"] += float(actual)
        if estimated is not None and actual is not None:
            group["usage_pairs"] += 1
            group["paired_estimated_units"] += float(estimated)
            group["paired_actual_units"] += float(actual)
        recorded_at = parse_timestamp(str(record["recorded_at"]), "recorded_at")
        if group["_last_recorded_dt"] is None or recorded_at > group["_last_recorded_dt"]:
            group["_last_recorded_dt"] = recorded_at
            group["last_recorded_at"] = recorded_at.isoformat().replace("+00:00", "Z")

    items: list[dict[str, Any]] = []
    for group in sorted(groups.values(), key=lambda item: item["key"]):
        runs = group["runs"]
        duration_runs = group["duration_runs"]
        usage_pairs = group["usage_pairs"]
        items.append(
            {
                "key": group["key"],
                "runs": runs,
                "outcomes": group["outcomes"],
                "success_rate": rounded(group["outcomes"]["success"] / runs),
                "verified_runs": group["verified_runs"],
                "verified_success_rate": rounded(
                    group["verified_successes"] / group["verified_runs"]
                )
                if group["verified_runs"]
                else None,
                "retries": group["retries"],
                "retries_per_run": rounded(group["retries"] / runs),
                "review_findings": group["review_findings"],
                "duration_runs": duration_runs,
                "duration_seconds_total": rounded(group["duration_seconds"], 2),
                "duration_seconds_avg": rounded(
                    group["duration_seconds"] / duration_runs, 2
                )
                if duration_runs
                else None,
                "estimated_runs": group["estimated_runs"],
                "estimated_units_total": rounded(group["estimated_units"], 2),
                "actual_runs": group["actual_runs"],
                "actual_units_total": rounded(group["actual_units"], 2),
                "usage_pairs": usage_pairs,
                "paired_estimated_units_total": rounded(
                    group["paired_estimated_units"], 2
                ),
                "paired_actual_units_total": rounded(
                    group["paired_actual_units"], 2
                ),
                "usage_ratio": rounded(
                    group["paired_actual_units"] / group["paired_estimated_units"]
                )
                if usage_pairs and group["paired_estimated_units"] > 0
                else None,
                "last_recorded_at": group["last_recorded_at"],
            }
        )
    return {
        "tool": TOOL,
        "version": SCHEMA_VERSION,
        "group_by": by,
        "groups": items,
        "summary": {
            "records": sum(item["runs"] for item in items),
            "groups": len(items),
        },
    }


def filter_since(records: Sequence[dict[str, Any]], since: str | None) -> list[dict[str, Any]]:
    if since is None:
        return list(records)
    cutoff = parse_timestamp(since, "--since")
    kept: list[dict[str, Any]] = []
    for record in records:
        recorded = parse_timestamp(str(record["recorded_at"]), "recorded_at")
        if recorded >= cutoff:
            kept.append(record)
    return kept


def format_summary(payload: dict[str, Any], verified_only: bool) -> str:
    lines = [
        f"{TOOL}: {payload['summary']['records']} record(s), "
        f"{payload['summary']['groups']} group(s) by {payload['group_by']}"
    ]
    for group in payload["groups"]:
        verified = (
            f"{group['verified_success_rate']:.0%} of {group['verified_runs']} verified"
            if group["verified_success_rate"] is not None
            else "no verified runs"
        )
        lines.append(
            f"\n{group['key']}"
            f"\n  runs={group['runs']} success={group['success_rate']:.0%} "
            f"({group['outcomes']['success']} success, "
            f"{group['outcomes']['failure']} failure, "
            f"{group['outcomes']['partial']} partial, "
            f"{group['outcomes']['cancelled']} cancelled)"
            f"\n  verified: {verified}"
            f"\n  retries={group['retries']} ({group['retries_per_run']:.2f}/run) "
            f"review_findings={group['review_findings']}"
            f"\n  duration: "
            + (
                f"{group['duration_seconds_avg']:.1f}s avg over "
                f"{group['duration_runs']} run(s), "
                f"{group['duration_seconds_total']:.1f}s total"
                if group["duration_seconds_avg"] is not None
                else "unknown"
            )
            + "\n  usage: "
            + (
                f"{group['usage_pairs']} paired run(s); "
                f"estimate={group['paired_estimated_units_total']:.0f}, "
                f"actual={group['paired_actual_units_total']:.0f}; "
                + (
                    f"actual/estimate={group['usage_ratio']:.2f}x"
                    if group["usage_ratio"] is not None
                    else "ratio unavailable (zero estimate)"
                )
                if group["usage_pairs"]
                else "no paired actual/estimate records"
            )
            + f"\n  last_recorded_at={group['last_recorded_at']}"
        )
    if not payload["groups"]:
        lines.append("No records matched. The routing policy is unchanged.")
    elif verified_only:
        lines.append(
            "\nVerified-only view. A verified success requires the parent to have "
            "checked the diff/tests, not just accepted the child's summary."
        )
    return "\n".join(lines)


def record_command(args: argparse.Namespace) -> int:
    record = build_record(args)
    append_record(Path(args.ledger), record)
    if args.json:
        print(json.dumps(record, indent=2))
    else:
        print(
            f"recorded {record['unit']} -> {record['outcome']} "
            f"({target_key(record)}) in {args.ledger}"
        )
    return 0


def summarize_command(args: argparse.Namespace) -> int:
    path = Path(args.ledger)
    records = read_records(path)
    if args.since:
        records = filter_since(records, args.since)
    if args.role:
        records = [record for record in records if record["role"] == args.role]
    if args.depth is not None:
        records = [record for record in records if record["depth"] == args.depth]
    if args.verified_only:
        records = [record for record in records if record["verification"] == "parent"]
    payload = build_summary(records, args.by)
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(format_summary(payload, args.verified_only))
    if args.strict_empty and not records:
        print("no records matched", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Append-only outcome ledger for delegated units.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--ledger",
        default=str(DEFAULT_LEDGER),
        help=f"JSONL ledger path (default: {DEFAULT_LEDGER})",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    record = subparsers.add_parser("record", help="append one outcome record")
    record.add_argument(
        "--ledger", default=argparse.SUPPRESS, help="override the global --ledger path"
    )
    record.add_argument("--unit", required=True)
    record.add_argument("--role", required=True)
    record.add_argument("--depth", type=int, required=True)
    record.add_argument("--provider", required=True)
    record.add_argument("--model", required=True)
    record.add_argument("--option", action="append", default=[], metavar="ID=VALUE")
    record.add_argument("--isolation", choices=sorted(ISOLATIONS))
    record.add_argument("--mode", choices=sorted(MODES))
    record.add_argument("--outcome", required=True, choices=sorted(OUTCOMES))
    record.add_argument(
        "--verification",
        default="none",
        choices=sorted(VERIFICATIONS),
        help="parent means the parent checked the diff/tests",
    )
    record.add_argument("--estimated-units", type=float)
    record.add_argument("--actual-units", type=float)
    record.add_argument("--duration", type=float, help="wall-clock seconds")
    record.add_argument("--retries", type=int, default=0)
    record.add_argument("--review-findings", type=int, default=0)
    record.add_argument("--pr")
    record.add_argument("--notes")
    record.add_argument("--recorded-at", help="ISO 8601 timestamp; defaults to now")
    record.add_argument("--json", action="store_true")
    record.set_defaults(handler=record_command)

    summarize = subparsers.add_parser("summarize", help="aggregate recorded outcomes")
    summarize.add_argument(
        "--ledger", default=argparse.SUPPRESS, help="override the global --ledger path"
    )
    summarize.add_argument(
        "--by",
        choices=["target", "model", "role"],
        default="target",
        help="target = model + options + depth; model = provider:model; role = unit role",
    )
    summarize.add_argument("--since", help="ISO 8601 lower bound on recorded_at")
    summarize.add_argument("--role", choices=sorted(ROLES), help="only this role")
    summarize.add_argument("--depth", type=int, choices=range(1, 6), help="only this depth")
    summarize.add_argument(
        "--verified-only", action="store_true", help="only parent-verified records"
    )
    summarize.add_argument("--json", action="store_true")
    summarize.add_argument(
        "--strict-empty",
        action="store_true",
        help="exit 1 when no records match",
    )
    summarize.set_defaults(handler=summarize_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except InputError as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
