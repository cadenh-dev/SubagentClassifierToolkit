"""End-to-end tests for benchmark plan, score, compare, and record commands."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".opencode/skills/benchmark-runner/scripts/benchmark.py"
FIXTURES = ROOT / "tests/fixtures/benchmark"
ROUTING_POLICY = ROOT / ".opencode/skills/subagent-orchestrator/data/routing-policy.json"


class BenchmarkRunnerTests(unittest.TestCase):
    def call(self, *arguments: str | Path, script: Path = SCRIPT) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(script), *map(str, arguments)],
            capture_output=True,
            text=True,
        )

    def git(self, repo: Path, *arguments: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(repo), *arguments],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def initialize_repo(self, repo: Path) -> str:
        repo.mkdir()
        self.git(repo, "init", "-q")
        self.git(repo, "config", "user.email", "benchmark@example.test")
        self.git(repo, "config", "user.name", "Benchmark Test")
        (repo / "src").mkdir()
        (repo / "src/base.py").write_text("BASE = True\n", encoding="utf-8")
        self.git(repo, "add", ".")
        self.git(repo, "commit", "-qm", "base")
        return self.git(repo, "rev-parse", "HEAD")

    def make_plan(self, directory: Path, script: Path = SCRIPT) -> dict[str, Any]:
        plan_path = directory / "benchmark-plan.json"
        result = self.call(
            "plan",
            "--task",
            FIXTURES / "task.json",
            "--targets",
            FIXTURES / "targets.json",
            "--out",
            plan_path,
            script=script,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(plan_path.read_text(encoding="utf-8"))

    def prepare_scored_benchmark(self, directory: Path) -> tuple[Path, Path, str, dict[str, Any]]:
        repo = directory / "repo"
        base = self.initialize_repo(repo)
        self.git(repo, "switch", "-c", "target-a")
        (repo / "src/target_a.py").write_text("RESULT = 'in scope'\n", encoding="utf-8")
        self.git(repo, "add", ".")
        self.git(repo, "commit", "-qm", "target a change")

        self.git(repo, "switch", "-c", "target-b", base)
        (repo / "outside.py").write_text("RESULT = 'out of scope'\n", encoding="utf-8")
        self.git(repo, "add", ".")
        self.git(repo, "commit", "-qm", "target b change")

        benchmark_plan = self.make_plan(directory)
        plan_path = directory / "benchmark-plan.json"
        units = benchmark_plan["units"]
        original_policy = ROUTING_POLICY.read_bytes()
        for unit, branch in zip(units, ("target-a", "target-b")):
            self.git(repo, "switch", branch)
            result = self.call(
                "score",
                "--plan",
                plan_path,
                "--unit",
                unit["id"],
                "--repo",
                repo,
                "--base",
                base,
                "--allow-commands",
            )
            self.assertIn(result.returncode, (0, 1), result.stderr)
        self.assertEqual(ROUTING_POLICY.read_bytes(), original_policy)
        return plan_path, repo, base, benchmark_plan

    def test_plan_validates_with_two_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            plan = self.make_plan(directory)
            self.assertEqual(len(plan["units"]), 2)
            self.assertEqual(
                [unit["target"]["providerInstanceId"] for unit in plan["units"]],
                ["target-a", "target-b"],
            )

    def test_score_writes_results_beside_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            plan_path, repo, base, plan = self.prepare_scored_benchmark(directory)
            result_paths = [
                plan_path.parent / f"{plan_path.stem}.results" / f"{unit['id']}.json"
                for unit in plan["units"]
            ]
            self.assertTrue(all(path.is_file() for path in result_paths))
            self.assertEqual(json.loads(result_paths[0].read_text())["verdict"], "pass")

    def test_compare_reports_pass_fail_scope_diff_usage_and_review_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            plan_path, repo, _, plan = self.prepare_scored_benchmark(directory)
            result = self.call("compare", "--plan", plan_path, "--repo", repo, "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            rows = json.loads(result.stdout)

            self.assertTrue(rows[0]["test_pass"])
            self.assertTrue(rows[1]["test_pass"])
            self.assertEqual(rows[0]["verdict"], "pass")
            self.assertEqual(rows[1]["verdict"], "fail")
            self.assertEqual(rows[0]["out_of_scope"], [])
            self.assertEqual(rows[1]["out_of_scope"][0]["path"], "outside.py")
            self.assertEqual(rows[0]["diff"], {"files": 1, "insertions": 1, "deletions": 0})
            self.assertEqual(rows[1]["diff"]["files"], 1)
            self.assertIsNone(rows[0]["usage"])
            self.assertEqual(rows[0]["review_status"], "unreviewed")
            self.assertEqual(rows[1]["review_status"], "unreviewed")
            self.assertEqual(len(plan["units"]), 2)

    def test_compare_accepts_explicit_review_and_usage_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            plan_path, repo, _, plan = self.prepare_scored_benchmark(directory)
            unit_id = plan["units"][0]["id"]
            result = self.call(
                "compare",
                "--plan",
                plan_path,
                "--repo",
                repo,
                "--reviewed",
                unit_id,
                "--usage",
                f"{unit_id}=500",
                "--json",
            )
            rows = json.loads(result.stdout)
            self.assertEqual(rows[0]["usage"], "500")
            self.assertEqual(rows[0]["review_status"], "reviewed")

    def test_score_compare_and_record_share_results_override(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            original_policy = ROUTING_POLICY.read_bytes()
            repo = directory / "repo"
            base = self.initialize_repo(repo)
            (repo / "src/change.py").write_text("VALUE = 1\n", encoding="utf-8")
            self.git(repo, "add", ".")
            self.git(repo, "commit", "-qm", "change")
            plan = self.make_plan(directory)
            plan_path = directory / "benchmark-plan.json"
            custom_results = directory / "custom-results"
            unit_id = plan["units"][0]["id"]
            scored = self.call(
                "score",
                "--plan",
                plan_path,
                "--unit",
                unit_id,
                "--repo",
                repo,
                "--base",
                base,
                "--allow-commands",
                "--results",
                custom_results,
            )
            self.assertEqual(scored.returncode, 0, scored.stderr)
            self.assertTrue((custom_results / f"{unit_id}.json").is_file())
            compared = self.call("compare", "--plan", plan_path, "--results", custom_results, "--json")
            self.assertTrue(json.loads(compared.stdout)[0]["test_pass"])
            ledger = directory / "outcomes.jsonl"
            recorded = self.call(
                "record",
                "--plan",
                plan_path,
                "--unit",
                unit_id,
                "--reviewed",
                unit_id,
                "--results",
                custom_results,
                "--depth",
                "3",
                "--outcome",
                "partial",
                "--verification",
                "parent",
                "--retries",
                "0",
                "--review-findings",
                "0",
                "--ledger",
                ledger,
            )
            self.assertEqual(recorded.returncode, 0, recorded.stderr)
            self.assertTrue(ledger.is_file())
            self.assertEqual(ROUTING_POLICY.read_bytes(), original_policy)

    def test_record_refuses_unreviewed_unit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            plan_path = directory / "plan.json"
            plan_path.write_text("{}", encoding="utf-8")
            result = self.call(
                "record",
                "--plan",
                plan_path,
                "--unit",
                "unit-a",
                "--depth",
                "3",
                "--outcome",
                "partial",
                "--verification",
                "parent",
                "--retries",
                "0",
                "--review-findings",
                "0",
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("unreviewed", result.stderr)

    def test_record_with_review_appends_to_supplied_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            original_policy = ROUTING_POLICY.read_bytes()
            plan_path, _, _, plan = self.prepare_scored_benchmark(directory)
            unit_id = plan["units"][0]["id"]
            ledger = directory / "ledger.jsonl"
            result = self.call(
                "record",
                "--plan",
                plan_path,
                "--unit",
                unit_id,
                "--reviewed",
                unit_id,
                "--depth",
                "3",
                "--outcome",
                "partial",
                "--verification",
                "parent",
                "--retries",
                "0",
                "--review-findings",
                "0",
                "--ledger",
                ledger,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            entries = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["unit"], unit_id)
            self.assertEqual(ROUTING_POLICY.read_bytes(), original_policy)

    def test_missing_orchestrator_reports_clear_exit_two_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            task = json.loads((FIXTURES / "task.json").read_text(encoding="utf-8"))
            task_path = directory / "task.json"
            task_path.write_text(json.dumps(task), encoding="utf-8")
            output = directory / "plan.json"
            result = self.call(
                "plan",
                "--task",
                task_path,
                "--targets",
                FIXTURES / "targets.json",
                "--out",
                output,
                "--orchestrator-dir",
                directory / "missing",
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("orchestrator scripts not found", result.stderr)
            self.assertIn("--orchestrator-dir", result.stderr)

    def test_installed_flat_skills_layout_finds_sibling_orchestrator(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            skills = directory / "skills"
            shutil.copytree(ROOT / ".opencode/skills/benchmark-runner", skills / "benchmark-runner")
            shutil.copytree(ROOT / ".opencode/skills/subagent-orchestrator", skills / "subagent-orchestrator")
            plan = self.make_plan(directory, skills / "benchmark-runner/scripts/benchmark.py")
            self.assertEqual(len(plan["units"]), 2)


if __name__ == "__main__":
    unittest.main()
