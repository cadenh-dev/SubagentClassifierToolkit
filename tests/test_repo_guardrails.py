import json
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / ".opencode/skills/repo-guardrails/scripts/generate_agents_md.py"
FIX = Path(__file__).parent / "fixtures/guardrails"

class GuardrailsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.profile = self.root / "profile.json"
        self.profile.write_text((FIX / "profile.json").read_text())
    def run_cli(self, *args): return subprocess.run(["python3", str(SCRIPT), "--profile", str(self.profile), "--repo", str(self.root), *args], capture_output=True)
    def test_preserves_bytes_and_repeat(self):
        (self.root / "AGENTS.md").write_bytes(b"prefix\r\n\xff" if False else b"prefix\n")
        self.run_cli("--append"); path = self.root / "AGENTS.md"; first = path.read_bytes()
        self.assertTrue(first.startswith(b"prefix\n")); self.assertEqual(self.run_cli().returncode, 0); self.assertEqual(path.read_bytes(), first)
        self.assertEqual(self.run_cli("--check").returncode, 0)
    def test_check_drift_no_write(self):
        p = self.root / "AGENTS.md"; p.write_text("old\n"); old = p.read_bytes()
        self.assertEqual(self.run_cli("--check", "--append").returncode, 1); self.assertEqual(p.read_bytes(), old)
    def test_unmarked_refused_and_append(self):
        (self.root / "AGENTS.md").write_text("existing\n")
        self.assertEqual(self.run_cli().returncode, 1); self.assertEqual(self.run_cli("--append").returncode, 0)
    def test_malformed(self):
        for content in ("<!-- toolkit:guardrails:end -->\n<!-- toolkit:guardrails:begin -->", "<!-- toolkit:guardrails:begin -->", "<!-- toolkit:guardrails:begin --><!-- toolkit:guardrails:begin --><!-- toolkit:guardrails:end -->"):
            (self.root / "AGENTS.md").write_text(content)
            self.assertEqual(self.run_cli().returncode, 1)
    def test_bad_input_exit_2(self):
        self.profile.write_text("{not json")
        self.assertEqual(self.run_cli("--stdout").returncode, 2)
        self.profile.write_text(json.dumps({"schema_version": 99}))
        self.assertEqual(self.run_cli("--stdout").returncode, 2)
    def test_new_file_ends_with_newline_and_is_stable(self):
        self.assertEqual(self.run_cli().returncode, 0)
        path = self.root / "AGENTS.md"; first = path.read_bytes()
        self.assertTrue(first.endswith(b"-->\n"))
        self.assertEqual(self.run_cli().returncode, 0); self.assertEqual(path.read_bytes(), first)
    def test_polyglot_path_routes(self):
        data = json.loads(self.profile.read_text()); data["ambiguous"] = True
        data["languages"] = [{"name": "python", "manifests": ["api/pyproject.toml"], "examples": ["api/app/main.py"]},
                             {"name": "java", "manifests": ["worker/pom.xml"], "examples": []}]
        self.profile.write_text(json.dumps(data))
        out = self.run_cli("--stdout").stdout.decode()
        self.assertIn("`api/` → python", out); self.assertIn("`worker/` → java", out)
    def test_stdout(self): self.assertIn(b"<!-- toolkit:guardrails:begin -->", self.run_cli("--stdout").stdout)

if __name__ == "__main__": unittest.main()
