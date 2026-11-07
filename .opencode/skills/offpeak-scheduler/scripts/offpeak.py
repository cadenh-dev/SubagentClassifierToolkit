#!/usr/bin/env python3
"""Check DeepSeek off-peak windows and defer scheduled units.

Usage: python3 offpeak.py {status|gate|next|defer|due|done|wait|schedule-hint} ...
Exit codes: 0 ok/run now, 1 validation failure, 2 bad input, 3 defer.
Uses only the Python 3.10+ standard library; deferred state is append-only JSONL.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
import time
from datetime import date, datetime, time as daytime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WINDOWS = ROOT / "data" / "windows.json"
DEFAULT_STATE = ROOT / "data" / "deferred.jsonl"
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
UTC = timezone.utc

class InputError(ValueError):
    """Invalid command input or scheduler data."""

def load_windows(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
        if data.get("schema_version") != 1 or not isinstance(data.get("rules"), list):
            raise InputError("windows must have schema_version 1 and a rules list")
        seen: set[str] = set()
        for rule in data["rules"]:
            if not rule.get("id") or rule["id"] in seen:
                raise InputError("each rule needs a unique id")
            seen.add(rule["id"])
            if rule.get("enforce") not in ("default", "opt-in"):
                raise InputError(f"{rule['id']}: invalid enforce")
            match = rule.get("match", {})
            if not match.get("model_patterns") or not rule.get("sources"):
                raise InputError(f"{rule['id']}: model_patterns and sources are required")
            holidays = rule.get("holidays", {})
            ZoneInfo(holidays.get("timezone", "UTC"))
            if not isinstance(holidays.get("dates"), list):
                raise InputError(f"{rule['id']}: holidays.dates must be a list")
            for day in holidays["dates"]:
                date.fromisoformat(day)
            for window in rule.get("peak_windows_utc", []):
                for key in ("start", "end"):
                    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", window.get(key, "")):
                        raise InputError(f"{rule['id']}: bad {key} time")
                if window["start"] == window["end"]:
                    raise InputError(f"{rule['id']}: start and end cannot be equal; end before start means overnight")
                if not window.get("days") or any(d not in WEEKDAYS for d in window["days"]):
                    raise InputError(f"{rule['id']}: invalid days")
        return data
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        if isinstance(error, InputError):
            raise
        raise InputError(f"invalid windows file: {error}") from error

def get_zone(name: str) -> timezone | ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:
        if name == "America/Phoenix":
            return timezone(timedelta(hours=-7), name)
        raise InputError(f"unknown timezone: {name}")

def parse_now(value: str | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timestamp needs an explicit timezone")
        return parsed.astimezone(UTC)
    except ValueError as error:
        raise InputError(f"invalid --now: {error}") from error

def split_target(target: str) -> tuple[str, str]:
    if ":" not in target:
        raise InputError("target must be provider:model")
    return tuple(target.split(":", 1))  # type: ignore[return-value]

def find_rule(target: str, rules: list[dict[str, Any]]) -> dict[str, Any] | None:
    provider, model = split_target(target)
    ordered = sorted(rules, key=lambda rule: (bool(rule["match"].get("providerInstanceId")), len(rule["match"].get("model_patterns", []))), reverse=True)
    for rule in ordered:
        match = rule["match"]
        providers = match.get("providerInstanceId")
        if providers is not None and provider not in providers:
            continue
        if any(fnmatch.fnmatchcase(model.lower(), pattern.lower()) for pattern in match["model_patterns"]):
            return rule
    return None

def is_peak(at: datetime, rule: dict[str, Any]) -> bool:
    at = at.astimezone(UTC)
    holiday_zone = get_zone(rule["holidays"]["timezone"])
    if at.astimezone(holiday_zone).date().isoformat() in rule["holidays"]["dates"]:
        return False
    minute = at.hour * 60 + at.minute
    for window in rule["peak_windows_utc"]:
        start = int(window["start"][:2]) * 60 + int(window["start"][3:])
        end = int(window["end"][:2]) * 60 + int(window["end"][3:])
        weekdays = [WEEKDAYS.index(day) for day in window["days"]]
        if end > start and at.weekday() in weekdays and start <= minute < end:
            return True
        if end <= start:
            if (at.weekday() in weekdays and minute >= start) or ((at.weekday() - 1) % 7 in weekdays and minute < end):
                return True
    return False

def next_transition(at: datetime, rule: dict[str, Any], desired_peak: bool) -> datetime:
    """Find the next state transition by checking minute boundaries up to 15 days."""
    cursor = at.astimezone(UTC).replace(second=0, microsecond=0)
    state = is_peak(cursor, rule)
    if state == desired_peak:
        return cursor
    for _ in range(15 * 24 * 60 + 2):
        cursor += timedelta(minutes=1)
        if is_peak(cursor, rule) == desired_peak:
            return cursor
    raise InputError("could not find a window within 15 days")

def interval(at: datetime, rule: dict[str, Any]) -> tuple[datetime, datetime]:
    """Return contiguous off-peak interval containing at, or the next one."""
    at = at.astimezone(UTC)
    if is_peak(at, rule):
        start = next_transition(at, rule, False)
    else:
        start = at.replace(second=0, microsecond=0)
    end = next_transition(start + timedelta(minutes=1), rule, True)
    return start, end

def windows_ahead(at: datetime, rule: dict[str, Any], count: int) -> list[tuple[datetime, datetime]]:
    items: list[tuple[datetime, datetime]] = []
    cursor = at.astimezone(UTC)
    for _ in range(count):
        start, end = interval(cursor, rule)
        items.append((start, end))
        cursor = end + timedelta(minutes=1)
    return items

def fmt(value: datetime, zone: timezone | ZoneInfo) -> str:
    return value.astimezone(zone).isoformat()

def status_payload(target: str, at: datetime, zone: timezone | ZoneInfo, rule: dict[str, Any] | None) -> dict[str, Any]:
    peak = bool(rule and is_peak(at, rule))
    payload: dict[str, Any] = {"gated": bool(rule), "enforce": rule["enforce"] if rule else None,
        "rule_id": rule["id"] if rule else None, "is_peak": peak, "now_local": fmt(at, zone),
        "current_window_ends_local": None, "next_offpeak_start_local": None, "next_offpeak_start_utc": None,
        "seconds_until_offpeak": 0, "reason": "unmatched target" if not rule else ("peak pricing window" if peak else "off-peak window")}
    if rule and peak:
        start = next_transition(at, rule, False)
        payload.update(next_offpeak_start_local=fmt(start, zone), next_offpeak_start_utc=start.isoformat().replace("+00:00", "Z"), seconds_until_offpeak=int((start-at).total_seconds()))
    elif rule:
        _, end = interval(at, rule)
        payload["current_window_ends_local"] = fmt(end, zone)
    return payload

def selection_target(path: str) -> str:
    try:
        data = json.loads(Path(path).read_text())
        target = data.get("target") or data.get("canonical_target") or data.get("canonical")
        if not target and isinstance(data.get("selection"), dict):
            target = data["selection"].get("target") or data["selection"].get("canonical_target")
        if not target:
            target = f"{data['providerInstanceId']}:{data['model']}"
        split_target(target)
        return target
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise InputError(f"cannot read selection target: {error}") from error

def gate_result(target: str, args: argparse.Namespace, rules: list[dict[str, Any]]) -> tuple[int, dict[str, Any]]:
    if args.from_select:
        target = selection_target(args.from_select)
    at = parse_now(args.now)
    zone = get_zone(args.tz)
    rule = find_rule(target, rules)
    payload = status_payload(target, at, zone, rule)
    if args.force:
        payload["reason"] = "forced"
        return 0, payload
    if not rule or (rule["enforce"] == "opt-in" and not (args.opt_in or os.getenv("OFFPEAK_OPT_IN") == "1")):
        if rule and rule["enforce"] == "opt-in":
            payload["reason"] = "opt-in rule; gating disabled"
        return 0, payload
    if payload["is_peak"]:
        return 3, payload
    estimate = args.estimate_minutes
    if estimate:
        _, end = interval(at, rule)
        if (end - at).total_seconds() < estimate * 60:
            payload["is_peak"] = True
            payload["reason"] = "insufficient off-peak time for estimate"
            start = next_transition(end, rule, False)
            payload.update(next_offpeak_start_local=fmt(start, zone), next_offpeak_start_utc=start.isoformat().replace("+00:00", "Z"), seconds_until_offpeak=int((start-at).total_seconds()))
            if not any((right-left).total_seconds() >= estimate*60 for left, right in windows_ahead(start, rule, 8)):
                payload["warning"] = "no single upcoming window can fit the estimate"
            return 3, payload
    return 0, payload

def read_state(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError) as error:
        raise InputError(f"invalid state JSONL: {error}") from error

def append_state(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")

def state_path(args: argparse.Namespace) -> Path:
    return Path(args.state) if args.state else DEFAULT_STATE

def due_items(path: Path, rules: list[dict[str, Any]], now: datetime, zone: timezone | ZoneInfo) -> list[dict[str, Any]]:
    rows = read_state(path)
    done = {row["unit_id"] for row in rows if row.get("event") == "done"}
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        if "defer_until_utc" in row:
            latest[row["unit_id"]] = row
    due = []
    for unit, row in latest.items():
        if unit in done or parse_now(row["defer_until_utc"]) > now:
            continue
        rule = find_rule(row["target"], rules)
        if not rule or is_peak(now, rule):
            continue
        if row.get("estimate_minutes", 0) and (interval(now, rule)[1] - now).total_seconds() < row["estimate_minutes"] * 60:
            continue
        due.append(row)
    return due

def handler(args: argparse.Namespace, rules: list[dict[str, Any]], sleep_fn: Callable[[float], None] = time.sleep) -> int:
    at = parse_now(getattr(args, "now", None))
    zone = get_zone(getattr(args, "tz", "America/Phoenix"))
    if args.command == "status":
        rule = find_rule(args.target, rules)
        payload = status_payload(args.target, at, zone, rule)
        print(json.dumps(payload, indent=2) if args.json else json.dumps(payload))
        return 0
    if args.command == "gate":
        code, payload = gate_result(args.target, args, rules)
        print(json.dumps(payload, indent=2) if args.json else json.dumps(payload))
        return code
    if args.command == "next":
        rule = find_rule(args.target, rules)
        if not rule:
            raise InputError("target has no off-peak rule")
        print(json.dumps([{"start_local": fmt(a, zone), "end_local": fmt(b, zone), "start_utc": a.isoformat().replace("+00:00", "Z"), "end_utc": b.isoformat().replace("+00:00", "Z")} for a,b in windows_ahead(at, rule, args.count)], indent=2))
        return 0
    if args.command == "defer":
        plan = json.loads(Path(args.plan).read_text())
        unit = next(unit for unit in plan["units"] if unit["id"] == args.unit)
        target_value = unit.get("target")
        if isinstance(target_value, dict):
            target_value = f"{target_value['providerInstanceId']}:{target_value['model']}"
        if not target_value:
            raise InputError("unit has no target")
        faux = argparse.Namespace(now=args.now, tz=args.tz, from_select=None, force=False, opt_in=True, estimate_minutes=args.estimate_minutes)
        code, payload = gate_result(target_value, faux, rules)
        if code != 3:
            raise InputError("unit target is not gated now; nothing to defer")
        append_state(state_path(args), {"unit_id": args.unit, "plan": str(Path(args.plan)), "target": target_value,
            "defer_until_utc": payload["next_offpeak_start_utc"], "created": at.isoformat().replace("+00:00", "Z"), "estimate_minutes": args.estimate_minutes})
        return 0
    if args.command == "due":
        print(json.dumps(due_items(state_path(args), rules, at, zone), indent=2))
        return 0
    if args.command == "done":
        append_state(state_path(args), {"event": "done", "unit_id": args.unit, "created": at.isoformat().replace("+00:00", "Z")})
        return 0
    if args.command == "wait":
        rule = find_rule(args.target, rules)
        if not rule:
            raise InputError("target has no off-peak rule")
        if not is_peak(at, rule):
            return 0
        start = next_transition(at, rule, False)
        if (start - at).total_seconds() > args.max_wait_hours * 3600:
            print(fmt(start, zone))
            return 3
        cursor = at
        while cursor < start:
            chunk = min(600, (start - cursor).total_seconds())
            sleep_fn(chunk)
            cursor += timedelta(seconds=chunk)
        return 0
    if args.command == "schedule-hint":
        rule = find_rule(args.target, rules)
        if not rule:
            raise InputError("target has no off-peak rule")
        start = next_transition(at, rule, False) if is_peak(at, rule) else at
        prompt = args.prompt or f"Resume deferred DeepSeek work for {args.target}; run offpeak.py due and continue eligible units."
        if args.host == "t3":
            print(f"One-time T3 schedule_task at {start.isoformat().replace('+00:00','Z')} ({fmt(start, zone)}): {prompt}")
        elif args.host == "claude-code":
            print(f"One-shot CronCreate or /schedule at {fmt(start, zone)} ({start.isoformat().replace('+00:00','Z')}): {prompt}")
        else:
            print(f"{start.minute} {start.hour} {start.day} {start.month} * python3 offpeak.py due  # {prompt}; local {fmt(start, zone)}; UTC {start.isoformat().replace('+00:00','Z')}")
        return 0
    raise InputError("unknown command")

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--windows", default=str(DEFAULT_WINDOWS))
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "gate", "next", "wait", "schedule-hint"):
        item = sub.add_parser(name)
        item.add_argument("--target", required=True)
        item.add_argument("--now")
        item.add_argument("--tz", default="America/Phoenix")
        item.add_argument("--json", action="store_true")
    gate = sub.choices["gate"]
    gate.add_argument("--from-select")
    gate.add_argument("--estimate-minutes", type=int, default=0)
    gate.add_argument("--opt-in", action="store_true")
    gate.add_argument("--force", action="store_true")
    sub.choices["next"].add_argument("--count", type=int, default=5)
    sub.choices["wait"].add_argument("--max-wait-hours", type=float, default=24)
    sub.choices["schedule-hint"].add_argument("--host", choices=["t3", "claude-code", "cron"], default="t3")
    sub.choices["schedule-hint"].add_argument("--prompt", default="")
    defer = sub.add_parser("defer")
    defer.add_argument("--plan", required=True); defer.add_argument("--unit", required=True); defer.add_argument("--state"); defer.add_argument("--now"); defer.add_argument("--tz", default="America/Phoenix"); defer.add_argument("--estimate-minutes", type=int, default=0)
    due = sub.add_parser("due"); due.add_argument("--state"); due.add_argument("--now"); due.add_argument("--tz", default="America/Phoenix")
    done = sub.add_parser("done"); done.add_argument("--unit", required=True); done.add_argument("--state"); done.add_argument("--now")
    return parser

def main(argv: Sequence[str] | None = None, sleep_fn: Callable[[float], None] = time.sleep) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        rules = load_windows(Path(args.windows))["rules"]
        return handler(args, rules, sleep_fn)
    except InputError as error:
        print(str(error), file=sys.stderr)
        return 2
    except (OSError, json.JSONDecodeError, KeyError, StopIteration, TypeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
