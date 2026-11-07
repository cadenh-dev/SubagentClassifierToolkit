import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).parents[1] / ".opencode/skills/subagent-orchestrator/scripts/decision_journal.py"
FIXTURE = Path(__file__).parent / "fixtures/decisions/select-output.json"


class DecisionJournalTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True)

    def test_append_and_select_alternatives(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = Path(temp) / "decisions.jsonl"
            args = ("--journal", journal, "record", "--from-select", FIXTURE, "--unit", "unit-a", "--json")
            first = self.run_cli(*args); self.assertEqual(first.returncode, 0, first.stderr)
            before = journal.read_text()
            second = self.run_cli(*args); self.assertEqual(second.returncode, 0, second.stderr)
            self.assertTrue(journal.read_text().startswith(before))
            row = json.loads(before)
            self.assertEqual(row["selected"]["providerInstanceId"], "codex")
            self.assertTrue(row["alternatives"])
            self.assertTrue(row["alternatives"][0]["rejected_because"])

    def test_refuses_secret(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = Path(temp) / "decisions.jsonl"
            proc = self.run_cli("--journal", journal, "record", "--unit", "u", "--target", '{"providerInstanceId":"codex","model":"sk-abcdefghijklmnop"}')
            self.assertEqual(proc.returncode, 2)
            self.assertFalse(journal.exists())


if __name__ == "__main__": unittest.main()
