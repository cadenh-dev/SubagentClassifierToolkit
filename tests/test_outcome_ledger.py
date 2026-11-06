"""Tests for the outcome ledger.

Run:
    python -m unittest tests.test_outcome_ledger -v
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".opencode" / "skills" / "subagent-orchestrator" / "scripts" / "outcome_ledger.py"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=ROOT,
        timeout=60,
    )


def record_args(*extra: str) -> list[str]:
    return [
        "record",
        "--unit",
        "auth-core",
        "--role",
        "implementation",
        "--depth",
        "3",
        "--provider",
        "codex",
        "--model",
        "gpt-6.1-sol",
        "--outcome",
        "success",
        *extra,
    ]


class OutcomeLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ledger = Path(self.temp.name) / "outcomes.jsonl"

    def invoke(self, *args: str) -> subprocess.CompletedProcess[str]:
        return run("--ledger", str(self.ledger), *args)

    def records(self) -> list[dict]:
        return [
            json.loads(line)
            for line in self.ledger.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_record_writes_one_valid_line(self) -> None:
        result = self.invoke(
            *record_args(
                "--option",
                "reasoningEffort=high",
                "--option",
                "fastMode=false",
                "--isolation",
                "worktree",
                "--mode",
                "async",
                "--verification",
                "parent",
                "--estimated-units",
                "50000",
                "--actual-units",
                "41200",
                "--duration",
                "615",
                "--retries",
                "1",
                "--review-findings",
                "2",
                "--pr",
                "https://example.test/pr/1",
                "--notes",
                "verified locally",
                "--recorded-at",
                "2026-11-06T12:00:00Z",
                "--json",
            )
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(result.stdout)
        self.assertEqual(record["version"], 1)
        self.assertEqual(record["unit"], "auth-core")
        self.assertEqual(record["role"], "implementation")
        self.assertEqual(record["depth"], 3)
        self.assertEqual(
            record["target"],
            {
                "providerInstanceId": "codex",
                "model": "gpt-6.1-sol",
                "options": {"reasoningEffort": "high", "fastMode": False},
            },
        )
        self.assertEqual(record["outcome"], "success")
        self.assertEqual(record["verification"], "parent")
        self.assertEqual(record["estimated_units"], 50000.0)
        self.assertEqual(record["actual_units"], 41200.0)
        self.assertEqual(record["duration_seconds"], 615.0)
        self.assertEqual(record["retries"], 1)
        self.assertEqual(record["review_findings"], 2)
        self.assertEqual(record["pr"], "https://example.test/pr/1")
        self.assertEqual(record["notes"], "verified locally")
        self.assertEqual(record["recorded_at"], "2026-11-06T12:00:00Z")
        self.assertEqual(self.records(), [record])

    def test_defaults_are_explicit(self) -> None:
        result = self.invoke(*record_args())
        self.assertEqual(result.returncode, 0, result.stderr)
        record = self.records()[0]
        self.assertEqual(record["verification"], "none")
        self.assertIsNone(record["estimated_units"])
        self.assertIsNone(record["actual_units"])
        self.assertIsNone(record["duration_seconds"])
        self.assertEqual(record["retries"], 0)
        self.assertEqual(record["review_findings"], 0)
        self.assertIsNone(record["pr"])
        self.assertIsNone(record["notes"])
        self.assertNotIn("isolation", record)
        self.assertNotIn("mode", record)
        self.assertTrue(record["recorded_at"].endswith("Z"))

    def test_ledger_flag_can_follow_subcommand(self) -> None:
        result = run(*record_args("--ledger", str(self.ledger)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.ledger.exists())
        summary = run("summarize", "--ledger", str(self.ledger), "--json")
        self.assertEqual(summary.returncode, 0, summary.stderr)
        self.assertEqual(json.loads(summary.stdout)["summary"]["records"], 1)

    def test_appends_multiple_records(self) -> None:
        for unit in ("one", "two", "three"):
            result = self.invoke(*record_args("--unit", unit))
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([item["unit"] for item in self.records()], ["one", "two", "three"])

    def test_invalid_input_does_not_write(self) -> None:
        cases = [
            (["--role", "nope"], "--role"),
            (["--depth", "0"], "--depth"),
            (["--depth", "6"], "--depth"),
            (["--outcome", "maybe"], "--outcome"),
            (["--verification", "trust-me"], "--verification"),
            (["--estimated-units", "-1"], "--estimated-units"),
            (["--estimated-units", "NaN"], "--estimated-units"),
            (["--actual-units", "Infinity"], "--actual-units"),
            (["--duration", "-3"], "--duration"),
            (["--retries", "-1"], "--retries"),
            (["--review-findings", "-1"], "--review-findings"),
            (["--recorded-at", "yesterday"], "--recorded-at"),
            (["--option", "variant"], "--option"),
            (["--option", "variant="], "--option"),
            (["--option", "variant=high", "--option", "variant=max"], "duplicate"),
            (["--isolation", "container"], "--isolation"),
            (["--mode", "later"], "--mode"),
        ]
        for extra, expected in cases:
            with self.subTest(extra=extra):
                result = self.invoke(*record_args(*extra))
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(expected, result.stderr)
                self.assertNotIn("Traceback", result.stderr)
        self.assertFalse(self.ledger.exists())

    def test_empty_strings_are_rejected(self) -> None:
        for extra, expected in (
            (["--unit", " "], "--unit"),
            (["--provider", ""], "--provider"),
            (["--model", "  "], "--model"),
        ):
            result = self.invoke(*record_args(*extra))
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn(expected, result.stderr)
        self.assertFalse(self.ledger.exists())

    def test_summary_aggregates_and_keeps_unknowns_separate(self) -> None:
        self.invoke(
            *record_args(
                "--verification",
                "parent",
                "--estimated-units",
                "100",
                "--actual-units",
                "80",
                "--duration",
                "10",
                "--review-findings",
                "1",
                "--recorded-at",
                "2026-11-01T00:00:00Z",
            )
        )
        self.invoke(
            *record_args(
                "--outcome",
                "failure",
                "--verification",
                "child-report",
                "--estimated-units",
                "100",
                "--duration",
                "30",
                "--retries",
                "1",
                "--review-findings",
                "2",
                "--recorded-at",
                "2026-11-02T00:00:00Z",
            )
        )
        self.invoke(
            "record",
            "--unit",
            "fast-one",
            "--role",
            "implementation",
            "--depth",
            "3",
            "--provider",
            "opencode",
            "--model",
            "deepseek",
            "--outcome",
            "success",
            "--verification",
            "parent",
            "--estimated-units",
            "50",
            "--actual-units",
            "50",
            "--duration",
            "20",
            "--recorded-at",
            "2026-11-03T00:00:00Z",
        )

        result = self.invoke("summarize", "--by", "model", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["summary"], {"records": 3, "groups": 2})
        codex, opencode = payload["groups"]
        self.assertEqual(codex["key"], "codex:gpt-6.1-sol")
        self.assertEqual(codex["runs"], 2)
        self.assertEqual(codex["outcomes"]["success"], 1)
        self.assertEqual(codex["outcomes"]["failure"], 1)
        self.assertEqual(codex["success_rate"], 0.5)
        self.assertEqual(codex["verified_runs"], 1)
        self.assertEqual(codex["verified_success_rate"], 1.0)
        self.assertEqual(codex["retries"], 1)
        self.assertEqual(codex["retries_per_run"], 0.5)
        self.assertEqual(codex["review_findings"], 3)
        self.assertEqual(codex["duration_seconds_total"], 40.0)
        self.assertEqual(codex["duration_seconds_avg"], 20.0)
        self.assertEqual(codex["usage_pairs"], 1)
        self.assertEqual(codex["usage_ratio"], 0.8)
        self.assertEqual(opencode["key"], "opencode:deepseek")
        self.assertEqual(opencode["usage_ratio"], 1.0)

    def test_summary_by_role_and_filters(self) -> None:
        self.invoke(
            *record_args(
                "--unit",
                "one",
                "--role",
                "review",
                "--verification",
                "parent",
                "--recorded-at",
                "2026-11-01T00:00:00Z",
            )
        )
        self.invoke(
            *record_args(
                "--unit",
                "two",
                "--role",
                "implementation",
                "--verification",
                "child-report",
                "--recorded-at",
                "2026-11-05T00:00:00Z",
            )
        )

        by_role = self.invoke("summarize", "--by", "role", "--json")
        self.assertEqual(by_role.returncode, 0, by_role.stderr)
        roles = {group["key"]: group for group in json.loads(by_role.stdout)["groups"]}
        self.assertEqual(set(roles), {"implementation", "review"})
        self.assertEqual(roles["review"]["verified_runs"], 1)
        self.assertEqual(roles["implementation"]["verified_runs"], 0)

        verified = self.invoke("summarize", "--by", "model", "--verified-only", "--json")
        self.assertEqual(verified.returncode, 0, verified.stderr)
        payload = json.loads(verified.stdout)
        self.assertEqual(payload["summary"]["records"], 1)
        self.assertEqual(payload["groups"][0]["key"], "codex:gpt-6.1-sol")

        since = self.invoke(
            "summarize", "--by", "model", "--since", "2026-11-03T00:00:00Z", "--json"
        )
        self.assertEqual(since.returncode, 0, since.stderr)
        payload = json.loads(since.stdout)
        self.assertEqual(payload["summary"]["records"], 1)
        self.assertEqual(payload["groups"][0]["runs"], 1)

    def test_missing_ledger_summarizes_empty(self) -> None:
        result = self.invoke("summarize")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("0 record(s)", result.stdout)
        self.assertIn("No records matched", result.stdout)
        self.assertFalse(self.ledger.exists())

        strict = self.invoke("summarize", "--strict-empty")
        self.assertEqual(strict.returncode, 1, strict.stderr)
        self.assertIn("no records matched", strict.stderr)

    def test_malformed_json_line_is_reported(self) -> None:
        valid = {
            "version": 1,
            "unit": "one",
            "role": "implementation",
            "depth": 3,
            "target": {
                "providerInstanceId": "codex",
                "model": "gpt-6.1-sol",
                "options": {},
            },
            "outcome": "success",
            "verification": "parent",
            "recorded_at": "2026-11-01T00:00:00Z",
        }
        self.ledger.write_text(json.dumps(valid) + "\nnot json\n", encoding="utf-8")
        result = self.invoke("summarize")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn(f"{self.ledger}:2", result.stderr)
        self.assertIn("invalid JSON", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_invalid_record_on_disk_is_reported(self) -> None:
        self.ledger.write_text(
            json.dumps(
                {
                    "version": 1,
                    "unit": "one",
                    "role": "implementation",
                    "depth": 3,
                    "target": {"providerInstanceId": "codex"},
                    "outcome": "success",
                    "verification": "parent",
                    "recorded_at": "2026-11-01T00:00:00Z",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        result = self.invoke("summarize")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("target.model", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_non_negative_number_validation_after_json_load(self) -> None:
        record = {
            "version": 1,
            "unit": "one",
            "role": "implementation",
            "depth": 3,
            "target": {
                "providerInstanceId": "codex",
                "model": "gpt-6.1-sol",
                "options": {},
            },
            "outcome": "success",
            "verification": "parent",
            "estimated_units": 1,
            "actual_units": float("nan"),
            "duration_seconds": None,
            "retries": 0,
            "review_findings": 0,
            "recorded_at": "2026-11-01T00:00:00Z",
        }
        self.ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")
        result = self.invoke("summarize")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("actual_units", result.stderr)

    def test_text_summary_is_readable(self) -> None:
        self.invoke(
            *record_args(
                "--verification",
                "parent",
                "--estimated-units",
                "100",
                "--actual-units",
                "80",
                "--duration",
                "12.5",
            )
        )
        result = self.invoke("summarize")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("codex:gpt-6.1-sol", result.stdout)
        self.assertIn("success=100%", result.stdout)
        self.assertIn("verified", result.stdout)
        self.assertIn("actual/estimate=0.80x", result.stdout)

    def test_target_group_includes_depth_and_options(self) -> None:
        self.invoke(*record_args("--option", "variant=high"))
        self.invoke(*record_args("--option", "variant=max"))
        self.invoke(*record_args("--depth", "2", "--option", "variant=high"))

        detailed = self.invoke("summarize", "--json")
        self.assertEqual(detailed.returncode, 0, detailed.stderr)
        payload = json.loads(detailed.stdout)
        self.assertEqual(payload["summary"]["groups"], 3)
        keys = {group["key"] for group in payload["groups"]}
        self.assertIn("codex:gpt-6.1-sol#variant=high@depth=3", keys)
        self.assertIn("codex:gpt-6.1-sol#variant=max@depth=3", keys)
        self.assertIn("codex:gpt-6.1-sol#variant=high@depth=2", keys)

        rollup = self.invoke("summarize", "--by", "model", "--json")
        self.assertEqual(rollup.returncode, 0, rollup.stderr)
        payload = json.loads(rollup.stdout)
        self.assertEqual(payload["summary"]["groups"], 1)
        self.assertEqual(payload["groups"][0]["runs"], 3)

        filtered = self.invoke(
            "summarize", "--role", "implementation", "--depth", "2", "--json"
        )
        self.assertEqual(filtered.returncode, 0, filtered.stderr)
        payload = json.loads(filtered.stdout)
        self.assertEqual(payload["summary"]["records"], 1)
        self.assertEqual(payload["groups"][0]["runs"], 1)

    def test_zero_estimate_usage_is_not_hidden(self) -> None:
        self.invoke(
            *record_args(
                "--estimated-units",
                "0",
                "--actual-units",
                "10",
            )
        )
        result = self.invoke("summarize", "--by", "model", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        group = json.loads(result.stdout)["groups"][0]
        self.assertEqual(group["usage_pairs"], 1)
        self.assertIsNone(group["usage_ratio"])
        self.assertEqual(group["paired_estimated_units_total"], 0.0)
        self.assertEqual(group["paired_actual_units_total"], 10.0)
        text = self.invoke("summarize")
        self.assertEqual(text.returncode, 0, text.stderr)
        self.assertIn("zero estimate", text.stdout)

    def test_fractional_timestamp_ordering(self) -> None:
        self.invoke(*record_args("--recorded-at", "2026-11-01T00:00:00Z"))
        self.invoke(*record_args("--recorded-at", "2026-11-01T00:00:00.500000Z"))
        result = self.invoke("summarize", "--by", "model", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        group = json.loads(result.stdout)["groups"][0]
        self.assertEqual(group["last_recorded_at"], "2026-11-01T00:00:00.500000Z")

    def test_bom_ledger_is_read(self) -> None:
        valid = {
            "version": 1,
            "unit": "one",
            "role": "implementation",
            "depth": 3,
            "target": {
                "providerInstanceId": "codex",
                "model": "gpt-6.1-sol",
                "options": {},
            },
            "outcome": "success",
            "verification": "parent",
            "recorded_at": "2026-11-01T00:00:00Z",
        }
        self.ledger.write_text("\ufeff" + json.dumps(valid) + "\n", encoding="utf-8")
        result = self.invoke("summarize", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["summary"]["records"], 1)

    def test_extreme_and_mistyped_disk_values_are_rejected(self) -> None:
        base = {
            "version": 1,
            "unit": "one",
            "role": "implementation",
            "depth": 3,
            "target": {
                "providerInstanceId": "codex",
                "model": "gpt-6.1-sol",
                "options": {},
            },
            "outcome": "success",
            "verification": "parent",
            "recorded_at": "2026-11-01T00:00:00Z",
        }
        cases = [
            ({"retries": float("inf")}, "retries"),
            ({"retries": "1"}, "retries"),
            ({"retries": 10**400}, "retries"),
            ({"estimated_units": 10**400}, "estimated_units"),
            ({"estimated_units": "100"}, "estimated_units"),
            ({"depth": True}, "depth"),
            ({"depth": 3.0}, "depth"),
            ({"version": 2}, "version"),
            ({"role": []}, "role"),
            ({"outcome": {}}, "outcome"),
            ({"verification": []}, "verification"),
            ({"isolation": {}}, "isolation"),
            ({"mode": []}, "mode"),
            ({"recorded_at": "0001-01-01T00:00:00+14:00"}, "recorded_at"),
            ({"recorded_at": 20261106}, "recorded_at"),
        ]
        for extra, expected in cases:
            record = dict(base)
            record.update(extra)
            self.ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")
            result = self.invoke("summarize")
            self.assertEqual(result.returncode, 2, (extra, result.stderr))
            self.assertIn(expected, result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_option_value_whitespace_is_trimmed(self) -> None:
        result = self.invoke(*record_args("--option", "variant= high "))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.records()[0]["target"]["options"], {"variant": "high"})


if __name__ == "__main__":
    unittest.main()
