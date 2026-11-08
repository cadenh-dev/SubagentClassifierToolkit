"""Tests for installing the delegation-check bootstrap snippet."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".opencode" / "skills" / "subagent-orchestrator"
SCRIPT = SKILL / "scripts" / "install_bootstrap.py"
SNIPPET = (SKILL / "assets" / "bootstrap.md").read_text(encoding="utf-8").strip()


class InstallBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.agents = self.project / "AGENTS.md"

    def run_script(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--project", str(self.project), *args],
            capture_output=True,
            text=True,
        )

    def write(self, text: str) -> None:
        self.agents.write_bytes(text.encode("utf-8"))

    def read(self) -> str:
        return self.agents.read_bytes().decode("utf-8")

    def test_snippet_names_the_skill_and_the_opt_out(self) -> None:
        self.assertIn("`subagent-orchestrator`", SNIPPET)
        self.assertIn("Otherwise do the work yourself.", SNIPPET)

    def test_creates_file(self) -> None:
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("created", result.stdout)
        self.assertEqual(self.read(), SNIPPET + "\n")

    def test_appends_and_preserves_existing_text(self) -> None:
        self.write("# Project\n\nUse pnpm.")
        self.assertEqual(self.run_script().returncode, 0)
        self.assertEqual(self.read(), "# Project\n\nUse pnpm.\n\n" + SNIPPET + "\n")

    def test_idempotent_and_check(self) -> None:
        self.write("# Project\n")
        self.assertEqual(self.run_script("--check").returncode, 1)
        self.run_script()
        first = self.read()
        result = self.run_script()
        self.assertIn("current", result.stdout)
        self.assertEqual(self.read(), first)
        self.assertEqual(self.run_script("--check").returncode, 0)

    def test_updates_stale_block_in_place(self) -> None:
        stale = "<!-- subagent-orchestrator:bootstrap v0 -->\nold text\n<!-- /subagent-orchestrator:bootstrap -->"
        self.write(f"# A\n\n{stale}\n\n# B\n")
        self.assertEqual(self.run_script("--check").returncode, 1)
        result = self.run_script()
        self.assertIn("updated", result.stdout)
        text = self.read()
        self.assertTrue(text.startswith("# A\n\n" + SNIPPET))
        self.assertTrue(text.endswith("\n\n# B\n"))
        self.assertNotIn("old text", text)

    def test_keeps_crlf_line_endings(self) -> None:
        self.agents.write_bytes(b"# Project\r\n")
        self.run_script()
        raw = self.agents.read_bytes()
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))

    def test_remove(self) -> None:
        self.write("# Project\n")
        self.run_script()
        result = self.run_script("--remove")
        self.assertIn("removed", result.stdout)
        self.assertEqual(self.read(), "# Project\n")
        self.assertIn("absent", self.run_script("--remove").stdout)

    def test_dry_run_writes_nothing(self) -> None:
        result = self.run_script("--dry-run")
        self.assertIn("would be created", result.stdout)
        self.assertFalse(self.agents.exists())

    def test_other_file_and_path_escape(self) -> None:
        self.assertEqual(self.run_script("--file", "CLAUDE.md").returncode, 0)
        self.assertTrue((self.project / "CLAUDE.md").exists())
        self.assertEqual(self.run_script("--file", "../AGENTS.md").returncode, 2)

    def test_duplicate_blocks_refused(self) -> None:
        self.write(SNIPPET + "\n\n" + SNIPPET + "\n")
        self.assertEqual(self.run_script().returncode, 2)


if __name__ == "__main__":
    unittest.main()
