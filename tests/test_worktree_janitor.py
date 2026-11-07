from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".opencode/skills/worktree-janitor/scripts/janitor.py"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, text=True,
                          capture_output=True).stdout.strip()


class JanitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        (self.repo / "file").write_text("base\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "base")

    def invoke(self, *args: str, env=None):
        return subprocess.run([sys.executable, str(SCRIPT), *args], text=True,
                              capture_output=True, env=env)

    def test_scan_is_read_only_and_missing_gh_skips_prs(self):
        before = git(self.repo, "status", "--porcelain") + git(self.repo, "worktree", "list", "--porcelain")
        fakebin = self.root / "bin"
        fakebin.mkdir()
        (fakebin / "git").symlink_to(shutil.which("git"))
        env = dict(os.environ, PATH=str(fakebin))
        result = self.invoke("scan", "--repo", str(self.repo), "--json", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [])
        after = git(self.repo, "status", "--porcelain") + git(self.repo, "worktree", "list", "--porcelain")
        self.assertEqual(before, after)

    def test_clean_requires_confirmation_and_removes_merged_branch_and_worktree(self):
        git(self.repo, "branch", "merged")
        wt = self.root / "wt"
        git(self.repo, "worktree", "add", "-b", "wt-branch", str(wt))
        # Merge its commit into base so both the branch and worktree are eligible.
        (wt / "new").write_text("x")
        git(wt, "add", "."); git(wt, "commit", "-qm", "work")
        git(self.repo, "merge", "--ff-only", "wt-branch")
        findings = json.loads(self.invoke("scan", "--repo", str(self.repo), "--json").stdout)
        worktree = next(f for f in findings if f["kind"] == "worktree")
        branch = next(f for f in findings if f.get("branch") == "merged")
        dry = self.invoke("clean", "--repo", str(self.repo), "--id", branch["id"])
        self.assertEqual(dry.returncode, 0)
        self.assertIn("would:", dry.stdout)
        applied = self.invoke("clean", "--repo", str(self.repo), "--apply", "--id", worktree["id"], "--id", branch["id"])
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        self.assertFalse(wt.exists())
        self.assertNotIn("refs/heads/merged", git(self.repo, "show-ref"))

    def test_refuses_dirty_worktree_and_branch_with_unique_commits(self):
        wt = self.root / "dirty"
        git(self.repo, "worktree", "add", "-b", "dirty-branch", str(wt))
        (wt / "file").write_text("dirty\n")
        result = self.invoke("scan", "--repo", str(self.repo), "--json")
        findings = json.loads(result.stdout)
        dirty = next(f for f in findings if f["kind"] == "worktree")
        self.assertFalse(dirty["safe_to_remove"])
        out = self.invoke("clean", "--repo", str(self.repo), "--apply", "--id", dirty["id"])
        self.assertEqual(out.returncode, 1)
        self.assertTrue(wt.exists())
        git(wt, "checkout", "-q", "-b", "unique")
        (wt / "new").write_text("unique\n"); git(wt, "add", "."); git(wt, "commit", "-qm", "unique")
        branch = next(f for f in json.loads(self.invoke("scan", "--repo", str(self.repo), "--json").stdout) if f.get("branch") == "unique")
        out = self.invoke("clean", "--repo", str(self.repo), "--apply", "--id", branch["id"])
        self.assertEqual(out.returncode, 1)
        self.assertIn("refs/heads/unique", git(self.repo, "show-ref"))


if __name__ == "__main__":
    unittest.main()
