from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".opencode" / "skills" / "compat-matrix"
SCRIPT = SKILL / "scripts" / "compat_matrix.py"
CONSTRAINTS = SKILL / "data" / "constraints.json"
CAPABILITIES = ROOT / "tests" / "fixtures" / "capabilities.json"
FIXTURES = ROOT / "tests" / "fixtures" / "compat"


def load_module():
    spec = importlib.util.spec_from_file_location("compat_matrix", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=ROOT, text=True, capture_output=True)


def temporary_json(data: object):
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8", delete=False)
    json.dump(data, handle)
    handle.close()
    return Path(handle.name)


class CompatMatrixTests(unittest.TestCase):
    def test_shipped_constraints_validate(self):
        result = run("validate")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["valid"])

    def test_missing_source_fails_validation(self):
        self.assertTrue(load_module().validate_constraints(json.loads((FIXTURES / "bad-missing-source.json").read_text())))

    def test_bad_type_fails_validation(self):
        self.assertTrue(load_module().validate_constraints(json.loads((FIXTURES / "bad-type.json").read_text())))

    def test_duplicate_model_fails_validation(self):
        errors = load_module().validate_constraints(json.loads((FIXTURES / "bad-duplicate.json").read_text()))
        self.assertTrue(any("duplicate" in error for error in errors))

    def test_bad_date_fails_validation(self):
        self.assertTrue(load_module().validate_constraints(json.loads((FIXTURES / "bad-date.json").read_text())))

    def test_show_joins_exact_model_id(self):
        result = run("show", "--capabilities", str(CAPABILITIES), "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = json.loads(result.stdout)["models"]
        codex = next(row for row in rows if row["model"] == "gpt-6.1-sol")
        self.assertEqual(codex["status"], "joined")
        self.assertEqual(codex["context_window"], 1050000)

    def test_show_matches_prefix_stripped_id(self):
        module = load_module()
        constraints = module.load_constraints(CONSTRAINTS)
        caps = {"providers": [{"providerInstanceId": "openai", "canRunChildTask": True, "constraints": [], "models": [{"id": "openai/gpt-6-luna", "options": []}]}]}
        rows = module.join_rows(caps, constraints)
        matched = next(row for row in rows if row["model"] == "openai/gpt-6-luna")
        self.assertEqual(matched["status"], "joined")

    def test_live_values_and_options_take_precedence(self):
        module = load_module()
        constraints = module.load_constraints(CONSTRAINTS)
        caps = {"providers": [{"providerInstanceId": "codex", "canRunChildTask": True, "constraints": [], "models": [{"id": "gpt-6.1-sol", "context_window": 777, "tool_calling": False, "options": [{"id": "live", "values": ["yes"]}]}]}]}
        row = module.join_rows(caps, constraints)[0]
        self.assertEqual(row["context_window"], 777)
        self.assertIs(row["tool_calling"], False)
        self.assertEqual(row["options"][0]["id"], "live")

    def test_disabled_provider_marked_and_filtered(self):
        result = run("show", "--capabilities", str(CAPABILITIES), "--json")
        rows = json.loads(result.stdout)["models"]
        disabled = next(row for row in rows if row["providerInstanceId"] == "claudeAgent")
        self.assertTrue(disabled["disabled"])
        filtered = run("filter", "--capabilities", str(CAPABILITIES), "--require", "tool_calling", "--json")
        targets = json.loads(filtered.stdout)["targets"]
        self.assertNotIn("claudeAgent", [row["providerInstanceId"] for row in targets])

    def test_filter_applies_requirement_and_minimums(self):
        result = run("filter", "--capabilities", str(CAPABILITIES), "--require", "tool_calling,image_input", "--min-context", "400000", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        targets = json.loads(result.stdout)["targets"]
        self.assertTrue(targets)
        self.assertTrue(all(row["tool_calling"] is True and row["image_input"] is True and row["context_window"] >= 400000 for row in targets))

    def test_unknown_values_are_reported_separately(self):
        module = load_module()
        caps = {"providers": [{"providerInstanceId": "p", "canRunChildTask": True, "constraints": [], "models": [{"id": "unknown-live", "options": []}]}]}
        targets, unknown = module.filter_rows(module.join_rows(caps, []), ["image_input"], None, None)
        self.assertEqual(targets, [])
        self.assertEqual(unknown[0]["status"], "unknown — verify")

    def test_stale_lists_unverified_and_old_entries(self):
        data = {"schema_version": 1, "models": []}
        base = json.loads(CONSTRAINTS.read_text())
        entry = dict(base["models"][0], verified=True, retrieved="2000-01-01")
        data["models"] = [entry]
        path = temporary_json(data)
        try:
            result = run("stale", "--constraints", str(path), "--max-age-days", "60", "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(json.loads(result.stdout)["entries"]), 1)
        finally:
            path.unlink()

    def test_cli_exit_codes_for_validation_strict_and_bad_input(self):
        invalid = run("validate", "--constraints", str(FIXTURES / "bad-date.json"))
        self.assertEqual(invalid.returncode, 1)
        strict = run("filter", "--capabilities", str(CAPABILITIES), "--min-context", "999999999", "--strict")
        self.assertEqual(strict.returncode, 1)
        bad = run("show", "--capabilities", str(FIXTURES / "no-such-file.json"))
        self.assertEqual(bad.returncode, 2)


if __name__ == "__main__":
    unittest.main()
