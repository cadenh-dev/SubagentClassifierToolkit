#!/usr/bin/env python3
"""End-to-end tests for the subagent-orchestrator helpers.

Run:
  python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".opencode" / "skills" / "subagent-orchestrator"
SKILL_MD = SKILL / "SKILL.md"
SELECT = SKILL / "scripts" / "select_target.py"
VALIDATE = SKILL / "scripts" / "validate_plan.py"
CAPS = ROOT / "tests" / "fixtures" / "capabilities.json"
USAGE = ROOT / "tests" / "fixtures" / "usage.json"
POLICY = SKILL / "data" / "routing-policy.json"
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


@contextmanager
def temp_json(payload: Any) -> Iterator[str]:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
        json.dump(payload, handle)
        path = handle.name
    try:
        yield path
    finally:
        Path(path).unlink(missing_ok=True)


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def minimal_plan() -> dict[str, Any]:
    return {
        "goal": "Ship one small unit.",
        "mode": "delegated",
        "units": [
            {
                "id": "one",
                "title": "One unit",
                "role": "implementation",
                "objective": "Make the change.",
                "depends_on": [],
                "isolation": "worktree",
                "mode": "async",
                "target": {"providerInstanceId": "codex", "model": "gpt-6.1-sol"},
                "write_scope": ["src/**"],
                "commands": ["python -m unittest"],
                "acceptance": ["The change works."],
                "deliverable": "A PR.",
                "brief": "Do the work.",
            }
        ],
        "integration": {"order": ["one"], "tests": ["python -m unittest"], "review": False},
    }


def two_unit_plan() -> dict[str, Any]:
    plan = minimal_plan()
    second = json.loads(json.dumps(plan["units"][0]))
    second.update(
        {
            "id": "two",
            "title": "Two unit",
            "role": "test",
            "isolation": "shared",
            "write_scope": ["tests/**"],
        }
    )
    plan["units"].append(second)
    plan["integration"]["order"] = ["one", "two"]
    return plan


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

    def test_usage_excludes_over_budget_target(self) -> None:
        result = run(
            SELECT,
            "--capabilities", str(CAPS),
            "--role", "review",
            "--depth", "4",
            "--usage", str(USAGE),
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "budget_exceeded")
        self.assertTrue(payload["budget"]["checked"])

    def test_usage_selects_affordable_parent(self) -> None:
        result = run(
            SELECT,
            "--capabilities", str(CAPS),
            "--role", "implementation",
            "--depth", "3",
            "--parent-provider", "opencode",
            "--parent-model", "opencode-go/deepseek-v4.1-flash",
            "--usage", str(USAGE),
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["target"]["providerInstanceId"], "opencode")
        self.assertEqual(payload["remaining_units"], 1000000)
        self.assertTrue(payload["budget"]["checked"])

    def test_unknown_budget_requires_opt_in(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"unit": "tokens", "providers": {"opencode": {"remaining": 1000000}}}, f)
            path = f.name
        try:
            blocked = run(
                SELECT,
                "--capabilities", str(CAPS),
                "--role", "review",
                "--depth", "4",
                "--usage", path,
                "--json",
            )
            self.assertEqual(blocked.returncode, 0, blocked.stderr)
            self.assertEqual(json.loads(blocked.stdout)["status"], "budget_exceeded")

            allowed = run(
                SELECT,
                "--capabilities", str(CAPS),
                "--role", "review",
                "--depth", "4",
                "--usage", path,
                "--allow-unknown-budget",
                "--json",
            )
            self.assertEqual(allowed.returncode, 0, allowed.stderr)
            self.assertEqual(json.loads(allowed.stdout)["target"]["providerInstanceId"], "codex")
        finally:
            Path(path).unlink(missing_ok=True)

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

    def test_duplicate_option_ids_are_rejected(self) -> None:
        caps = {
            "providers": [
                {
                    "providerInstanceId": "opencode",
                    "canRunChildTask": True,
                    "canRunCrossProviderChildTask": True,
                    "constraints": [],
                    "models": [
                        {
                            "id": "opencode-go/deepseek-v4.1-flash",
                            "options": [
                                {"id": "variant", "values": ["low"]},
                                {"id": "variant", "values": ["high"]},
                            ],
                        }
                    ],
                }
            ]
        }
        with temp_json(caps) as path:
            result = run(SELECT, "--capabilities", path, "--role", "implementation", "--depth", "2")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("duplicate id", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_option_default_must_be_declared(self) -> None:
        caps = {
            "providers": [
                {
                    "providerInstanceId": "opencode",
                    "canRunChildTask": True,
                    "canRunCrossProviderChildTask": True,
                    "constraints": [],
                    "models": [
                        {
                            "id": "opencode-go/deepseek-v4.1-flash",
                            "options": [
                                {"id": "variant", "values": ["low", "high"], "default": "medium"}
                            ],
                        }
                    ],
                }
            ]
        }
        with temp_json(caps) as path:
            result = run(SELECT, "--capabilities", path, "--role", "implementation", "--depth", "2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.strip(), "opencode:opencode-go/deepseek-v4.1-flash#variant=low"
        )

    def test_option_values_must_be_strings_or_booleans(self) -> None:
        def caps_for(values: Any) -> dict[str, Any]:
            return {
                "providers": [
                    {
                        "providerInstanceId": "opencode",
                        "canRunChildTask": True,
                        "canRunCrossProviderChildTask": True,
                        "constraints": [],
                        "models": [
                            {
                                "id": "opencode-go/deepseek-v4.1-flash",
                                "options": [{"id": "variant", "values": values}],
                            }
                        ],
                    }
                ]
            }

        for values in ([1], [{"high": True}], [["high"]], [None]):
            with temp_json(caps_for(values)) as path:
                result = run(
                    SELECT, "--capabilities", path, "--role", "implementation", "--depth", "2"
                )
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("string or boolean", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_option_membership_is_type_aware(self) -> None:
        module = load_module("select_target_type_aware", SELECT)
        self.assertFalse(module.declared(True, [1]))
        self.assertFalse(module.declared(1, [True]))
        chosen = module.choose_option(
            {"id": "variant", "values": [True, "low"], "default": 1},
            maximize=False,
            depth=2,
        )
        self.assertEqual(chosen, ("variant", True))

    def test_malformed_capabilities_are_rejected(self) -> None:
        cases = [
            ({"providers": {}}, "providers must be an array"),
            (
                {
                    "providers": [
                        {
                            "providerInstanceId": "opencode",
                            "canRunChildTask": True,
                            "models": [{"id": "m", "options": {}}],
                        }
                    ]
                },
                "options must be an array",
            ),
            (
                {
                    "providers": [
                        {
                            "providerInstanceId": "opencode",
                            "canRunChildTask": "yes",
                            "models": [],
                        }
                    ]
                },
                "canRunChildTask must be a boolean",
            ),
        ]
        for caps, message in cases:
            with temp_json(caps) as path:
                result = run(
                    SELECT, "--capabilities", path, "--role", "implementation", "--depth", "2"
                )
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn(message, result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_non_finite_or_negative_estimate_is_rejected(self) -> None:
        for value in ("NaN", "Infinity", "-Infinity", "-1"):
            result = run(
                SELECT,
                "--capabilities",
                str(CAPS),
                "--role",
                "review",
                "--depth",
                "4",
                "--usage",
                str(USAGE),
                "--estimate-units",
                value,
                "--json",
            )
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("--estimate-units", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_usage_numbers_must_be_finite_and_non_negative(self) -> None:
        for remaining in (True, -1, float("nan"), float("inf")):
            usage = {"unit": "tokens", "providers": {"opencode": {"remaining": remaining}}}
            with temp_json(usage) as path:
                result = run(
                    SELECT,
                    "--capabilities",
                    str(CAPS),
                    "--role",
                    "review",
                    "--depth",
                    "4",
                    "--usage",
                    path,
                    "--json",
                )
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("finite non-negative", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

        usage = {"unit": "tokens", "providers": {"opencode": False}}
        with temp_json(usage) as path:
            result = run(
                SELECT,
                "--capabilities",
                str(CAPS),
                "--role",
                "review",
                "--depth",
                "4",
                "--usage",
                path,
                "--json",
            )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("finite non-negative", result.stderr)

    def test_budget_boundary_allows_exact_estimate(self) -> None:
        for remaining, status in ((2500, "ok"), (2499, "budget_exceeded")):
            usage = {"unit": "tokens", "providers": {"opencode": {"remaining": remaining}}}
            with temp_json(usage) as path:
                result = run(
                    SELECT,
                    "--capabilities",
                    str(CAPS),
                    "--role",
                    "implementation",
                    "--depth",
                    "1",
                    "--usage",
                    path,
                    "--json",
                )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], status)
            self.assertEqual(payload["budget"]["estimated_units"], 2500.0)

    def test_pinned_target_cannot_be_escaped_by_higher_ranked_target(self) -> None:
        common = [
            "--capabilities",
            str(CAPS),
            "--role",
            "implementation",
            "--depth",
            "3",
            "--ignore-cost",
            "--parent-provider",
            "codex",
            "--parent-model",
            "gpt-6.1-sol",
            "--json",
        ]
        unpinned = run(SELECT, *common)
        self.assertEqual(unpinned.returncode, 0, unpinned.stderr)
        self.assertEqual(
            json.loads(unpinned.stdout)["target"]["providerInstanceId"], "codex"
        )

        pinned = run(
            SELECT,
            *common,
            "--provider",
            "opencode",
            "--model",
            "opencode-go/deepseek-v4.1-flash",
        )
        self.assertEqual(pinned.returncode, 0, pinned.stderr)
        payload = json.loads(pinned.stdout)
        self.assertEqual(payload["target"]["providerInstanceId"], "opencode")
        self.assertEqual(payload["target"]["model"], "opencode-go/deepseek-v4.1-flash")

    def test_unavailable_pin_never_falls_back(self) -> None:
        cases = [
            ["--provider", "claudeAgent"],
            ["--provider", "codex", "--model", "missing-model"],
            ["--provider", "opencode", "--model", "opencode-go/deepseek-v4.1-flash"],
        ]
        for extra in cases:
            result = run(
                SELECT,
                "--capabilities",
                str(CAPS),
                "--role",
                "review",
                "--depth",
                "4",
                "--json",
                *extra,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["status"], "unavailable")

    def test_option_pin_overrides_depth_choice(self) -> None:
        result = run(
            SELECT,
            "--capabilities",
            str(CAPS),
            "--role",
            "implementation",
            "--depth",
            "2",
            "--provider",
            "opencode",
            "--model",
            "opencode-go/deepseek-v4.1-flash",
            "--option",
            "variant=max",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["target"]["options"], [{"id": "variant", "value": "max"}])
        self.assertEqual(
            payload["canonical"],
            "opencode:opencode-go/deepseek-v4.1-flash#variant=max",
        )

    def test_undeclared_option_pin_is_unavailable(self) -> None:
        for extra in (
            ["--option", "variant=ultra"],
            ["--option", "missing=high"],
            ["--option", "variant="],
        ):
            result = run(
                SELECT,
                "--capabilities",
                str(CAPS),
                "--role",
                "implementation",
                "--depth",
                "2",
                "--json",
                *extra,
            )
            if extra == ["--option", "variant="]:
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("--option", result.stderr)
                continue
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["status"], "unavailable")

    def test_huge_policy_number_is_rejected_without_traceback(self) -> None:
        base = json.loads(POLICY.read_text(encoding="utf-8"))
        base["models"]["codex:gpt-6.1-sol"]["cost_tier"] = 10**400
        with temp_json(base) as path:
            result = run(
                SELECT,
                "--capabilities",
                str(CAPS),
                "--policy",
                path,
                "--role",
                "implementation",
                "--depth",
                "3",
                "--json",
            )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("cost_tier", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_huge_json_integer_is_rejected_without_traceback(self) -> None:
        try:
            int("9" * 5000)
        except ValueError:
            pass
        else:
            self.skipTest("this Python has no integer string conversion limit")
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        ) as handle:
            handle.write('{"providers": [], "junk": ' + "9" * 5000 + "}")
            path = handle.name
        try:
            result = run(
                SELECT,
                "--capabilities",
                path,
                "--role",
                "implementation",
                "--depth",
                "3",
            )
        finally:
            Path(path).unlink(missing_ok=True)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_option_pin_validation(self) -> None:
        for extra, message in (
            (["--option", "variant"], "--option"),
            (["--option", "variant=high", "--option", "variant=max"], "duplicate"),
            (["--option", "variant=high", "--no-options"], "no-options"),
        ):
            result = run(
                SELECT,
                "--capabilities",
                str(CAPS),
                "--role",
                "implementation",
                "--depth",
                "2",
                *extra,
            )
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn(message, result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_over_budget_pin_returns_budget_exceeded(self) -> None:
        result = run(
            SELECT,
            "--capabilities",
            str(CAPS),
            "--role",
            "review",
            "--depth",
            "4",
            "--provider",
            "codex",
            "--model",
            "gpt-6.1-sol",
            "--usage",
            str(USAGE),
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "budget_exceeded")
        self.assertNotIn("target", payload)

    def test_pin_preserves_identifier_punctuation(self) -> None:
        caps = {
            "providers": [
                {
                    "providerInstanceId": "acme:edge",
                    "canRunChildTask": True,
                    "canRunCrossProviderChildTask": True,
                    "constraints": [],
                    "models": [
                        {"id": "vendor/model.v4-1_preview@edge#build", "options": []}
                    ],
                }
            ]
        }
        with temp_json(caps) as path:
            result = run(
                SELECT,
                "--capabilities",
                path,
                "--provider",
                "acme:edge",
                "--model",
                "vendor/model.v4-1_preview@edge#build",
                "--role",
                "implementation",
                "--depth",
                "2",
                "--json",
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["target"]["providerInstanceId"], "acme:edge")
        self.assertEqual(payload["target"]["model"], "vendor/model.v4-1_preview@edge#build")
        self.assertEqual(
            payload["canonical"], "acme:edge:vendor/model.v4-1_preview@edge#build"
        )

    def test_provider_only_pin_and_model_only_pin(self) -> None:
        provider_pin = run(
            SELECT,
            "--capabilities",
            str(CAPS),
            "--role",
            "implementation",
            "--depth",
            "2",
            "--provider",
            "opencode",
            "--json",
        )
        self.assertEqual(provider_pin.returncode, 0, provider_pin.stderr)
        self.assertEqual(
            json.loads(provider_pin.stdout)["target"]["providerInstanceId"], "opencode"
        )

        model_pin = run(
            SELECT,
            "--capabilities",
            str(CAPS),
            "--role",
            "implementation",
            "--depth",
            "2",
            "--model",
            "gpt-6-luna",
            "--json",
        )
        self.assertEqual(model_pin.returncode, 0, model_pin.stderr)
        payload = json.loads(model_pin.stdout)
        self.assertEqual(payload["target"]["providerInstanceId"], "codex")
        self.assertEqual(payload["target"]["model"], "gpt-6-luna")

    def test_model_level_constraint_is_skipped(self) -> None:
        caps = {
            "providers": [
                {
                    "providerInstanceId": "opencode",
                    "canRunChildTask": True,
                    "canRunCrossProviderChildTask": True,
                    "constraints": [],
                    "models": [
                        {
                            "id": "opencode/deepseek-v4.1-flash-free",
                            "constraints": ["rate limited"],
                            "options": [],
                        },
                        {"id": "opencode-go/deepseek-v4.1-flash", "options": []},
                    ],
                }
            ]
        }
        with temp_json(caps) as path:
            result = run(
                SELECT,
                "--capabilities",
                path,
                "--role",
                "implementation",
                "--depth",
                "2",
                "--json",
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["target"]["model"], "opencode-go/deepseek-v4.1-flash")

    def test_exclude_provider_id(self) -> None:
        result = run(
            SELECT,
            "--capabilities",
            str(CAPS),
            "--role",
            "implementation",
            "--depth",
            "2",
            "--exclude",
            "opencode",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertNotEqual(payload["target"]["providerInstanceId"], "opencode")

    def test_parent_flags_must_be_paired(self) -> None:
        result = run(
            SELECT,
            "--capabilities",
            str(CAPS),
            "--role",
            "review",
            "--depth",
            "4",
            "--parent-provider",
            "opencode",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("together", result.stderr)

    def test_malformed_role_rules_are_rejected(self) -> None:
        base = json.loads(POLICY.read_text(encoding="utf-8"))
        for field, value in (("allow_roles", "research"), ("allow_roles", []), ("match", "")):
            policy = json.loads(json.dumps(base))
            policy["role_restrictions"][0][field] = value
            with temp_json(policy) as path:
                result = run(
                    SELECT,
                    "--capabilities",
                    str(CAPS),
                    "--policy",
                    path,
                    "--role",
                    "review",
                    "--depth",
                    "4",
                    "--json",
                )
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("role_restrictions", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_malformed_web_research_patterns_are_rejected(self) -> None:
        base = json.loads(POLICY.read_text(encoding="utf-8"))
        for value in ("antigravity:gemini-3.8-flash", [1]):
            policy = json.loads(json.dumps(base))
            policy["web_research"]["models"] = value
            with temp_json(policy) as path:
                result = run(
                    SELECT,
                    "--capabilities",
                    str(CAPS),
                    "--policy",
                    path,
                    "--role",
                    "research",
                    "--depth",
                    "3",
                    "--web",
                    "--json",
                )
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("web_research.models", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_malformed_policy_scores_are_rejected(self) -> None:
        base = json.loads(POLICY.read_text(encoding="utf-8"))

        bad_reasoning = json.loads(json.dumps(base))
        bad_reasoning["models"]["codex:gpt-6.1-sol"]["reasoning"]["code"] = "high"
        with temp_json(bad_reasoning) as path:
            result = run(
                SELECT,
                "--capabilities",
                str(CAPS),
                "--policy",
                path,
                "--role",
                "implementation",
                "--depth",
                "3",
                "--json",
            )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("reasoning", result.stderr)

        bad_cost = json.loads(json.dumps(base))
        bad_cost["models"]["codex:gpt-6.1-sol"]["cost_tier"] = "cheap"
        with temp_json(bad_cost) as path:
            result = run(
                SELECT,
                "--capabilities",
                str(CAPS),
                "--policy",
                path,
                "--role",
                "implementation",
                "--depth",
                "3",
                "--json",
            )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("cost_tier", result.stderr)


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

    def test_depends_on_null_is_invalid_without_crash(self) -> None:
        plan = minimal_plan()
        plan["units"][0]["depends_on"] = None
        with temp_json(plan) as path:
            result = run(VALIDATE, "--plan", path, "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(
            any("depends_on must be an array" in error for error in payload["errors"]), payload
        )

    def test_integration_order_non_string_entry_is_invalid_without_crash(self) -> None:
        plan = minimal_plan()
        plan["integration"]["order"] = [["one"]]
        with temp_json(plan) as path:
            result = run(VALIDATE, "--plan", path, "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(
            any("order entries must be strings" in error for error in payload["errors"]), payload
        )

    def test_invalid_unit_id_pattern_is_rejected(self) -> None:
        plan = minimal_plan()
        plan["units"][0]["id"] = "One Unit!"
        plan["integration"]["order"] = ["One Unit!"]
        with temp_json(plan) as path:
            result = run(VALIDATE, "--plan", path, "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(any("id must match" in error for error in payload["errors"]), payload)

    def test_unknown_dependency_is_not_reported_as_cycle(self) -> None:
        plan = minimal_plan()
        plan["units"][0]["depends_on"] = ["missing"]
        with temp_json(plan) as path:
            result = run(VALIDATE, "--plan", path, "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(any("unknown dependency" in error for error in payload["errors"]), payload)
        self.assertFalse(any("cycle" in error for error in payload["errors"]), payload)

    def test_shared_write_overlap_is_rejected(self) -> None:
        plan = two_unit_plan()
        plan["units"][0].update({"role": "general", "isolation": "shared", "write_scope": ["src/**"]})
        plan["units"][1].update({"role": "test", "isolation": "shared", "write_scope": ["src/core.py"]})
        with temp_json(plan) as path:
            result = run(VALIDATE, "--plan", path, "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(
            any("share a shared write scope" in error for error in payload["errors"]), payload
        )

    def test_disjoint_shared_write_globs_are_allowed(self) -> None:
        plan = two_unit_plan()
        plan["units"][0].update({"role": "general", "isolation": "shared", "write_scope": ["src/*.py"]})
        plan["units"][1].update({"role": "test", "isolation": "shared", "write_scope": ["src/*.js"]})
        with temp_json(plan) as path:
            result = run(VALIDATE, "--plan", path, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["errors"], [])

    def test_duplicate_dependencies_are_rejected(self) -> None:
        plan = two_unit_plan()
        plan["units"][1]["depends_on"] = ["one", "one"]
        with temp_json(plan) as path:
            result = run(VALIDATE, "--plan", path, "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(
            any("duplicate dependency" in error for error in payload["errors"]), payload
        )

    def test_non_string_write_scope_and_commands_are_rejected(self) -> None:
        plan = minimal_plan()
        plan["units"][0]["write_scope"] = [1]
        plan["units"][0]["commands"] = ["ok", 2]
        plan["integration"]["tests"] = [1]
        with temp_json(plan) as path:
            result = run(VALIDATE, "--plan", path, "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(
            any("write_scope entries must be non-empty strings" in error for error in payload["errors"]),
            payload,
        )
        self.assertTrue(
            any("commands entries must be strings" in error for error in payload["errors"]), payload
        )
        self.assertTrue(
            any("integration.tests entries must be strings" in error for error in payload["errors"]),
            payload,
        )

    def test_plan_numeric_and_boolean_fields_are_validated(self) -> None:
        plan = minimal_plan()
        plan["ignore_cost"] = "yes"
        plan["fallback"] = 1
        plan["budget"] = {
            "unit": 5,
            "estimated_units": float("nan"),
            "remaining_units": True,
            "note": [],
        }
        with temp_json(plan) as path:
            result = run(VALIDATE, "--plan", path, "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        payload = json.loads(result.stdout)
        messages = "\n".join(payload["errors"])
        for expected in (
            "ignore_cost must be a boolean",
            "fallback must be a string",
            "budget.unit must be a string",
            "budget.estimated_units must be a finite non-negative number",
            "budget.remaining_units must be a finite non-negative number",
            "budget.note must be a string",
        ):
            self.assertIn(expected, messages)

    def test_target_option_values_are_validated(self) -> None:
        plan = minimal_plan()
        plan["units"][0]["target"]["options"] = [
            {"id": "reasoningEffort", "value": {"nested": True}}
        ]
        with temp_json(plan) as path:
            result = run(VALIDATE, "--plan", path, "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(
            any("value must be a scalar" in error for error in payload["errors"]), payload
        )

        plan = minimal_plan()
        plan["units"][0]["target"]["options"] = {"reasoningEffort": ["high"]}
        with temp_json(plan) as path:
            result = run(VALIDATE, "--plan", path, "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(
            any("must be a scalar" in error for error in payload["errors"]), payload
        )

    def test_malformed_enum_types_are_errors_without_traceback(self) -> None:
        cases = [
            ("mode", []),
            ("role", {}),
            ("isolation", []),
            ("unit_mode", {}),
        ]
        for field, value in cases:
            with self.subTest(field=field):
                plan = two_unit_plan()
                if field == "mode":
                    plan["mode"] = value
                elif field == "role":
                    plan["units"][0]["role"] = value
                elif field == "isolation":
                    plan["units"][0]["isolation"] = value
                else:
                    plan["units"][0]["mode"] = value
                with temp_json(plan) as path:
                    result = run(VALIDATE, "--plan", path, "--json")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                payload = json.loads(result.stdout)
                self.assertTrue(payload["errors"])

    def test_single_mode_target_is_still_validated(self) -> None:
        plan = minimal_plan()
        plan["mode"] = "single"
        plan["units"][0]["target"]["bogus"] = True
        with temp_json(plan) as path:
            result = run(VALIDATE, "--plan", path, "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(
            any("target has unknown key 'bogus'" in error for error in payload["errors"]),
            payload,
        )

    def test_simple_glob_overlaps_are_rejected(self) -> None:
        cases = [
            ("src/*", "src/*.py"),
            ("**/*.py", "src/**"),
            ("src/*_test.py", "src/*.py"),
            ("src//a.py", "src/a.py"),
            ("src/../src/a.py", "src/a.py"),
            ("src/a*", "src/ab*"),
            ("src/*.py", "src/*.[p]y"),
        ]
        for left, right in cases:
            with self.subTest(left=left, right=right):
                plan = two_unit_plan()
                plan["units"][0].update(
                    {"role": "general", "isolation": "shared", "write_scope": [left]}
                )
                plan["units"][1].update(
                    {"role": "test", "isolation": "shared", "write_scope": [right]}
                )
                with temp_json(plan) as path:
                    result = run(VALIDATE, "--plan", path, "--json")
                self.assertEqual(result.returncode, 1, result.stderr)
                payload = json.loads(result.stdout)
                self.assertTrue(
                    any(
                        "share a shared write scope" in error
                        for error in payload["errors"]
                    ),
                    payload,
                )

    def test_plan_with_bom_is_valid(self) -> None:
        plan = minimal_plan()
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8-sig"
        ) as handle:
            json.dump(plan, handle)
            path = handle.name
        try:
            result = run(VALIDATE, "--plan", path, "--json")
        finally:
            Path(path).unlink(missing_ok=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["ok"])

    def test_huge_budget_number_is_rejected_without_traceback(self) -> None:
        plan = minimal_plan()
        plan["budget"] = {"estimated_units": 10**400}
        with temp_json(plan) as path:
            result = run(VALIDATE, "--plan", path, "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(
            any(
                "budget.estimated_units must be a finite non-negative number" in error
                for error in payload["errors"]
            ),
            payload,
        )

    def test_unknown_keys_are_rejected(self) -> None:
        plan = minimal_plan()
        plan["goal_typo"] = "x"
        plan["budget"] = {"estimated_units": 1, "budget_typo": 2}
        plan["units"][0]["write_scopes"] = ["src/**"]
        plan["units"][0]["target"]["reasoning"] = "high"
        plan["units"][0]["target"]["options"] = [
            {"id": "reasoningEffort", "value": "high", "extra": True}
        ]
        plan["integration"]["test"] = ["python -m unittest"]
        with temp_json(plan) as path:
            result = run(VALIDATE, "--plan", path, "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        payload = json.loads(result.stdout)
        messages = "\n".join(payload["errors"])
        for expected in (
            "unknown top-level key 'goal_typo'",
            "budget: unknown key 'budget_typo'",
            "units[0]: unknown key 'write_scopes'",
            "target has unknown key 'reasoning'",
            "target.options[0] has unknown key 'extra'",
            "integration: unknown key 'test'",
        ):
            self.assertIn(expected, messages)


if __name__ == "__main__":
    unittest.main()
