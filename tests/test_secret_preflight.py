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
            fake = "ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            fixture.write_text(fake + "\n")
            subprocess.run(["git", "-C", str(root), "add", "credential.txt"], check=True)
            findings = preflight.scan([], root)
            self.assertEqual(findings[0], {"file": "credential.txt", "line": 1, "pattern": "GitHub token"})
            self.assertNotIn(fake, json.dumps(findings))


if __name__ == "__main__":
    unittest.main()
