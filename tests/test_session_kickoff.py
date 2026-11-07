"""Tests for session-kickoff's role persistence and managed instruction files."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".opencode/skills/session-kickoff/scripts/kickoff.py"
PRESETS = ROOT / ".opencode/skills/session-kickoff/data/presets.json"
CAPS = ROOT / "tests/fixtures/session_kickoff/capabilities.json"


class KickoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.env = dict(os.environ, HOME=str(self.root / "home"), XDG_CONFIG_HOME=str(self.root / "config"))

    def run_cli(self, *args, cwd=None):
        return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], cwd=cwd, env=self.env, text=True, capture_output=True)

    def set_role(self, preset="planner", *extra, repo=None):
        return self.run_cli("set", "--preset", preset, "--repo", repo or self.repo, *extra)

    def test_presets_schema_and_default(self):
        data = json.loads(PRESETS.read_text())
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["default"], "planner")

    def test_each_preset_required_fields(self):
        for item in json.loads(PRESETS.read_text())["presets"]:
            for key in ("id", "summary", "main_agent", "delegation", "policies"):
                self.assertIn(key, item)

    def test_show_builtin_source(self):
        out = json.loads(self.run_cli("show", "--repo", self.repo).stdout)
        self.assertEqual((out["role"]["id"], out["source"]), ("planner", "builtin"))

    def test_show_global_source(self):
        global_file = self.root / "roles.json"
        self.set_role("builder", "--scope", "global", "--global-file", global_file)
        out = json.loads(self.run_cli("show", "--repo", self.repo, "--global-file", global_file).stdout)
        self.assertEqual((out["role"]["id"], out["source"]), ("builder", "global"))

    def test_show_project_precedes_global(self):
        gf = self.root / "global.json"
        self.set_role("builder", "--scope", "global", "--global-file", gf)
        self.set_role("reviewer")
        out = json.loads(self.run_cli("show", "--repo", self.repo, "--global-file", gf).stdout)
        self.assertEqual((out["role"]["id"], out["source"]), ("reviewer", "project"))

    def test_set_writes_valid_role_file(self):
        self.assertEqual(self.set_role().returncode, 0)
        value = json.loads((self.repo / ".agent-role.json").read_text())
        self.assertEqual(value["role"]["id"], "planner")

    def test_set_string_override(self):
        self.set_role("planner", "--set", "delegation.implementation.model=gpt-6.1-sol")
        role = json.loads((self.repo / ".agent-role.json").read_text())["role"]
        self.assertEqual(role["delegation"]["implementation"]["model"], "gpt-6.1-sol")

    def test_set_boolean_override(self):
        self.set_role("planner", "--set", "policies.ignore_cost=true")
        self.assertIs(json.loads((self.repo / ".agent-role.json").read_text())["role"]["policies"]["ignore_cost"], True)

    def test_set_json_override(self):
        self.set_role("planner", "--set", 'delegation.implementation.options={"reasoningEffort":"max"}')
        self.assertEqual(json.loads((self.repo / ".agent-role.json").read_text())["role"]["delegation"]["implementation"]["options"]["reasoningEffort"], "max")

    def test_unknown_preset_is_usage_error(self):
        self.assertEqual(self.set_role("nobody").returncode, 2)

    def test_bad_override_path_is_usage_error(self):
        self.assertEqual(self.set_role("planner", "--set", "unknown.x=y").returncode, 2)

    def test_agents_md_create(self):
        self.assertEqual(self.set_role("planner", "--agents-md").returncode, 0)
        self.assertIn("<!-- toolkit:role:begin -->", (self.repo / "AGENTS.md").read_text())

    def test_agents_md_replaces_markers_preserving_bytes(self):
        path = self.repo / "AGENTS.md"
        path.write_bytes(b"prefix\xff\n<!-- toolkit:role:begin -->old<!-- toolkit:role:end -->\nsuffix\n")
        result = self.set_role("planner", "--agents-md")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(path.read_bytes().startswith(b"prefix\xff\n"))
        self.assertTrue(path.read_bytes().endswith(b"\nsuffix\n"))

    def test_agents_md_refuses_unmarked_file(self):
        (self.repo / "AGENTS.md").write_text("keep")
        self.assertEqual(self.set_role("planner", "--agents-md").returncode, 1)

    def test_agents_md_append_unmarked_file(self):
        path = self.repo / "AGENTS.md"; path.write_bytes(b"keep")
        self.assertEqual(self.set_role("planner", "--agents-md", "--append").returncode, 0)
        self.assertTrue(path.read_bytes().startswith(b"keep\n"))

    def test_agents_md_rejects_malformed_markers(self):
        (self.repo / "AGENTS.md").write_text("<!-- toolkit:role:end -->")
        self.assertEqual(self.set_role("planner", "--agents-md").returncode, 1)

    def test_agents_md_is_idempotent(self):
        self.set_role("planner", "--agents-md"); before = (self.repo / "AGENTS.md").read_bytes()
        self.set_role("planner", "--agents-md")
        self.assertEqual((self.repo / "AGENTS.md").read_bytes(), before)

    def test_brief_content_and_line_limit(self):
        text = self.run_cli("brief", "--repo", self.repo).stdout
        self.assertIn("Role: planner", text)
        self.assertIn("implementation → codex:gpt-6-luna", text)
        self.assertIn("research → antigravity:gemini-3.8-flash", text)
        self.assertIn("Announcement: Role: planner", text)
        self.assertLessEqual(len(text.splitlines()), 40)

    def test_brief_reflects_overrides(self):
        self.set_role("planner", "--set", "policies.ignore_cost=true")
        self.assertIn("ignore_cost=True", self.run_cli("brief", "--repo", self.repo).stdout)

    def test_onboard_done_and_undone(self):
        (self.repo / "AGENTS.md").write_text("<!-- toolkit:guardrails:begin -->ok<!-- toolkit:guardrails:end -->")
        (self.repo / ".agent-role.json").write_text('{"schema_version":1,"role":{}}')
        result = json.loads(self.run_cli("onboard", "--repo", self.repo, "--json").stdout)["checklist"]
        self.assertFalse(result[0]["done"])
        self.assertTrue(result[2]["done"])
        self.assertTrue(result[3]["done"])
        self.assertTrue(result[4]["done"])

    def test_check_gemini_present(self):
        result = self.run_cli("check", "--repo", self.repo, "--capabilities", CAPS)
        self.assertEqual(result.returncode, 0)

    def test_check_missing_provider_warning_and_strict(self):
        caps = json.loads(CAPS.read_text()); caps["providers"] = [p for p in caps["providers"] if p["providerInstanceId"] != "antigravity"]
        path = self.root / "caps.json"; path.write_text(json.dumps(caps))
        self.assertEqual(self.run_cli("check", "--repo", self.repo, "--capabilities", path).returncode, 0)
        strict = self.run_cli("check", "--strict", "--repo", self.repo, "--capabilities", path)
        self.assertEqual(strict.returncode, 1); self.assertIn("warning", strict.stderr)

    def test_check_disabled_provider_warning(self):
        caps = json.loads(CAPS.read_text())
        for provider in caps["providers"]:
            if provider["providerInstanceId"] == "antigravity": provider["canRunChildTask"] = False
        path = self.root / "caps.json"; path.write_text(json.dumps(caps))
        self.assertIn("disabled", self.run_cli("check", "--repo", self.repo, "--capabilities", path).stderr)

    def test_install_rules_creates_missing_parent(self):
        target = self.root / "new" / "dir" / "CLAUDE.md"
        self.assertEqual(self.run_cli("install-rules", "--file", target).returncode, 0)
        self.assertTrue(target.exists())

    def test_install_rules_appends_unmarked_and_preserves_existing(self):
        target = self.root / "CLAUDE.md"; target.write_bytes(b"original")
        self.run_cli("install-rules", "--file", target)
        self.assertTrue(target.read_bytes().startswith(b"original\n\n"))

    def test_install_rules_refreshes_and_is_idempotent(self):
        target = self.root / "CLAUDE.md"
        self.run_cli("install-rules", "--file", target); first = target.read_bytes()
        self.assertEqual(self.run_cli("install-rules", "--file", target).returncode, 0)
        self.assertEqual(target.read_bytes(), first)

    def test_install_rules_rejects_malformed_markers(self):
        target = self.root / "CLAUDE.md"; target.write_text("<!-- toolkit:session-kickoff:end -->")
        self.assertEqual(self.run_cli("install-rules", "--file", target).returncode, 1)

    def test_install_rules_check_drift_does_not_write(self):
        target = self.root / "CLAUDE.md"; target.write_bytes(b"old")
        self.assertEqual(self.run_cli("install-rules", "--file", target, "--check").returncode, 1)
        self.assertEqual(target.read_bytes(), b"old")


if __name__ == "__main__":
    unittest.main()
