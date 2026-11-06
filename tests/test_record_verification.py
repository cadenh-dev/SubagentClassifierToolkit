"""Tests for converting reviewed verification reports to ledger evidence."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".opencode" / "skills" / "subagent-orchestrator" / "scripts" / "record_verification.py"


class RecordVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.report = self.root / "verify.json"
        self.ledger = self.root / "outcomes.jsonl"
        self.write_report()

    def write_report(self, *, verdict: str = "pass") -> None:
        self.report.write_text(
            json.dumps(
                {
                    "tool": "verify_unit",
                    "schema_version": 1,
                    "verdict": verdict,
                    "unit_details": {
                        "id": "api-auth",
                        "role": "implementation",
                        "isolation": "worktree",
                        "mode": "async",
                        "target": {
                            "providerInstanceId": "opencode",
                            "model": "deepseek-v4.1-flash",
                            "options": {"reasoningEffort": "high", "fastMode": True},
                        },
                        "acceptance_evaluated": False,
                    },
                }
            ),
            encoding="utf-8",
        )

    def run_record(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--report",
                str(self.report),
                "--depth",
                "3",
                "--outcome",
                "partial",
                "--verification",
                "parent",
                "--retries",
                "1",
                "--review-findings",
                "2",
                "--ledger",
                str(self.ledger),
                *extra,
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    def records(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.ledger.read_text(encoding="utf-8").splitlines()]

    def test_records_plan_metadata_and_explicit_review_fields(self) -> None:
        result = self.run_record("--actual-units", "1234")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        record = self.records()[0]
        self.assertEqual(record["unit"], "api-auth")
        self.assertEqual(record["role"], "implementation")
        self.assertEqual(record["depth"], 3)
        self.assertEqual(record["target"]["providerInstanceId"], "opencode")
        self.assertEqual(record["target"]["options"], {"reasoningEffort": "high", "fastMode": True})
        self.assertEqual(record["outcome"], "partial")
        self.assertEqual(record["verification"], "parent")
        self.assertEqual(record["retries"], 1)
        self.assertEqual(record["review_findings"], 2)
        self.assertEqual(record["actual_units"], 1234)
        self.assertIsNone(record["estimated_units"])

    def test_success_requires_passing_report_and_acceptance_confirmation(self) -> None:
        success_args = [
            "--outcome",
            "success",
            "--acceptance-reviewed",
        ]
        failed_report = self.root / "failed.json"
        self.report.replace(failed_report)
        self.report = failed_report
        self.write_report(verdict="fail")
        result = self.run_record(*success_args)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.ledger.exists())

    def test_success_needs_acceptance_review_flag(self) -> None:
        result = self.run_record("--outcome", "success")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--acceptance-reviewed", result.stderr)
        self.assertFalse(self.ledger.exists())

    def test_rejects_unrelated_or_malformed_report(self) -> None:
        self.report.write_text("{}", encoding="utf-8")
        result = self.run_record()
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.ledger.exists())


if __name__ == "__main__":
    unittest.main()
