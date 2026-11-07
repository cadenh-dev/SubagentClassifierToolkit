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


def git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], check=check, text=True,
                            capture_output=True, timeout=15)
    return result.stdout.strip()


class JanitorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        git(self.repo, "config", "commit.gpgsign", "false")
        (self.repo / "file").write_text("base\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "base")

    def invoke(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=ROOT,
                              text=True, capture_output=True, env=env, timeout=30)

    def scan(self, *args: str, env: dict[str, str] | None = None) -> list[dict]:
        result = self.invoke("scan", "--repo", str(self.repo), "--json", *args, env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def worktree(self, name: str, *, branch: str | None = None) -> Path:
        path = self.root / name
        args = ["worktree", "add", "-q"]
        if branch:
            args += ["-b", branch]
        args += [str(path)]
        git(self.repo, *args)
        return path

    def commit(self, path: Path, file: str = "extra", content: str = "x\n") -> None:
        (path / file).write_text(content, encoding="utf-8")
        git(path, "add", ".")
        git(path, "commit", "-qm", "change")

    def test_scan_without_remote_succeeds(self) -> None:
        result = self.invoke("scan", "--repo", str(self.repo), "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any(item["kind"] == "skip" for item in json.loads(result.stdout)))

    def test_scan_is_read_only(self) -> None:
        def snapshot() -> tuple[str, str, str]:
            return (git(self.repo, "worktree", "list", "--porcelain"),
                    git(self.repo, "branch", "-a"), git(self.repo, "status", "--porcelain"))
        before = snapshot()
        self.scan()
        self.assertEqual(snapshot(), before)

    def test_scan_never_marks_main_worktree_safe(self) -> None:
        (self.repo / "file").write_text("dirty\n", encoding="utf-8")
        item = next(item for item in self.scan() if item["kind"] == "worktree")
        self.assertFalse(item["safe_to_remove"])

    def test_detects_merged_clean_worktree(self) -> None:
        path = self.worktree("merged", branch="merged-wt")
        self.commit(path)
        git(self.repo, "merge", "--ff-only", "merged-wt")
        item = next(item for item in self.scan() if item.get("path") == str(path))
        self.assertTrue(item["safe_to_remove"])
        self.assertIn("branch merged into base", item["reasons"])

    def test_detects_dirty_worktree(self) -> None:
        path = self.worktree("dirty", branch="dirty-wt")
        (path / "file").write_text("dirty\n", encoding="utf-8")
        item = next(item for item in self.scan() if item.get("path") == str(path))
        self.assertIn("dirty", item["reasons"])
        self.assertFalse(item["safe_to_remove"])

    def test_detects_missing_directory_prunable_worktree(self) -> None:
        path = self.worktree("missing", branch="missing-wt")
        shutil.rmtree(path)
        item = next(item for item in self.scan() if item.get("path") == str(path))
        self.assertIn("prunable", item["reasons"])
        self.assertEqual(item["suggested_action"], "git worktree prune")

    def test_detects_merged_branch(self) -> None:
        git(self.repo, "branch", "old-merged")
        item = next(item for item in self.scan() if item.get("branch") == "old-merged")
        self.assertIn("merged into base", item["reasons"])
        self.assertTrue(item["safe_to_remove"])

    def test_detects_unmerged_branch_as_unsafe(self) -> None:
        git(self.repo, "checkout", "-qb", "unmerged")
        self.commit(self.repo)
        git(self.repo, "checkout", "-q", "main")
        item = next(item for item in self.scan() if item.get("branch") == "unmerged")
        self.assertIn("unique commits not in base", item["reasons"])
        self.assertFalse(item["safe_to_remove"])

    def test_detects_locked_worktree(self) -> None:
        path = self.worktree("locked", branch="locked-wt")
        git(self.repo, "worktree", "lock", str(path), "--reason", "test lock")
        item = next(item for item in self.scan() if item.get("path") == str(path))
        self.assertIn("locked", item["reasons"])
        self.assertFalse(item["safe_to_remove"])

    def test_clean_without_apply_changes_nothing(self) -> None:
        git(self.repo, "branch", "old-merged")
        item = next(item for item in self.scan() if item.get("branch") == "old-merged")
        result = self.invoke("clean", "--repo", str(self.repo), "--id", item["id"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("would:", result.stdout)
        self.assertTrue(git(self.repo, "show-ref", "--verify", "refs/heads/old-merged", check=False))

    def test_apply_removes_merged_clean_worktree_and_branch(self) -> None:
        path = self.worktree("merged", branch="merged-wt")
        self.commit(path)
        git(self.repo, "merge", "--ff-only", "merged-wt")
        git(self.repo, "branch", "old-merged")
        items = self.scan()
        selected = [item for item in items if item.get("path") == str(path) or item.get("branch") == "old-merged"]
        result = self.invoke("clean", "--repo", str(self.repo), "--apply",
                             *[arg for item in selected for arg in ("--id", item["id"])])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(path.exists())
        self.assertFalse(git(self.repo, "show-ref", "--verify", "refs/heads/old-merged", check=False))

    def test_apply_refuses_dirty_worktree(self) -> None:
        path = self.worktree("dirty", branch="dirty-wt")
        (path / "file").write_text("dirty", encoding="utf-8")
        item = next(item for item in self.scan() if item.get("path") == str(path))
        result = self.invoke("clean", "--repo", str(self.repo), "--apply", "--id", item["id"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("dirty", result.stdout)
        self.assertTrue(path.exists())

    def test_apply_refuses_unmerged_branch(self) -> None:
        git(self.repo, "checkout", "-qb", "unmerged")
        self.commit(self.repo)
        git(self.repo, "checkout", "-q", "main")
        item = next(item for item in self.scan() if item.get("branch") == "unmerged")
        result = self.invoke("clean", "--repo", str(self.repo), "--apply", "--id", item["id"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("commits not in base", result.stdout)

    def test_apply_refuses_current_branch(self) -> None:
        result = self.invoke("clean", "--repo", str(self.repo), "--apply", "--id", "br:" + "0" * 12)
        self.assertEqual(result.returncode, 2)  # unknown IDs remain input errors
        # A current branch ID is refused even though scan deliberately omits it.
        import hashlib
        ident = "br:" + hashlib.sha256(b"main").hexdigest()[:12]
        result = self.invoke("clean", "--repo", str(self.repo), "--apply", "--id", ident)
        self.assertEqual(result.returncode, 1)
        self.assertIn("current branch", result.stdout)

    def test_unknown_id_is_bad_input(self) -> None:
        result = self.invoke("clean", "--repo", str(self.repo), "--id", "br:unknown")
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown finding", result.stderr)

    def fake_gh(self, payload: list[dict]) -> tuple[Path, dict[str, str]]:
        bindir = self.root / "bin"
        bindir.mkdir()
        fake = bindir / "gh"
        fake.write_text("#!/usr/bin/env python3\nimport json,sys\n"
                        "if sys.argv[1:3] == ['auth','status']: sys.exit(0)\n"
                        f"print(json.dumps({payload!r}))\n", encoding="utf-8")
        fake.chmod(0o755)
        git(self.repo, "remote", "add", "origin", "https://github.com/example/project.git")
        return fake, dict(os.environ)

    def test_pr_scan_skips_when_gh_missing(self) -> None:
        bindir = self.root / "only-git"
        bindir.mkdir()
        (bindir / "git").symlink_to(shutil.which("git"))
        result = self.invoke("scan", "--repo", str(self.repo), "--json", env=dict(os.environ, PATH=str(bindir)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any(item["kind"] == "skip" and "gh is not available" in item["reasons"][0]
                            for item in json.loads(result.stdout)))

    def test_pr_scan_flags_stale_open_and_merged_head(self) -> None:
        oid = git(self.repo, "rev-parse", "HEAD")
        payload = [
            {"number": 12, "state": "OPEN", "headRefName": "feature", "headRefOid": "f" * 40,
             "updatedAt": "2000-01-01T00:00:00Z", "mergedAt": None},
            {"number": 13, "state": "OPEN", "headRefName": "already", "headRefOid": oid,
             "updatedAt": "2099-01-01T00:00:00Z", "mergedAt": None},
        ]
        fake, _ = self.fake_gh(payload)
        items = self.scan("--gh", str(fake))
        stale = next(item for item in items if item.get("pr") == "12")
        merged = next(item for item in items if item.get("pr") == "13")
        self.assertIn("no activity", stale["reasons"])
        self.assertIn("head branch merged into base", merged["reasons"])


if __name__ == "__main__":
    unittest.main()
