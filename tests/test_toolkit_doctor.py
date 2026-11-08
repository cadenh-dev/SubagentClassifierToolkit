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

    def fake_runner(self, auth_ok):
        def runner(args, **kwargs):
            if args[1:3] == ["auth", "status"]:
                return SimpleNamespace(returncode=0 if auth_ok else 1, stdout="", stderr="")
            if args[1:] == ["remote"]:
                return SimpleNamespace(returncode=0, stdout="origin\n", stderr="")
            if "get-url" in args:
                return SimpleNamespace(returncode=0, stdout="git@github.com:org/repo.git\n", stderr="")
            if "ls-remote" in args:
                return SimpleNamespace(returncode=1, stdout="", stderr="git@github.com: Permission denied (publickey).")
            if "rev-list" in args:
                return SimpleNamespace(returncode=1, stdout="", stderr="no upstream")
            raise AssertionError(args)
        return runner

    def test_remote_no_suggestion_when_unauthenticated(self):
        with tempfile.TemporaryDirectory() as t:
            repo = Path(t)
            result = d.doctor(repo, repo / "i", None, repo, "", .1, runner=self.fake_runner(False),
                              which=lambda c, **k: "/fake" if c in ("git", "gh") else None)
            remote = next(x for x in result if x["id"] == "git.remote.origin")
            self.assertEqual(remote["status"], "error")
            self.assertNotIn("suggestion", remote["details"])

    def write_secret_home(self, home):
        sentinels = ["SENTINEL_ENV_A1", "SENTINEL_HDR_B2", "SENTINEL_URL_C3", "SENTINEL_TOML_D4", "SENTINEL_GEM_E5"]
        files = {
            ".config/opencode/opencode.jsonc": '{\n // comment\n "mcp": {"oc-server": {"environment": {"TOKEN": "SENTINEL_ENV_A1"},'
                                               ' "url": "https://user:SENTINEL_URL_C3@example.test/mcp"}}}',
            ".claude.json": '{"mcpServers": {"claude-server": {"headers": {"Authorization": "Bearer SENTINEL_HDR_B2"}}}}',
            ".codex/config.toml": '[mcp_servers.codex-server]\ncommand = "x"\nenv = { API_KEY = "SENTINEL_TOML_D4" }\n',
            ".gemini/settings.json": '{"mcpServers": {"gemini-server": {"env": {"KEY": "SENTINEL_GEM_E5"}}}}',
        }
        for rel, text in files.items():
            target = home / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        return sentinels

    def test_secrets_never_in_doctor_output(self):
        with tempfile.TemporaryDirectory() as t:
            home = Path(t)
            sentinels = self.write_secret_home(home)
            result = d.doctor(home, home / "i", None, home, "", .1, which=lambda *a, **k: None)
            dumped = json.dumps(result)
            self.assertIn("claude-server", dumped)
            self.assertIn("oc-server", dumped)
            for sentinel in sentinels:
                self.assertNotIn(sentinel, dumped)

    def test_secrets_never_in_cli_json(self):
        with tempfile.TemporaryDirectory() as t:
            home = Path(t)
            sentinels = self.write_secret_home(home)
            env = {"HOME": str(home), "PATH": "/usr/bin:/bin"}
            proc = subprocess.run(["python3", str(ROOT / ".opencode/skills/toolkit-doctor/scripts/doctor.py"),
                                   "--repo", str(home), "--json", "--timeout", "2"],
                                  capture_output=True, text=True, env=env)
            self.assertIn(proc.returncode, (0, 1), proc.stderr)
            self.assertIn("gemini-server", proc.stdout)
            for sentinel in sentinels:
                self.assertNotIn(sentinel, proc.stdout + proc.stderr)

if __name__ == "__main__":
    unittest.main()
