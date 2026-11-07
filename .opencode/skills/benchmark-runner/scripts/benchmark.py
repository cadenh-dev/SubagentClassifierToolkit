#!/usr/bin/env python3
"""Generate, score, compare, and record human-reviewed model benchmarks.

Usage:
  benchmark.py plan --task task.json --targets targets.json --out plan.json
  benchmark.py score --plan plan.json --unit UNIT --repo TREE --base SHA --allow-commands
  benchmark.py compare --plan plan.json --json [--reviewed UNIT]
  benchmark.py record --plan plan.json --unit UNIT --reviewed UNIT ...

Results default to <plan_dir>/<plan_stem>.results/. Use --results to override
that directory. Orchestrator scripts are found beside this skill or selected
with --orchestrator-dir.

Exit codes:
  0  completed successfully
  1  verification command failed
  2  invalid input, missing orchestrator, or malformed benchmark data
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Any


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True)


def results_dir(plan_path: Path, override: Path | None) -> Path:
    if override is not None:
        return override
    return plan_path.resolve().parent / f"{plan_path.stem}.results"


def orchestrator_dir(override: Path | None) -> Path:
    directory = override.expanduser().resolve() if override else (
        Path(__file__).resolve().parents[2]
        / "subagent-orchestrator"
        / "scripts"
    )
    if not directory.is_dir():
        raise ValueError(
            f"orchestrator scripts not found at {directory}; install "
            "subagent-orchestrator beside benchmark-runner or pass --orchestrator-dir"
        )
    return directory


def plan(args: argparse.Namespace) -> None:
    scripts = orchestrator_dir(args.orchestrator_dir)
    task = json.loads(args.task.read_text(encoding="utf-8"))
    targets = json.loads(args.targets.read_text(encoding="utf-8"))
    if not isinstance(task, dict) or not isinstance(targets, list) or not targets:
        raise ValueError("task must be an object and targets a non-empty array")

    required = ("objective", "acceptance", "commands", "write_scope")
    if any(key not in task for key in required):
        raise ValueError("task requires objective, acceptance, commands, and write_scope")

    benchmark_id = f"bench-{secrets.token_hex(6)}"
    units: list[dict[str, Any]] = []
    for index, target in enumerate(targets, start=1):
        if (
            not isinstance(target, dict)
            or not isinstance(target.get("providerInstanceId"), str)
            or not isinstance(target.get("model"), str)
        ):
            raise ValueError("each target needs providerInstanceId and model")
        unit_id = f"{benchmark_id}-{index}"
        brief = (
            f"Run benchmark {benchmark_id}. Implement the identical task: "
            f"{task['objective']}\nAcceptance: {json.dumps(task['acceptance'])}\n"
            f"Commands: {json.dumps(task['commands'])}\n"
            f"Write scope: {json.dumps(task['write_scope'])}. "
            "Commit your work on your assigned branch."
        )
        units.append(
            {
                "id": unit_id,
                "title": f"Benchmark target {index}",
                "role": "implementation",
                "objective": task["objective"],
                "depends_on": [],
                "isolation": "worktree",
                "mode": "async",
                "target": target,
                "write_scope": task["write_scope"],
                "acceptance": task["acceptance"],
                "commands": task["commands"],
                "deliverable": (
                    f"Commit implementation on distinct branch bench/{benchmark_id}/"
                    f"{index} and report commit SHA."
                ),
                "brief": brief,
            }
        )

    output = {
        "goal": f"{benchmark_id}: {task['objective']}",
        "mode": "delegated",
        "units": units,
        "integration": {
            "order": [unit["id"] for unit in units],
            "tests": [],
            "review": True,
            "notes": f"Benchmark id {benchmark_id}; each unit uses a separate worktree and branch.",
        },
        "fallback": "Parent launches each target and reviews results.",
    }
    args.out.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    checked = run(
        [
            sys.executable,
            str(scripts / "validate_plan.py"),
            "--plan",
            str(args.out),
            "--strict",
        ]
    )
    if checked.returncode:
        args.out.unlink(missing_ok=True)
        raise ValueError(checked.stdout + checked.stderr)
    print(json.dumps({"benchmark_id": benchmark_id, "plan": str(args.out), "units": [unit["id"] for unit in units]}))


def score(args: argparse.Namespace) -> int:
    scripts = orchestrator_dir(args.orchestrator_dir)
    directory = results_dir(args.plan, args.results)
    report_path = directory / f"{args.unit}.json"
    directory.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(scripts / "verify_unit.py"),
        "--plan",
        str(args.plan),
        "--unit",
        args.unit,
        "--repo",
        str(args.repo),
        "--base",
        args.base,
    ]
    if args.allow_commands:
        command.append("--allow-commands")
    result = run(command)
    report_path.write_text(result.stdout, encoding="utf-8")
    print(json.dumps({"report": str(report_path), "exit_code": result.returncode}))
    if result.stderr:
        sys.stderr.write(result.stderr)
    return result.returncode


def pairs(values: list[str]) -> dict[str, str]:
    output: dict[str, str] = {}
    for value in values:
        key, separator, item = value.partition("=")
        if not separator or not key:
            raise ValueError(f"expected id=value: {value}")
        output[key] = item
    return output


def diff_stats(report: dict[str, Any], repo_override: Path | None) -> dict[str, int | None]:
    git_info = report.get("git") or {}
    base = git_info.get("base_commit")
    head = git_info.get("head_commit")
    repo = repo_override or Path(report.get("repo") or Path.cwd())
    if base and head:
        process = run(
            ["git", "-C", str(repo), "diff", "--shortstat", f"{base}..{head}"]
        )
        if process.returncode == 0:
            match = re.search(
                r"(\d+) files? changed(?:, (\d+) insertions?\(\+\))?"
                r"(?:, (\d+) deletions?\(-\))?",
                process.stdout,
            )
            if match:
                return {
                    "files": int(match[1]),
                    "insertions": int(match[2] or 0),
                    "deletions": int(match[3] or 0),
                }
            return {"files": 0, "insertions": 0, "deletions": 0}
    paths = report.get("changed_paths", [])
    return {"files": len(paths) if isinstance(paths, list) else 0, "insertions": None, "deletions": None}


def compare(args: argparse.Namespace) -> None:
    benchmark_plan = json.loads(args.plan.read_text(encoding="utf-8"))
    directory = results_dir(args.plan, args.results)
    reviewed = set(args.reviewed)
    findings = pairs(args.review_findings)
    durations = pairs(args.duration)
    usage = pairs(args.usage)
    rows: list[dict[str, Any]] = []

    for unit in benchmark_plan["units"]:
        report_path = directory / f"{unit['id']}.json"
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
        commands = report.get("command_results", [])
        test_pass = (
            bool(commands)
            and all(item.get("exit_code") == 0 for item in commands)
        )
        violations = report.get("scope_violations", [])
        rows.append(
            {
                "id": unit["id"],
                "target": unit["target"],
                "verdict": report.get("verdict", "unknown"),
                "test_pass": test_pass,
                "out_of_scope": violations,
                "review_findings": int(findings[unit["id"]]) if unit["id"] in findings else None,
                "duration": float(durations[unit["id"]]) if unit["id"] in durations else None,
                "usage": usage.get(unit["id"]),
                "diff": diff_stats(report, args.repo),
                "review_status": "reviewed" if unit["id"] in reviewed else "unreviewed",
            }
        )

    if args.json:
        print(json.dumps(rows, indent=2))
        return
    print("ID | Target | Tests | Out of scope | Findings | Duration | Usage | Diff | Review")
    for row in rows:
        target = row["target"]
        findings_value = row["review_findings"] if row["review_findings"] is not None else "unknown"
        duration_value = row["duration"] if row["duration"] is not None else "unknown"
        usage_value = row["usage"] or "unknown"
        print(
            f"{row['id']} | {target['providerInstanceId']}:{target['model']} | "
            f"{row['test_pass']} | {len(row['out_of_scope'])} | {findings_value} | "
            f"{duration_value} | {usage_value} | {row['diff']} | {row['review_status']}"
        )


def record(args: argparse.Namespace) -> int:
    if args.unit not in set(args.reviewed):
        raise ValueError("refusing to record unreviewed unit; pass --reviewed <id>")
    scripts = orchestrator_dir(args.orchestrator_dir)
    report = results_dir(args.plan, args.results) / f"{args.unit}.json"
    command = [
        sys.executable,
        str(scripts / "record_verification.py"),
        "--report",
        str(report),
        "--depth",
        str(args.depth),
        "--outcome",
        args.outcome,
        "--verification",
        args.verification,
        "--retries",
        str(args.retries),
        "--review-findings",
        str(args.review_findings),
    ]
    if args.acceptance_reviewed:
        command.append("--acceptance-reviewed")
    for key in (
        "estimated_units",
        "actual_units",
        "duration",
        "pr",
        "notes",
        "ledger",
    ):
        value = getattr(args, key, None)
        if value is not None:
            command.extend(["--" + key.replace("_", "-"), str(value)])
    result = run(command)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


def add_orchestrator_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--orchestrator-dir",
        type=Path,
        help="Path to subagent-orchestrator/scripts.",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    plan_parser = commands.add_parser("plan")
    plan_parser.add_argument("--task", type=Path, required=True)
    plan_parser.add_argument("--targets", type=Path, required=True)
    plan_parser.add_argument("--out", type=Path, required=True)
    add_orchestrator_argument(plan_parser)
    plan_parser.set_defaults(function=plan)

    score_parser = commands.add_parser("score")
    score_parser.add_argument("--plan", type=Path, required=True)
    score_parser.add_argument("--unit", required=True)
    score_parser.add_argument("--repo", type=Path, required=True)
    score_parser.add_argument("--base", required=True)
    score_parser.add_argument("--results", type=Path)
    score_parser.add_argument("--allow-commands", action="store_true")
    add_orchestrator_argument(score_parser)
    score_parser.set_defaults(function=score)

    compare_parser = commands.add_parser("compare")
    compare_parser.add_argument("--plan", type=Path, required=True)
    compare_parser.add_argument("--results", type=Path)
    compare_parser.add_argument("--repo", type=Path)
    compare_parser.add_argument("--review-findings", action="append", default=[])
    compare_parser.add_argument("--duration", action="append", default=[])
    compare_parser.add_argument("--usage", action="append", default=[])
    compare_parser.add_argument("--reviewed", action="append", default=[])
    compare_parser.add_argument("--json", action="store_true")
    compare_parser.set_defaults(function=compare)

    record_parser = commands.add_parser("record")
    record_parser.add_argument("--plan", type=Path, required=True)
    record_parser.add_argument("--unit", required=True)
    record_parser.add_argument("--reviewed", action="append", default=[])
    record_parser.add_argument("--results", type=Path)
    record_parser.add_argument("--depth", type=int, choices=range(1, 6), required=True)
    record_parser.add_argument("--outcome", choices=("success", "failure", "partial", "cancelled"), required=True)
    record_parser.add_argument("--verification", choices=("parent", "child-report", "none"), required=True)
    record_parser.add_argument("--retries", type=int, required=True)
    record_parser.add_argument("--review-findings", type=int, required=True)
    record_parser.add_argument("--acceptance-reviewed", action="store_true")
    for key in ("estimated_units", "actual_units", "duration", "pr", "notes", "ledger"):
        argument_type = Path if key == "ledger" else str
        record_parser.add_argument("--" + key.replace("_", "-"), type=argument_type)
    add_orchestrator_argument(record_parser)
    record_parser.set_defaults(function=record)

    args = parser.parse_args(argv)
    try:
        result = args.function(args)
        return result if isinstance(result, int) else 0
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
