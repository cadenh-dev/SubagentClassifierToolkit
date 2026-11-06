#!/usr/bin/env python3
"""Create an outcome-ledger record from a verify_unit JSON report.

This helper carries trusted plan metadata into the ledger. The caller must still
choose the outcome, task depth, verification level, and retry/review counts.
Acceptance prose is not inferred from a passing command/scope verdict.

Example:
  record_verification.py --report verify.json --depth 3 --outcome partial \
      --verification parent --retries 0 --review-findings 1
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

LEDGER_MODULE_PATH = Path(__file__).resolve().with_name("outcome_ledger.py")
VERIFY_TOOL = "verify_unit"
VERIFY_SCHEMA_VERSION = 1


def _load_ledger_module() -> Any:
    spec = importlib.util.spec_from_file_location("verification_outcome_ledger", LEDGER_MODULE_PATH)
    if spec is None or spec.loader is None:
        raise ValueError(f"could not load outcome ledger module: {LEDGER_MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_report(path: Path) -> dict[str, Any]:
    try:
        report = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise ValueError(f"verification report not found: {path}") from None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"could not read verification report {path}: {exc}") from None
    if not isinstance(report, dict):
        raise ValueError("verification report must be a JSON object")
    if report.get("tool") != VERIFY_TOOL or report.get("schema_version") != VERIFY_SCHEMA_VERSION:
        raise ValueError("report is not a supported verify_unit report")
    if report.get("verdict") not in {"pass", "fail", "unverified", "blocked", "error"}:
        raise ValueError("verification report has an invalid verdict")
    details = report.get("unit_details")
    if not isinstance(details, dict):
        raise ValueError("verification report is missing unit_details")
    target = details.get("target")
    if not isinstance(target, dict):
        raise ValueError("verification report is missing target metadata")
    return report


def _record_args(args: argparse.Namespace, report: dict[str, Any]) -> argparse.Namespace:
    details = report["unit_details"]
    target = details["target"]
    options = target.get("options", {})
    if not isinstance(options, dict):
        raise ValueError("verification target options must be an object")
    raw_options: list[str] = []
    for key, value in options.items():
        if not isinstance(key, str) or not key.strip() or not isinstance(value, (str, bool)):
            raise ValueError("verification target options must contain string keys and scalar values")
        raw_options.append(f"{key}={str(value).lower() if isinstance(value, bool) else value}")

    if args.outcome == "success":
        if report["verdict"] != "pass":
            raise ValueError("outcome success requires a passing verification report")
        if not args.acceptance_reviewed:
            raise ValueError(
                "outcome success requires --acceptance-reviewed; verify_unit does not evaluate acceptance prose"
            )

    return argparse.Namespace(
        unit=details.get("id"),
        role=details.get("role"),
        depth=args.depth,
        provider=target.get("providerInstanceId"),
        model=target.get("model"),
        option=raw_options,
        isolation=details.get("isolation"),
        mode=details.get("mode"),
        outcome=args.outcome,
        verification=args.verification,
        estimated_units=args.estimated_units,
        actual_units=args.actual_units,
        duration=args.duration,
        retries=args.retries,
        review_findings=args.review_findings,
        pr=args.pr,
        notes=args.notes,
        recorded_at=None,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path, help="verify_unit JSON report")
    parser.add_argument("--depth", required=True, type=int, choices=range(1, 6))
    parser.add_argument("--outcome", required=True, choices=("success", "failure", "partial", "cancelled"))
    parser.add_argument("--verification", required=True, choices=("parent", "child-report", "none"))
    parser.add_argument("--retries", required=True, type=int)
    parser.add_argument("--review-findings", required=True, type=int)
    parser.add_argument("--acceptance-reviewed", action="store_true", help="confirm acceptance prose was checked; required for success")
    parser.add_argument("--estimated-units", type=float)
    parser.add_argument("--actual-units", type=float, help="omit when provider usage is unknown")
    parser.add_argument("--duration", type=float, help="whole task duration, not verifier runtime")
    parser.add_argument("--pr")
    parser.add_argument("--notes")
    parser.add_argument("--ledger", type=Path, help="JSONL path (default: outcome ledger location)")
    args = parser.parse_args(argv)

    try:
        ledger = _load_ledger_module()
        report = _load_report(args.report)
        record = ledger.build_record(_record_args(args, report))
        ledger_path = args.ledger or ledger.DEFAULT_LEDGER
        ledger.append_record(ledger_path, record)
    except (ValueError, OSError, ImportError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(json.dumps({"recorded": True, "ledger": str(ledger_path), "record": record}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
