"""Tests for building fix-round briefs from verification evidence."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".opencode" / "skills" / "subagent-orchestrator"
SCRIPT = SKILL / "scripts" / "fix_brief.py"
EXAMPLE_PLAN = SKILL / "assets" / "plan.example.json"


def report(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "tool": "verify_unit",
        "schema_version": 1,
        "verdict": "fail",
        "unit": "api-auth",
        "git": {"branch": "auth-refresh-rotation", "head_commit": "9c1e2f0"},
        "scope_violations": [],
        "invalid_write_scope": [],
        "command_results": [
            {
                "command": "pnpm test --filter auth",
                "status": "fail",
                "exit_code": 1,
                "timed_out": False,
                "stdout": "\n".join(f"line {i}" for i in range(100)) + "\nFAIL replay accepted",
                "stderr": "GITHUB_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz0123456789 leaked",
            }
        ],
    }
    base.update(overrides)
    return base


class FixBriefTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name: str, payload: Any) -> Path:
        path = self.root / name
        path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
        return path

    def run_script(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--plan", str(EXAMPLE_PLAN), "--unit", "api-auth", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def test_failing_report_becomes_brief(self) -> None:
        result = self.run_script("--report", str(self.write("v.json", report())), "--round", "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        brief = result.stdout
        self.assertIn("Fix round 1 of 2 for unit api-auth", brief)
        self.assertIn("branch auth-refresh-rotation, starting from commit 9c1e2f0", brief)
        self.assertIn("`pnpm test --filter auth`: exit 1", brief)
        self.assertIn("FAIL replay accepted", brief)
        self.assertIn("earlier lines omitted", brief)
        self.assertNotIn("line 5\n", brief)
        self.assertIn("- services/auth/**", brief)
        self.assertIn("A used refresh token cannot be replayed.", brief)

    def test_secrets_in_output_are_redacted(self) -> None:
        result = self.run_script("--report", str(self.write("v.json", report())))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz0123456789", result.stdout)
        self.assertIn("[REDACTED]", result.stdout)

    def test_scope_violations_listed(self) -> None:
        payload = report(command_results=[], scope_violations=["shared/config.ts"])
        result = self.run_script("--report", str(self.write("v.json", payload)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Out of write scope: `shared/config.ts`", result.stdout)

    def test_findings_json_and_text(self) -> None:
        findings = self.write(
            "f.json",
            [{"severity": "high", "location": "services/auth/rotate.ts:42", "summary": "replay accepted"}],
        )
        result = self.run_script("--findings", str(findings), "--branch", "b", "--head", "abc123")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("- HIGH services/auth/rotate.ts:42: replay accepted", result.stdout)

        text = self.write("f.txt", "- missing expiry test\n\n* off-by-one in ttl\n")
        result = self.run_script("--findings", str(text), "--branch", "b", "--head", "abc123")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("- missing expiry test", result.stdout)
        self.assertIn("- off-by-one in ttl", result.stdout)

    def test_round_limit_escalates(self) -> None:
        result = self.run_script("--report", str(self.write("v.json", report())), "--round", "3")
        self.assertEqual(result.returncode, 1)
        self.assertIn("escalate", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_nothing_to_fix(self) -> None:
        payload = report(verdict="pass", command_results=[])
        result = self.run_script("--report", str(self.write("v.json", payload)))
        self.assertEqual(result.returncode, 2)
        self.assertIn("nothing to fix", result.stderr)

    def test_harness_failures_are_not_child_failures(self) -> None:
        for verdict in ("blocked", "error", "unverified"):
            with self.subTest(verdict=verdict):
                result = self.run_script("--report", str(self.write("v.json", report(verdict=verdict))))
                self.assertEqual(result.returncode, 2)

    def test_wrong_unit_or_tool_rejected(self) -> None:
        result = self.run_script("--report", str(self.write("v.json", report(unit="web-auth"))))
        self.assertEqual(result.returncode, 2)
        self.assertIn("not 'api-auth'", result.stderr)
        result = self.run_script("--report", str(self.write("v.json", report(tool="other"))))
        self.assertEqual(result.returncode, 2)

    def test_needs_branch_and_head(self) -> None:
        result = self.run_script("--findings", str(self.write("f.txt", "a finding\n")))
        self.assertEqual(result.returncode, 2)
        self.assertIn("--branch", result.stderr)

    def test_requires_some_evidence(self) -> None:
        result = self.run_script()
        self.assertEqual(result.returncode, 2)

    def test_unknown_unit(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--plan", str(EXAMPLE_PLAN), "--unit", "nope", "--findings", str(self.write("f.txt", "x\n")), "--branch", "b", "--head", "h"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("not in the plan", result.stderr)


if __name__ == "__main__":
    unittest.main()
