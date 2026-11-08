#!/usr/bin/env python3
"""Write a fix-round brief for a unit that failed a Phase 7 gate.

Input is the delegation plan, plus either a failing verify_unit report, review
findings, or both. Output is one self-contained brief on stdout for a new
delegate_task. The script reads files only; it never launches anything.

Examples:
  fix_brief.py --plan plan.json --unit api-auth --report verify.json --round 1
  fix_brief.py --plan plan.json --unit api-auth --findings review.json \\
      --branch auth-rotation --head 9c1e2f0 --round 2

Findings file: a JSON list of {"severity", "location", "summary"} objects (or
{"findings": [...]}), or plain text with one finding per line.

Exit codes:
  0  brief written
  1  round limit reached: escalate to the user instead of another round
  2  bad input, or nothing to fix
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

VALIDATE_PATH = Path(__file__).resolve().with_name("validate_plan.py")
VERIFY_TOOL = "verify_unit"
VERIFY_SCHEMA_VERSION = 1
DEFAULT_MAX_ROUNDS = 2
DEFAULT_TAIL_LINES = 40

# High-confidence token shapes. Command output can echo environment values;
# a brief must never carry them.
SECRET_PATTERNS = [
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S),
    # Environment-style assignments such as DB_PASSWORD=... or GITHUB_TOKEN=...
    re.compile(r"\b[A-Z0-9_]*(?:PASSWORD|PASSWD|SECRET|TOKEN|API_KEY|APIKEY)[A-Z0-9_]*=\S+"),
]


class InputError(ValueError):
    pass


def _load_validator() -> Any:
    spec = importlib.util.spec_from_file_location("fix_brief_validate_plan", VALIDATE_PATH)
    if spec is None or spec.loader is None:
        raise InputError(f"could not load {VALIDATE_PATH.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def redact(text: str) -> str:
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def tail(text: str, lines: int) -> str:
    parts = text.rstrip("\n").splitlines()
    if len(parts) <= lines:
        return "\n".join(parts)
    return f"[... {len(parts) - lines} earlier lines omitted]\n" + "\n".join(parts[-lines:])


def load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise InputError(f"cannot read {label} {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise InputError(f"{label} {path} is not valid JSON: {exc}") from exc


def load_unit(plan_path: Path, unit_id: str) -> dict[str, Any]:
    plan = load_json(plan_path, "plan")
    validator = _load_validator()
    errors, _warnings = validator.validate(plan)
    if errors:
        raise InputError("plan is invalid: " + "; ".join(errors[:5]))
    for unit in plan.get("units", []):
        if unit.get("id") == unit_id:
            return unit
    raise InputError(f"unit {unit_id!r} is not in the plan")


def load_report(path: Path, unit_id: str) -> dict[str, Any]:
    report = load_json(path, "report")
    if not isinstance(report, dict) or report.get("tool") != VERIFY_TOOL or report.get("schema_version") != VERIFY_SCHEMA_VERSION:
        raise InputError(f"{path} is not a verify_unit schema {VERIFY_SCHEMA_VERSION} report")
    if report.get("unit") != unit_id:
        raise InputError(f"report is for unit {report.get('unit')!r}, not {unit_id!r}")
    verdict = report.get("verdict")
    if verdict in {"blocked", "error"}:
        raise InputError(
            f"report verdict is {verdict!r}: the harness could not verify the unit. "
            "Fix the verification setup; that is not the child's failure."
        )
    if verdict == "unverified":
        raise InputError("report verdict is 'unverified' (no commands). Add commands to the unit or check it by hand.")
    return report


def load_findings(path: Path) -> list[dict[str, str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InputError(f"cannot read findings {path}: {exc}") from exc
    stripped = text.strip()
    if stripped.startswith(("[", "{")):
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise InputError(f"findings {path} looks like JSON but does not parse: {exc}") from exc
        items = data.get("findings") if isinstance(data, dict) else data
        if not isinstance(items, list):
            raise InputError("findings JSON must be a list or an object with a 'findings' list")
        findings = []
        for item in items:
            if isinstance(item, str):
                findings.append({"summary": item})
            elif isinstance(item, dict) and item.get("summary"):
                findings.append({k: str(item[k]) for k in ("severity", "location", "summary") if item.get(k)})
            else:
                raise InputError(f"finding without a summary: {item!r}")
        return findings
    return [{"summary": line.strip().lstrip("-* ").strip()} for line in text.splitlines() if line.strip()]


def failing_commands(report: dict[str, Any], tail_lines: int) -> list[str]:
    blocks = []
    for result in report.get("command_results", []):
        status = result.get("status")
        if status == "pass":
            continue
        if status == "not_run":
            blocks.append(f"- `{result.get('command')}`: not run (an earlier command failed)")
            continue
        detail = f"exit {result.get('exit_code')}" if result.get("exit_code") is not None else status
        if result.get("timed_out"):
            detail = "timed out"
        out = []
        for stream in ("stdout", "stderr"):
            body = result.get(stream) or ""
            if body.strip():
                out.append(f"{stream} (last lines):\n" + redact(tail(body, tail_lines)))
        block = f"- `{result.get('command')}`: {detail}"
        if out:
            block += "\n\n```text\n" + "\n\n".join(out) + "\n```"
        blocks.append(block)
    return blocks


def build_brief(
    *,
    unit: dict[str, Any],
    report: dict[str, Any] | None,
    findings: list[dict[str, str]],
    round_no: int,
    max_rounds: int,
    branch: str | None,
    head: str | None,
    tail_lines: int,
) -> str:
    git = (report or {}).get("git", {})
    branch = branch or git.get("branch")
    head = head or git.get("head_commit")
    if not branch or not head:
        raise InputError("branch and head are unknown: pass --branch and --head, or a report from the unit's branch")

    problems: list[str] = []
    if report is not None and report.get("verdict") == "fail":
        problems.extend(failing_commands(report, tail_lines))
        for path in report.get("scope_violations", []):
            problems.append(f"- Out of write scope: `{path}`. Revert this change; do not widen the scope yourself.")
        for scope in report.get("invalid_write_scope", []):
            problems.append(f"- Invalid write scope entry in the plan: `{scope}`. Report this; do not work around it.")
    for finding in findings:
        prefix = " ".join(filter(None, [finding.get("severity", "").upper(), finding.get("location")]))
        problems.append(f"- {prefix + ': ' if prefix else ''}{redact(finding['summary'])}")
    if not problems:
        raise InputError("nothing to fix: the report passed and no findings were given")

    write_scope = unit.get("write_scope") or []
    lines = [
        f"ROLE: {unit.get('role', 'implementation')}",
        f"OBJECTIVE: Fix round {round_no} of {max_rounds} for unit {unit['id']}: make it pass the problems listed below. "
        f"Original objective: {unit.get('objective', '').strip()}",
        "",
        "CONTEXT",
        f"- Work on branch {branch}, starting from commit {head}. Commit your fixes on that branch and push it.",
        "- A previous child implemented this unit. Its report is not evidence; the problems below are.",
        "",
        "PROBLEMS TO FIX",
        *problems,
        "",
        "WRITE SCOPE",
        *([f"- {s}" for s in write_scope] or ["- none declared; make no file changes and report back"]),
        "",
        "ACCEPTANCE",
        *[f"- {a}" for a in unit.get("acceptance", [])],
        "- Every problem above is fixed, with a test where the problem is testable.",
        "- No files outside the write scope change.",
    ]
    commands = unit.get("commands") or []
    if commands:
        lines += ["", "COMMANDS (run all of them before you report)", *[f"- {c}" for c in commands]]
    lines += [
        "",
        "RULES",
        "- Fix only the problems listed. No unrelated cleanup or refactoring.",
        "- If a problem is wrong or cannot be fixed inside the write scope, say so and stop. Do not work around it.",
        "- Do not start subagents.",
        "",
        "REPORT",
        "- For each problem: fixed or not fixed, with file:line.",
        "- The exact commands you ran and their results.",
        "- The new head commit SHA.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--unit", required=True)
    parser.add_argument("--report", type=Path, help="verify_unit JSON report for the unit's current head")
    parser.add_argument("--findings", type=Path, help="review or spec-check findings (JSON or one per line)")
    parser.add_argument("--round", dest="round_no", type=int, default=1, help="fix round number, starting at 1")
    parser.add_argument("--max-rounds", type=int, default=DEFAULT_MAX_ROUNDS)
    parser.add_argument("--branch", help="unit branch (default: from the report)")
    parser.add_argument("--head", help="unit head commit (default: from the report)")
    parser.add_argument("--tail-lines", type=int, default=DEFAULT_TAIL_LINES, help="output lines kept per failing command")
    args = parser.parse_args(argv)

    if args.report is None and args.findings is None:
        print("error: pass --report, --findings, or both", file=sys.stderr)
        return 2
    if args.round_no < 1 or args.max_rounds < 1 or args.tail_lines < 1:
        print("error: --round, --max-rounds, and --tail-lines must be at least 1", file=sys.stderr)
        return 2
    if args.round_no > args.max_rounds:
        print(
            f"escalate: unit {args.unit} has used {args.max_rounds} fix round(s). Report the history to the user "
            "and ask whether to raise the target, re-split the unit, or change approach.",
            file=sys.stderr,
        )
        return 1
    try:
        unit = load_unit(args.plan, args.unit)
        report = load_report(args.report, args.unit) if args.report else None
        findings = load_findings(args.findings) if args.findings else []
        brief = build_brief(
            unit=unit,
            report=report,
            findings=findings,
            round_no=args.round_no,
            max_rounds=args.max_rounds,
            branch=args.branch,
            head=args.head,
            tail_lines=args.tail_lines,
        )
    except InputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(brief)
    return 0


if __name__ == "__main__":
    sys.exit(main())
