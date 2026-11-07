import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).parents[1]
SKILL = ROOT / ".opencode/skills/subagent-orchestrator"
SCRIPT = SKILL / "scripts/decision_journal.py"
SELECT = SKILL / "scripts/select_target.py"
CAPABILITIES = ROOT / "tests/fixtures/capabilities.json"
USAGE = ROOT / "tests/fixtures/usage.json"


class DecisionJournalTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *map(str, args)],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )

    def record(self, journal, unit, decision_id=None):
        args = [
            "--journal", str(journal), "record", "--unit", unit,
            "--target", '{"providerInstanceId":"codex","model":"gpt-6.1-sol"}',
            "--json",
        ]
        if decision_id:
            args.extend(["--decision-id", decision_id])
        result = self.run_cli(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)[0]

    def test_append_and_real_selector_alternatives(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = Path(temp) / "decisions.jsonl"
            selected = subprocess.run(
                [
                    sys.executable, str(SELECT), "--capabilities", str(CAPABILITIES),
                    "--usage", str(USAGE), "--role", "implementation", "--depth", "3", "--json",
                ],
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            self.assertEqual(selected.returncode, 0, selected.stderr)
            selection = json.loads(selected.stdout)
            self.assertTrue(selection["rejected_candidates"])

            # Persist the real selector output, then pass that file to the journal.
            selector_file = Path(temp) / "selector.json"
            selector_file.write_text(selected.stdout, encoding="utf-8")
            proc = self.run_cli(
                "--journal", journal, "record", "--from-select", selector_file,
                "--unit", "unit-a", "--json",
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            row = json.loads(proc.stdout)[0]
            self.assertEqual(row["selected"], selection["target"])
            self.assertTrue(any(alt["rejected_because"].strip() for alt in row["alternatives"]))
            self.assertEqual(json.loads(journal.read_text())["selected"], selection["target"])

    def test_selector_reports_budget_rejections_with_reason(self):
        selected = subprocess.run(
            [
                sys.executable, str(SELECT), "--capabilities", str(CAPABILITIES),
                "--usage", str(USAGE), "--role", "implementation", "--depth", "3",
                "--estimate-units", "20000", "--json",
            ],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(selected.returncode, 0, selected.stderr)
        selection = json.loads(selected.stdout)
        reasons = [alt["rejected_because"] for alt in selection.get("rejected_candidates", [])]
        self.assertEqual(selection["status"], "ok")
        codex = [alt for alt in selection["rejected_candidates"] if alt["target"]["providerInstanceId"] == "codex"]
        self.assertTrue(codex)
        self.assertTrue(all("below estimate" in alt["rejected_because"] for alt in codex))
        self.assertTrue(any(reason.startswith("ranked below") and "score" in reason for reason in reasons), reasons)

    def test_budget_json_is_stored_as_object(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = Path(temp) / "decisions.jsonl"
            proc = self.run_cli(
                "--journal", journal, "record", "--unit", "u",
                "--target", '{"providerInstanceId":"codex","model":"gpt-6.1-sol"}',
                "--budget", '{"remaining":42}', "--json",
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(proc.stdout)[0]["budget"], {"remaining": 42})

    def test_invalid_target_json_exits_two_without_write(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = Path(temp) / "decisions.jsonl"
            proc = self.run_cli("--journal", journal, "record", "--unit", "u", "--target", "{bad")
            self.assertEqual(proc.returncode, 2)
            self.assertNotIn("Traceback", proc.stderr)
            self.assertFalse(journal.exists())

    def test_invalid_budget_json_exits_two_without_write(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = Path(temp) / "decisions.jsonl"
            proc = self.run_cli(
                "--journal", journal, "record", "--unit", "u",
                "--target", '{"providerInstanceId":"codex","model":"gpt-6.1-sol"}',
                "--budget", "{bad",
            )
            self.assertEqual(proc.returncode, 2)
            self.assertNotIn("Traceback", proc.stderr)
            self.assertFalse(journal.exists())

    def test_refuses_secret_in_target_without_echoing_value(self):
        secret = "sk-abcdefghijklmnop"
        with tempfile.TemporaryDirectory() as temp:
            journal = Path(temp) / "decisions.jsonl"
            proc = self.run_cli(
                "--journal", journal, "record", "--unit", "u",
                "--target", json.dumps({"providerInstanceId": "codex", "model": secret}),
            )
            self.assertEqual(proc.returncode, 2)
            self.assertNotIn(secret, proc.stderr)
            self.assertFalse(journal.exists())

    def test_refuses_secret_in_reason_without_echoing_value(self):
        secret = "ghp_abcdefghijklmnopqrstuvwxyz123456"
        with tempfile.TemporaryDirectory() as temp:
            journal = Path(temp) / "decisions.jsonl"
            proc = self.run_cli(
                "--journal", journal, "record", "--unit", "u",
                "--target", '{"providerInstanceId":"codex","model":"gpt-6.1-sol"}',
                "--reason", f"selected after reading {secret}",
            )
            self.assertEqual(proc.returncode, 2)
            self.assertNotIn(secret, proc.stderr)
            self.assertFalse(journal.exists())

    def test_refuses_secret_in_alternative_without_echoing_value(self):
        secret = "ghp_abcdefghijklmnopqrstuvwxyz123456"
        with tempfile.TemporaryDirectory() as temp:
            journal = Path(temp) / "decisions.jsonl"
            source = Path(temp) / "source.json"
            source.write_text(json.dumps({
                "target": {"providerInstanceId": "codex", "model": "gpt-6.1-sol"},
                "alternatives": [{
                    "target": {"providerInstanceId": "opencode", "model": "other"},
                    "rejected_because": f"contains {secret}",
                }],
            }), encoding="utf-8")
            proc = self.run_cli("--journal", journal, "record", "--json-file", source, "--unit", "u")
            self.assertEqual(proc.returncode, 2)
            self.assertNotIn(secret, proc.stderr)
            self.assertFalse(journal.exists())

    def test_list_filters_provider_and_since(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = Path(temp) / "decisions.jsonl"
            rows = [
                {"unit_id": "old", "timestamp": "2026-01-01T00:00:00Z", "selected": {"providerInstanceId": "codex"}},
                {"unit_id": "new-codex", "timestamp": "2026-11-02T00:00:00Z", "selected": {"providerInstanceId": "codex"}},
                {"unit_id": "new-other", "timestamp": "2026-11-03T00:00:00Z", "selected": {"providerInstanceId": "opencode"}},
            ]
            journal.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            proc = self.run_cli("--journal", journal, "list", "--provider", "codex", "--since", "2026-11-01", "--json")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual([row["unit_id"] for row in json.loads(proc.stdout)], ["new-codex"])

    def test_show_missing_unit_exits_two(self):
        with tempfile.TemporaryDirectory() as temp:
            proc = self.run_cli("--journal", Path(temp) / "missing.jsonl", "show", "--unit", "missing")
            self.assertEqual(proc.returncode, 2)
            self.assertIn("no decision found", proc.stderr)

    def test_malformed_journal_reports_line_number(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = Path(temp) / "decisions.jsonl"
            journal.write_text('{"unit_id":"u"}\nnot-json\n', encoding="utf-8")
            proc = self.run_cli("--journal", journal, "list")
            self.assertEqual(proc.returncode, 2)
            self.assertIn(f"{journal}:2: invalid JSON", proc.stderr)

    def test_join_matches_unit_and_optional_decision_id(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = Path(temp) / "decisions.jsonl"
            self.record(journal, "unit-a", "decision-a")
            self.record(journal, "unit-b")
            ledger = Path(temp) / "outcomes.jsonl"
            outcomes = [
                {"unit": "unit-a", "decision_id": "decision-a", "outcome": "success"},
                {"unit": "unit-a", "decision_id": "other", "outcome": "failure"},
                {"unit": "unit-b", "outcome": "partial"},
                {"unit": "other", "outcome": "success"},
            ]
            ledger.write_text("".join(json.dumps(row) + "\n" for row in outcomes), encoding="utf-8")
            proc = self.run_cli("--journal", journal, "join", "--ledger", ledger, "--json")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            joined = json.loads(proc.stdout)
            self.assertEqual([row["outcome"] for row in joined[0]["outcomes"]], ["success"])
            self.assertEqual([row["outcome"] for row in joined[1]["outcomes"]], ["partial"])


if __name__ == "__main__":
    unittest.main()
