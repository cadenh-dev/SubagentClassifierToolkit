"""Static checks for the skill-text evals.

These do not call a model. They check that the scenarios are well formed, that
the grader classifies each scenario's own pass and fail examples correctly, and
that every SKILL.md rule a scenario depends on is still in the skill.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "evals" / "run_skill_evals.py"
SCENARIOS = ROOT / "evals" / "subagent-orchestrator" / "scenarios.json"
TRIGGERS = ROOT / "evals" / "subagent-orchestrator" / "triggers.json"
SKILLS = ROOT / ".opencode" / "skills"
SKILL_MD = SKILLS / "subagent-orchestrator" / "SKILL.md"

spec = importlib.util.spec_from_file_location("run_skill_evals", RUNNER)
evals = importlib.util.module_from_spec(spec)
sys.modules["run_skill_evals"] = evals
spec.loader.exec_module(evals)


class ScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = evals.load_scenarios(SCENARIOS)
        cls.skill = SKILL_MD.read_text(encoding="utf-8")

    def test_ids_unique_and_fields_present(self) -> None:
        ids = [s["id"] for s in self.data["scenarios"]]
        self.assertEqual(len(ids), len(set(ids)))
        for s in self.data["scenarios"]:
            with self.subTest(s["id"]):
                for key in ("title", "pressure", "rule_anchors", "context", "expect", "examples"):
                    self.assertIn(key, s)
                self.assertTrue(set(s["expect"].get("decision", [])) <= set(evals.DECISIONS))

    def test_rule_anchors_exist_in_skill(self) -> None:
        for s in self.data["scenarios"]:
            for anchor in s["rule_anchors"]:
                with self.subTest(scenario=s["id"], anchor=anchor):
                    self.assertIn(anchor, self.skill)

    def test_examples_grade_as_labelled(self) -> None:
        for s in self.data["scenarios"]:
            with self.subTest(s["id"]):
                self.assertEqual(evals.grade(s, s["examples"]["pass"]), [])
                self.assertNotEqual(evals.grade(s, s["examples"]["fail"]), [])

    def test_every_decision_is_defined_in_the_contract(self) -> None:
        for decision in evals.DECISIONS:
            self.assertIn(f"- {decision}: ", evals.OUTPUT_CONTRACT)

    def test_prompt_contains_state_and_skill_only_when_asked(self) -> None:
        s = self.data["scenarios"][0]
        with_skill = evals.build_prompt(self.data, s, "SKILL BODY")
        baseline = evals.build_prompt(self.data, s, None)
        self.assertIn("SKILL BODY", with_skill)
        self.assertNotIn("<skill", baseline)
        self.assertNotIn("{caps_default}", with_skill)
        self.assertIn(s["context"]["user"].strip(), baseline)


class GraderTests(unittest.TestCase):
    scenario = {
        "id": "x",
        "expect": {
            "decision": ["delegate"],
            "target": "re:^codex:",
            "require_calls": [{"tool": "delegate_task", "match": {"role": "review"}}],
            "forbid_calls": [{"tool": "t3_thread_send"}],
            "max_calls": {"delegate_task": 1},
            "must_mention": ["recheck"],
            "must_not_mention": ["unavailable"],
        },
    }

    def good(self) -> dict:
        return {
            "decision": "delegate",
            "target": "codex:gpt-6.1-sol",
            "tool_calls": [{"tool": "delegate_task", "args": {"role": "Review"}}],
            "user_message": "Launched a recheck.",
        }

    def test_good_passes(self) -> None:
        self.assertEqual(evals.grade(self.scenario, self.good()), [])

    def test_each_check_can_fail(self) -> None:
        cases = {
            "decision": {"decision": "end_turn"},
            "target": {"target": "cursor:x"},
            "missing required": {"tool_calls": []},
            "forbidden": {"tool_calls": [{"tool": "delegate_task", "args": {"role": "review"}}, {"tool": "t3_thread_send", "args": {}}]},
            "max": {"tool_calls": [{"tool": "delegate_task", "args": {"role": "review"}}] * 2},
            "does not mention": {"user_message": "Launched."},
            "forbidden /unavailable/": {"user_message": "recheck unavailable"},
        }
        for label, change in cases.items():
            with self.subTest(label):
                response = {**self.good(), **change}
                failures = evals.grade(self.scenario, response)
                self.assertTrue(any(label.split()[0] in f for f in failures), failures)

    def test_parse_response_handles_fences_and_noise(self) -> None:
        parsed, error = evals.parse_response('Sure:\n```json\n{"decision": "end_turn"}\n```')
        self.assertIsNone(error)
        self.assertEqual(parsed["decision"], "end_turn")
        parsed, error = evals.parse_response("no json here")
        self.assertIsNone(parsed)
        self.assertIsNotNone(error)

    def test_grade_raw_reports_bad_json(self) -> None:
        result = evals.grade_raw(self.scenario, "{not json")
        self.assertFalse(result["passed"])


class TriggerTests(unittest.TestCase):
    def test_cases_valid(self) -> None:
        data = evals.load_triggers(TRIGGERS)
        ids = [c["id"] for c in data["cases"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(any(c["load"] for c in data["cases"]))
        self.assertTrue(any(not c["load"] for c in data["cases"]))
        for c in data["cases"]:
            self.assertIsInstance(c["load"], bool)

    def test_catalog_reads_every_skill(self) -> None:
        names = [name for name, _ in evals.skill_catalog(SKILLS)]
        self.assertIn("subagent-orchestrator", names)
        self.assertIn("skill-linter", names)

    def test_grade_trigger(self) -> None:
        case = {"id": "c", "load": True}
        self.assertTrue(evals.grade_trigger(case, "subagent-orchestrator", '{"load_skills": ["subagent-orchestrator"]}')["passed"])
        self.assertFalse(evals.grade_trigger(case, "subagent-orchestrator", '{"load_skills": []}')["passed"])
        self.assertFalse(evals.grade_trigger(case, "subagent-orchestrator", '{"skills": []}')["passed"])


class DescriptionTests(unittest.TestCase):
    """Descriptions say when to use a skill, not what it does step by step."""

    def test_descriptions_start_with_a_trigger(self) -> None:
        for name, description in evals.skill_catalog(SKILLS):
            with self.subTest(name):
                self.assertTrue(description.startswith("Use when"), description[:60])

    def test_orchestrator_description_does_not_summarize_the_workflow(self) -> None:
        description = dict(evals.skill_catalog(SKILLS))["subagent-orchestrator"]
        for workflow_word in ("writes self-contained briefs", "launches delegates", "records outcomes", "Chooses the cheapest"):
            self.assertNotIn(workflow_word, description)
        self.assertTrue(re.search(r"\bparallel\b", description))


if __name__ == "__main__":
    unittest.main()
