#!/usr/bin/env python3
"""End-to-end tests for the subagent-orchestrator helpers.

Run:
  python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".opencode" / "skills" / "subagent-orchestrator"
SKILL_MD = SKILL / "SKILL.md"
SELECT = SKILL / "scripts" / "select_target.py"
VALIDATE = SKILL / "scripts" / "validate_plan.py"
CAPS = ROOT / "tests" / "fixtures" / "capabilities.json"
EXAMPLE = SKILL / "assets" / "plan.example.json"


class SkillMetadataTests(unittest.TestCase):
    def test_name_matches_directory_and_is_valid(self) -> None:
        text = SKILL_MD.read_text(encoding="utf-8")
        match = re.match(r"^---\n(.*?)\n---", text, re.S)
        self.assertIsNotNone(match)
        frontmatter = match.group(1)
        name = re.search(r"^name:\s*(.+)$", frontmatter, re.M)
        description = re.search(r"^description:\s*(.+)$", frontmatter, re.M)
        self.assertIsNotNone(name)
        self.assertIsNotNone(description)
        self.assertEqual(name.group(1).strip(), SKILL.name)
        self.assertRegex(name.group(1).strip(), r"^[a-z0-9]+(-[a-z0-9]+)*$")
        self.assertLessEqual(len(description.group(1).strip()), 1024)

    def test_json_assets_parse(self) -> None:
        for path in SKILL.rglob("*.json"):
            json.loads(path.read_text(encoding="utf-8"))

    def test_referenced_local_files_exist(self) -> None:
        text = SKILL_MD.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
            if target.startswith(("http://", "https://", "#")):
                continue
            self.assertTrue((SKILL / target).exists(), target)



def run(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *args],
        text=True,
        capture_output=True,
        cwd=str(ROOT),
    )


class SelectTargetTests(unittest.TestCase):
    def test_cost_mode_prefers_parent_when_capable(self) -> None:
        result = run(
            SELECT,
            "--capabilities", str(CAPS),
            "--role", "implementation",
            "--depth", "3",
            "--parent-provider", "opencode",
            "--parent-model", "opencode-go/deepseek-v4.1-flash",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.strip(),
            "opencode:opencode-go/deepseek-v4.1-flash#variant=high",
        )

    def test_cost_mode_picks_cheap_target_without_parent(self) -> None:
        result = run(
            SELECT,
            "--capabilities", str(CAPS),
            "--role", "implementation",
            "--depth", "2",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "ok")
        self.assertIn(payload["target"]["providerInstanceId"], {"antigravity", "opencode"})
        self.assertLessEqual(payload["cost_tier"], 1)

    def test_cost_mode_raises_effort_for_depth(self) -> None:
        result = run(
            SELECT,
            "--capabilities", str(CAPS),
            "--role", "review",
            "--depth", "4",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["canonical"], "codex:gpt-6.1-sol#reasoningEffort=xhigh,serviceTier=default")

    def test_max_mode_picks_highest_reasoning(self) -> None:
        result = run(
            SELECT,
            "--capabilities", str(CAPS),
            "--role", "review",
            "--depth", "4",
            "--ignore-cost",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["canonical"], "codex:gpt-6.1-sol#reasoningEffort=ultra,serviceTier=default")
        self.assertEqual(payload["reasoning"], 5)

    def test_disabled_provider_is_never_selected(self) -> None:
        result = run(
            SELECT,
            "--capabilities", str(CAPS),
            "--role", "review",
            "--depth", "5",
            "--ignore-cost",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "ok")
        self.assertNotEqual(payload["target"]["providerInstanceId"], "claudeAgent")

    def test_unavailable_when_no_target_meets_depth(self) -> None:
        result = run(
            SELECT,
            "--capabilities", str(CAPS),
            "--role", "review",
            "--depth", "5",
            "--exclude", "codex:gpt-6.1-sol",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(result.stdout.strip().splitlines()[0], "{")

    def test_route_only_stdout_is_one_line(self) -> None:
        result = run(
            SELECT,
            "--capabilities", str(CAPS),
            "--role", "research",
            "--depth", "3",
            "--ignore-cost",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1)
        self.assertIn(":", lines[0])


class ValidatePlanTests(unittest.TestCase):
    def test_example_plan_is_valid(self) -> None:
        result = run(VALIDATE, "--plan", str(EXAMPLE), "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["errors"], [])

    def test_cycle_is_rejected(self) -> None:
        plan = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        plan["units"][0]["depends_on"] = ["migration-review"]
        plan["units"][2]["depends_on"] = ["api-auth"]
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(plan, f)
            path = f.name
        try:
            result = run(VALIDATE, "--plan", path, "--json")
            self.assertEqual(result.returncode, 1)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["ok"])
            self.assertTrue(any("cycle" in e for e in payload["errors"]), payload)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_missing_acceptance_is_rejected(self) -> None:
        plan = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        plan["units"][0]["acceptance"] = []
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(plan, f)
            path = f.name
        try:
            result = run(VALIDATE, "--plan", path, "--json")
            self.assertEqual(result.returncode, 1)
            payload = json.loads(result.stdout)
            self.assertTrue(any("acceptance" in e for e in payload["errors"]), payload)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_strict_promotes_warnings(self) -> None:
        plan = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        plan["units"][2]["isolation"] = "worktree"
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(plan, f)
            path = f.name
        try:
            result = run(VALIDATE, "--plan", path, "--strict", "--json")
            self.assertEqual(result.returncode, 1)
            payload = json.loads(result.stdout)
            self.assertTrue(any("review" in w for w in payload["warnings"]), payload)
        finally:
            Path(path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
