import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).parents[1] / ".opencode/skills/secret-preflight/scripts/preflight.py"
spec = importlib.util.spec_from_file_location("preflight", SCRIPT)
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


class SecretPreflightTests(unittest.TestCase):
    def manifest(self, root, sources):
        path = root / "manifest.json"
        path.write_text(json.dumps({"secrets": [{"name": "demo", "purpose": "test", "required": True, "sources": sources, "used_by": []}]}))
        return path

    def cli(self, *args, env=None):
        return subprocess.run(["python3", str(SCRIPT), *map(str, args)], capture_output=True, text=True, env=env)

    def test_env_file_and_command_values_never_escape(self):
        fake = "fake-private-value-123"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secret_file = root / "secret"
            secret_file.write_text(fake)
            manifest = self.manifest(root, [{"type": "env", "var": "PREFLIGHT_TEST"}, {"type": "file", "path": str(secret_file)}, {"type": "command", "check": ["python3", "-c", f"import sys; print('{fake}'); sys.exit(1)"]}])
            env = dict(os.environ, PREFLIGHT_TEST=fake)
            result = self.cli("check", "--manifest", manifest, "--json", env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn(fake, result.stdout + result.stderr)

    def test_file_source_never_opened(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secret_file = root / "secret"
            secret_file.write_text("x")
            entries = [{"required": True, "name": "x", "sources": [{"type": "file", "path": str(secret_file)}]}]
            with patch.object(Path, "open", side_effect=AssertionError("read opened")):
                result, missing = preflight.check(entries, root, 1)
            self.assertFalse(missing)
            self.assertEqual(result[0]["status"], "present")

    def test_scan_reports_location_and_pattern_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            fixture = root / "credential.txt"
            fake = "ghp_" + "a" * 28
            fixture.write_text(fake + "\n")
            subprocess.run(["git", "-C", str(root), "add", "credential.txt"], check=True)
            findings = preflight.scan([], root)
            self.assertEqual(findings[0], {"file": "credential.txt", "line": 1, "pattern": "GitHub token"})
            self.assertNotIn(fake, json.dumps(findings))

    def test_commands_skipped_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            entries = [{"name": "x", "required": True, "sources": [{"type": "command", "check": ["echo", "x"]}]}]
            with patch.object(preflight.subprocess, "run") as run:
                records, missing = preflight.check(entries, root, 1)
            run.assert_not_called()
            self.assertTrue(missing)
            self.assertEqual(records[0]["sources"][0], {"type": "command", "status": "skipped", "reason": "commands not allowed"})

    def test_allowed_command_uses_minimal_environment_and_closed_stdin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            entries = [{"name": "x", "required": True, "sources": [{"type": "command", "check": ["echo", "x"]}]}]
            with patch.dict(os.environ, {"PATH": "/bin", "HOME": "/tmp", "UNRELATED_SECRET": "hidden"}), \
                    patch.object(preflight.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
                records, missing = preflight.check(entries, root, 1, allow_commands=True)
            self.assertFalse(missing)
            kwargs = run.call_args.kwargs
            self.assertEqual(kwargs["env"], {"PATH": "/bin", "HOME": "/tmp"})
            self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(records[0]["status"], "present")

    def test_nested_tracked_env_file_is_finding_but_example_is_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "config").mkdir()
            (root / "config/.env").write_text("placeholder")
            (root / ".env.example").write_text("KEY=your-value")
            subprocess.run(["git", "-C", str(root), "add", "-f", "config/.env", ".env.example"], check=True)
            findings = preflight.scan([], root)
            self.assertEqual([item["file"] for item in findings], ["config/.env"])

    def test_literal_assignment_is_reported(self):
        findings = self.scan_text("API_KEY=abc123", ["API_KEY"])
        self.assertEqual(findings[0]["pattern"], "manifest env var assigned literal")

    def test_environment_references_are_not_reported_as_literals(self):
        for line in ("API_KEY = os.environ['API_KEY']", "API_KEY=${API_KEY}", "API_KEY=process.env.API_KEY",
                     "API_KEY=env('API_KEY')", "API_KEY=getenv('API_KEY')", "API_KEY=<API_KEY>",
                     "API_KEY=changeme", "API_KEY=your-token", "API_KEY=xxx", "API_KEY=''", "API_KEY=\"\""):
            with self.subTest(line=line):
                self.assertEqual(self.scan_text(line, ["API_KEY"]), [])

    def scan_text(self, line, names):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "config.txt").write_text(line + "\n")
            subprocess.run(["git", "-C", str(root), "add", "config.txt"], check=True)
            entries = [{"sources": [{"type": "env", "var": name} for name in names]}]
            return preflight.scan(entries, root)

    def test_binary_files_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "binary").write_bytes(b"\0" + b"ghp_" + b"a" * 28)
            subprocess.run(["git", "-C", str(root), "add", "binary"], check=True)
            self.assertEqual(preflight.scan([], root), [])

    def test_manifest_errors_identify_entry_and_field_without_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "manifest.json"
            manifest.write_text(json.dumps({"secrets": [{"name": "private-value", "purpose": "x", "required": True,
                                                            "used_by": [], "sources": [{"type": "env", "var": 42}]}]}))
            with self.assertRaisesRegex(preflight.InputError, r"entry 0 source 0: invalid var field") as caught:
                preflight.load_manifest(manifest)
            self.assertNotIn("private-value", str(caught.exception))

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            present = root / "present.json"
            present.write_text(json.dumps({"secrets": [{"name": "demo", "purpose": "test", "required": True,
                                                          "sources": [{"type": "env", "var": "PATH"}], "used_by": []}]}))
            missing = root / "missing.json"
            missing.write_text(json.dumps({"secrets": [{"name": "demo", "purpose": "test", "required": True,
                                                          "sources": [{"type": "env", "var": "PREFLIGHT_MISSING_TEST"}], "used_by": []}]}))
            self.assertEqual(self.cli("check", "--manifest", present, env={"PATH": "/bin"}).returncode, 0)
            self.assertEqual(self.cli("check", "--manifest", missing, env={"PATH": "/bin"}).returncode, 1)

    def test_cli_scan_findings_exit_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / ".env").write_text("placeholder")
            subprocess.run(["git", "-C", str(root), "add", "-f", ".env"], check=True)
            manifest = self.manifest(root, [])
            self.assertEqual(self.cli("scan-repo", "--manifest", manifest, "--root", root).returncode, 1)

    def test_bad_manifest_and_timeout_exit_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bad = root / "bad.json"
            bad.write_text('{"secrets": [{"name":"safe", "sources":[]}]}')
            self.assertEqual(self.cli("check", "--manifest", bad).returncode, 2)
            good = self.manifest(root, [])
            self.assertEqual(self.cli("check", "--manifest", good, "--timeout", "0").returncode, 2)

    @unittest.skipIf(os.name == "nt", "POSIX mode bits are unavailable on Windows")
    def test_file_permission_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            secret_file = Path(tmp) / "credential"
            secret_file.write_text("placeholder")
            entry = [{"name": "x", "required": True, "sources": [{"type": "file", "path": str(secret_file)}]}]
            secret_file.chmod(0o644)
            report, _ = preflight.check(entry, Path(tmp), 1)
            self.assertEqual(report[0]["sources"][0]["warning"], "group/world readable")
            secret_file.chmod(0o600)
            report, _ = preflight.check(entry, Path(tmp), 1)
            self.assertNotIn("warning", report[0]["sources"][0])


if __name__ == "__main__":
    unittest.main()
