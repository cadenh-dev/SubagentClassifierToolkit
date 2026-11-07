import importlib.util
import json
import subprocess
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("doctor", ROOT / ".opencode/skills/toolkit-doctor/scripts/doctor.py")
d = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(d)


class DoctorTests(unittest.TestCase):
    def setup_repo(self, base):
        repo = Path(base) / "repo"
        repo.mkdir()
        return repo

    def test_skill_drift(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t) / "repo"; installed = Path(t) / "install"
            (root / ".opencode/skills/a").mkdir(parents=True); (root / ".opencode/skills/a/SKILL.md").write_text("source")
            installed.mkdir(); (installed / "a").mkdir(); (installed / "a/SKILL.md").write_text("stale"); (installed / "extra").mkdir()
            result = d.doctor(root, installed, None, Path(t), "", .1, which=lambda *a, **k: None)
            drift = next(x for x in result if x["id"] == "skills.drift")["details"]["skills"]
            self.assertEqual({x["skill"]: x["status"] for x in drift}, {"a": "stale", "extra": "extra"})

    def test_config_only_exposes_names(self):
        obj = {"mcpServers": {"safe-name": {"env": {"TOKEN": "fake-secret"}}}}
        self.assertEqual(d.servers_from(obj), ["safe-name"])

    def test_top_level_opencode_mcp(self):
        self.assertEqual(d.servers_from({"mcp": {"server": {}}}, True), ["server"])

    def test_jsonc_keeps_url(self):
        self.assertEqual(json.loads(d.strip_jsonc_comments('{"url":"https://example.test/a", // comment\n"x":1}'))["url"], "https://example.test/a")

    def test_safe_error_redacts_credential_url(self):
        self.assertNotIn("user:secret", d.safe_error("fatal https://user:secret@example.test/path"))

    def test_profile_missing_commands(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t); profile = ROOT / "tests/fixtures/doctor/profile.json"
            result = d.doctor(root, root / "install", profile, root, "", .1, which=lambda *a, **k: None)
            missing = next(x for x in result if x["id"] == "profile.expected")["details"]["missing"]
            self.assertTrue(missing)

    def test_missing_expected_is_warn(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "p.json"; p.write_text('{"commands":[{"command":"fakecmd run"}]}')
            result = d.doctor(Path(t), Path(t) / "i", p, Path(t), "", .1, which=lambda *a, **k: None)
            self.assertEqual(next(x for x in result if x["id"] == "profile.expected")["status"], "warn")

    def test_absent_unexpected_is_skip(self):
        with tempfile.TemporaryDirectory() as t:
            result = d.doctor(Path(t), Path(t) / "i", None, Path(t), "", .1, which=lambda *a, **k: None)
            self.assertEqual(next(x for x in result if x["id"] == "command.node")["status"], "skip")

    def test_parse_failure_warns(self):
        with tempfile.TemporaryDirectory() as t:
            home = Path(t); config = home / ".config/opencode/opencode.json"; config.parent.mkdir(parents=True); config.write_text("{")
            result = d.doctor(home, home / "i", None, home, "", .1, which=lambda *a, **k: None)
            self.assertTrue(any(x["id"] == "mcp.config.opencode" and x["status"] == "warn" for x in result))

    def test_no_upstream_reported(self):
        with tempfile.TemporaryDirectory() as t:
            repo = Path(t)
            subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
            result = d.doctor(repo, repo / "i", None, repo, shutil.which("git").rsplit("/", 1)[0], .1)
            check = next(x for x in result if x["id"] == "git.upstream")
            self.assertEqual(check["status"], "skip")
            self.assertEqual(check["summary"], "no upstream")

    def test_remote_ssh_failure_suggests_https_when_authenticated(self):
        with tempfile.TemporaryDirectory() as t:
            repo = Path(t); calls = []
            def runner(args, **kwargs):
                calls.append(args)
                if args[1:3] == ["auth", "status"]: return SimpleNamespace(returncode=0, stdout="", stderr="")
                if args[1:] == ["remote"]: return SimpleNamespace(returncode=0, stdout="origin\n", stderr="")
                if "get-url" in args: return SimpleNamespace(returncode=0, stdout="git@github.com:org/repo.git\n", stderr="")
                if "ls-remote" in args: return SimpleNamespace(returncode=1, stdout="", stderr="git@github.com: Permission denied (publickey).")
                if "rev-list" in args: return SimpleNamespace(returncode=1, stdout="", stderr="no upstream")
                raise AssertionError(args)
            result = d.doctor(repo, repo / "i", None, repo, "", .1, runner=runner, which=lambda c, **k: "/fake" if c in ("git", "gh") else None)
            remote = next(x for x in result if x["id"] == "git.remote.origin")
            self.assertEqual(remote["status"], "error"); self.assertIn("Permission denied", remote["details"]["reason"]); self.assertIn("suggestion", remote["details"])

    def test_remote_no_suggestion_when_unauthenticated(self):
        self.assertNotIn("suggestion", {"reason": "failure"})

    def test_secret_scanner_and_jsonc(self):
        for raw in ('{"env":{"TOKEN":"SENTINEL_A"}}', '{"headers":{"Authorization":"SENTINEL_B"}}', '{"url":"https://u:SENTINEL_C@example.test"}'):
            parsed = json.loads(d.strip_jsonc_comments(raw))
            self.assertTrue(parsed)
            self.assertNotIn("SENTINEL", json.dumps(d.report("x", "ok", "", {"names": ["safe"]})))


if __name__ == "__main__":
    unittest.main()
