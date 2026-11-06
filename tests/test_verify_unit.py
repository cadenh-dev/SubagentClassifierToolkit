#!/usr/bin/env python3
"""Focused tests for the roadmap verification harness.

Run:
  python3 -m unittest tests.test_verify_unit -v
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VERIFY = (
    ROOT
    / ".opencode"
    / "skills"
    / "subagent-orchestrator"
    / "scripts"
    / "verify_unit.py"
)


def py_command(code: str) -> str:
    """Build a harmless Python command string for a plan."""
    if os.name == "nt":
        return f"{subprocess.list2cmdline([sys.executable])} -c {subprocess.list2cmdline([code])}"
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"


def run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        cwd=str(repo),
        text=True,
        capture_output=True,
        check=False,
    )


def git_ok(repo: Path, *args: str) -> str:
    result = run_git(repo, *args)
    if result.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed in {repo}: {result.stderr.strip()}"
        )
    return result.stdout.strip()


def payload_of(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        raise AssertionError(
            f"verify_unit did not emit JSON\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        ) from None
    if not isinstance(payload, dict):
        raise AssertionError(f"verify_unit emitted non-object JSON: {payload!r}")
    return payload


class VerifyUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git_ok(self.repo, "init", "-q")
        git_ok(self.repo, "config", "user.email", "tests@example.invalid")
        git_ok(self.repo, "config", "user.name", "Verify Unit Tests")
        git_ok(self.repo, "config", "commit.gpgsign", "false")

        (self.repo / "src").mkdir()
        (self.repo / "src" / "base.py").write_text("BASE = 1\n", encoding="utf-8")
        (self.repo / "README.md").write_text("# fixture\n", encoding="utf-8")
        self.base = self.commit("base")
        self.plan = self.root / "plan.json"
        self.write_plan()

    def commit(self, message: str) -> str:
        git_ok(self.repo, "add", "-A")
        git_ok(self.repo, "commit", "-q", "-m", message)
        return git_ok(self.repo, "rev-parse", "HEAD")

    def change(self, relative: str, content: str = "change\n") -> str:
        target = self.repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return self.commit("head")

    def write_plan(
        self,
        *,
        commands: list[str] | None = None,
        write_scope: list[str] | None = None,
        acceptance: list[str] | None = None,
        unit_id: str = "one",
    ) -> None:
        plan = {
            "goal": "Verify one unit.",
            "mode": "delegated",
            "units": [
                {
                    "id": unit_id,
                    "title": "One unit",
                    "role": "implementation",
                    "objective": "Make the change.",
                    "depends_on": [],
                    "isolation": "worktree",
                    "mode": "async",
                    "target": {
                        "providerInstanceId": "opencode",
                        "model": "opencode-go/deepseek-v4.1-flash",
                    },
                    "write_scope": ["src/**"] if write_scope is None else write_scope,
                    "acceptance": (
                        ["The declared behavior works."]
                        if acceptance is None
                        else acceptance
                    ),
                    "commands": (
                        [py_command('print("ok")')] if commands is None else commands
                    ),
                    "deliverable": "A commit.",
                    "brief": "Do the work.",
                }
            ],
            "integration": {"order": [unit_id], "tests": [], "review": False},
        }
        self.plan.write_text(json.dumps(plan, indent=2), encoding="utf-8")

    def run_verify(
        self,
        *,
        plan: Path | None = None,
        unit: str = "one",
        repo: Path | None = None,
        base: str | None = None,
        timeout: str = "10",
        allow: bool = True,
        shell: bool = False,
        max_output: int | None = None,
        env: dict[str, str] | None = None,
        expected_git_dir: str | None = None,
        expected_git_common_dir: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        args = [
            sys.executable,
            str(VERIFY),
            "--plan",
            str(plan if plan is not None else self.plan),
            "--unit",
            unit,
            "--repo",
            str(repo if repo is not None else self.repo),
            "--base",
            self.base if base is None else base,
            f"--timeout={timeout}",
        ]
        if allow:
            args.append("--allow-commands")
        if shell:
            args.append("--shell")
        if max_output is not None:
            args.append(f"--max-output-bytes={max_output}")
        if expected_git_dir is not None:
            args.extend(["--expected-git-dir", expected_git_dir])
        if expected_git_common_dir is not None:
            args.extend(["--expected-git-common-dir", expected_git_common_dir])
        return subprocess.run(
            args,
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            timeout=120,
            env=env,
        )

    def assert_clean_cli(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("Traceback", result.stdout)

    # -- passing, failing, refusal ------------------------------------------

    def test_passing_command_and_scope_returns_pass(self) -> None:
        self.change("src/ok.py")
        result = self.run_verify()
        self.assert_clean_cli(result)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = payload_of(result)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["verdict"], "pass")
        self.assertEqual(payload["changed_paths"], ["src/ok.py"])
        self.assertEqual(payload["scope_violations"], [])
        self.assertEqual(payload["command_results"][0]["status"], "pass")
        self.assertEqual(payload["command_results"][0]["exit_code"], 0)
        self.assertFalse(payload["unit_details"]["acceptance_evaluated"])
        self.assertFalse(payload["verdict_scope"]["acceptance_prose_verified"])
        self.assertTrue(
            any("Acceptance criteria are prose" in item for item in payload["limitations"])
        )

    def test_failing_command_returns_fail(self) -> None:
        self.write_plan(commands=[py_command("raise SystemExit(7)")])
        result = self.run_verify()
        self.assert_clean_cli(result)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        payload = payload_of(result)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["verdict"], "fail")
        command = payload["command_results"][0]
        self.assertEqual(command["status"], "fail")
        self.assertEqual(command["exit_code"], 7)
        self.assertIn("7", command["reason"])

    def test_default_refuses_commands_without_allow(self) -> None:
        marker = self.repo / "marker.txt"
        self.write_plan(
            commands=[py_command('from pathlib import Path; Path("marker.txt").write_text("x")')],
            write_scope=[],
        )
        result = self.run_verify(allow=False)
        self.assert_clean_cli(result)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        payload = payload_of(result)
        self.assertEqual(payload["verdict"], "blocked")
        self.assertFalse(payload["ok"])
        self.assertTrue(
            any("--allow-commands" in error for error in payload["errors"]), payload
        )
        self.assertEqual(payload["command_results"][0]["status"], "not_run")
        self.assertFalse(marker.exists())

    def test_repo_fsmonitor_does_not_run_when_commands_are_blocked(self) -> None:
        marker = self.root / "fsmonitor-ran"
        hook = self.root / "fsmonitor.py"
        hook.write_text(
            "from pathlib import Path; import sys; "
            f"Path({str(marker)!r}).write_text('ran'); "
            "sys.stdin.read(); print('token')\n",
            encoding="utf-8",
        )
        git_ok(
            self.repo,
            "config",
            "core.fsmonitor",
            subprocess.list2cmdline([sys.executable, str(hook)]),
        )
        self.write_plan(commands=[py_command("print('must not run')")], write_scope=[])
        result = self.run_verify(allow=False)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertFalse(marker.exists(), "repo-configured fsmonitor hook ran")

    def test_allow_commands_executes_command(self) -> None:
        marker = self.repo / "marker.txt"
        self.write_plan(
            commands=[py_command('from pathlib import Path; Path("marker.txt").write_text("x")')],
            write_scope=[],
        )
        result = self.run_verify(allow=True)
        self.assert_clean_cli(result)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = payload_of(result)
        self.assertEqual(payload["verdict"], "pass")
        self.assertEqual(payload["command_results"][0]["status"], "pass")
        self.assertTrue(marker.exists())
        self.assertTrue(payload["git"]["untracked_after"])
        self.assertTrue(any("after command execution" in note for note in payload["limitations"]))

    def test_command_commit_moves_head_and_fails_verification(self) -> None:
        command = py_command(
            "from pathlib import Path; import subprocess; "
            "Path('src/command.py').write_text('x'); "
            "subprocess.run(['git','add','src/command.py'],check=True); "
            "subprocess.run(['git','commit','-m','command commit'],check=True)"
        )
        self.write_plan(commands=[command], write_scope=["src"])
        result = self.run_verify()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        payload = payload_of(result)
        self.assertEqual(payload["verdict"], "fail")
        self.assertTrue(payload["git"]["head_moved_during_verification"])
        self.assertTrue(any("HEAD moved" in error for error in payload["errors"]))

    def test_timeout_is_bounded(self) -> None:
        self.write_plan(
            commands=[py_command("import time; time.sleep(30)")],
            write_scope=[],
        )
        result = self.run_verify(timeout="0.3")
        self.assert_clean_cli(result)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        payload = payload_of(result)
        self.assertEqual(payload["verdict"], "fail")
        command = payload["command_results"][0]
        self.assertEqual(command["status"], "timeout")
        self.assertTrue(command["timed_out"])
        self.assertLess(payload["duration_seconds"], 10)

    def test_inherited_git_repository_overrides_are_ignored(self) -> None:
        other = self.root / "other"
        other.mkdir()
        git_ok(other, "init", "-q")
        git_ok(other, "config", "core.worktree", str(self.repo))
        env = os.environ.copy()
        env.update({"GIT_DIR": str(other / ".git"), "GIT_WORK_TREE": str(self.repo)})
        result = self.run_verify(env=env)
        self.assert_clean_cli(result)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = payload_of(result)
        self.assertEqual(payload["git"]["head_commit"], self.base)

    def test_external_gitfile_redirect_is_rejected(self) -> None:
        other = self.root / "other"
        other.mkdir()
        git_ok(other, "init", "-q")
        git_metadata = self.repo / ".git"
        for entry in git_metadata.rglob("*"):
            if entry.is_dir():
                continue
            try:
                os.chmod(entry, stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass
        shutil.rmtree(git_metadata)
        (self.repo / ".git").write_text(f"gitdir: {other / '.git'}\n", encoding="utf-8")
        result = self.run_verify()
        self.assert_clean_cli(result)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("linked worktree", payload_of(result)["errors"][0])

    def test_linked_worktree_requires_and_accepts_pinned_git_paths(self) -> None:
        linked = self.root / "linked"
        git_ok(self.repo, "worktree", "add", "--detach", str(linked), self.base)
        linked_git_dir = git_ok(linked, "rev-parse", "--absolute-git-dir")
        common_dir = git_ok(linked, "rev-parse", "--git-common-dir")
        if not Path(common_dir).is_absolute():
            common_dir = str(linked / common_dir)
        rejected = self.run_verify(repo=linked)
        self.assertEqual(rejected.returncode, 2, rejected.stdout + rejected.stderr)
        result = self.run_verify(
            repo=linked,
            expected_git_dir=linked_git_dir,
            expected_git_common_dir=common_dir,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_invalid_utf8_command_output_still_emits_json(self) -> None:
        self.write_plan(commands=[py_command("import sys; sys.stdout.buffer.write(bytes([129]))")], write_scope=[])
        result = self.run_verify()
        self.assert_clean_cli(result)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = payload_of(result)
        self.assertEqual(payload["command_results"][0]["stdout"], "\ufffd")

    def test_nested_glob_requires_explicit_double_star(self) -> None:
        self.change("src/nested/ok.py")
        self.write_plan(write_scope=["src/*.py"])
        result = self.run_verify()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(payload_of(result)["scope_violations"][0]["path"], "src/nested/ok.py")

        self.write_plan(write_scope=["src/**/*.py"])
        result = self.run_verify()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    # -- scope matching ------------------------------------------------------

    def test_glob_scope_allows_matching_path(self) -> None:
        self.change("src/ok.py")
        self.write_plan(write_scope=["src/*.py"])
        result = self.run_verify()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = payload_of(result)
        self.assertEqual(payload["verdict"], "pass")
        self.assertEqual(payload["scope_violations"], [])

    def test_glob_scope_flags_non_matching_path(self) -> None:
        self.change("src/notes.txt")
        self.write_plan(write_scope=["src/*.py"])
        result = self.run_verify()
        self.assert_clean_cli(result)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        payload = payload_of(result)
        self.assertEqual(payload["verdict"], "fail")
        self.assertEqual(len(payload["scope_violations"]), 1)
        self.assertEqual(payload["scope_violations"][0]["path"], "src/notes.txt")
        self.assertIn("outside declared write_scope", payload["scope_violations"][0]["reason"])

    def test_literal_directory_scope_allows_nested_path(self) -> None:
        self.change("src/nested/ok.py")
        self.write_plan(write_scope=["src"])
        result = self.run_verify()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(payload_of(result)["verdict"], "pass")

    def test_path_traversal_scope_is_rejected(self) -> None:
        self.change("src/ok.py")
        self.write_plan(write_scope=["../outside/**"])
        result = self.run_verify()
        self.assert_clean_cli(result)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        payload = payload_of(result)
        self.assertEqual(payload["verdict"], "fail")
        self.assertEqual(len(payload["invalid_write_scope"]), 1)
        self.assertIn("path traversal", payload["invalid_write_scope"][0]["reason"])
        self.assertEqual(payload["scope_violations"][0]["path"], "src/ok.py")

    def test_absolute_scopes_are_rejected(self) -> None:
        self.change("src/ok.py")
        for scope in ("/outside/**", "C:/outside/**", "C:\\outside\\**"):
            with self.subTest(scope=scope):
                self.write_plan(write_scope=[scope])
                result = self.run_verify()
                self.assert_clean_cli(result)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                payload = payload_of(result)
                self.assertEqual(payload["verdict"], "fail")
                self.assertTrue(payload["invalid_write_scope"], payload)

    def test_no_write_scope_with_clean_diff_passes(self) -> None:
        self.write_plan(write_scope=[])
        result = self.run_verify()
        self.assert_clean_cli(result)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = payload_of(result)
        self.assertEqual(payload["verdict"], "pass")
        self.assertFalse(payload["write_scope_declared"])
        self.assertEqual(payload["changed_paths"], [])

    def test_no_write_scope_with_changes_fails(self) -> None:
        self.change("src/ok.py")
        self.write_plan(write_scope=[])
        result = self.run_verify()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        payload = payload_of(result)
        self.assertEqual(payload["verdict"], "fail")
        self.assertEqual(payload["scope_violations"][0]["path"], "src/ok.py")
        self.assertIn("no write_scope", payload["scope_violations"][0]["reason"])

    # -- input and repository handling --------------------------------------

    def test_invalid_base_reports_error(self) -> None:
        result = self.run_verify(base="does-not-exist")
        self.assert_clean_cli(result)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        payload = payload_of(result)
        self.assertEqual(payload["verdict"], "error")
        self.assertTrue(
            any("base commit not found" in error for error in payload["errors"]), payload
        )

    def test_malformed_plan_reports_error(self) -> None:
        self.plan.write_text("{not json", encoding="utf-8")
        result = self.run_verify()
        self.assert_clean_cli(result)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        payload = payload_of(result)
        self.assertEqual(payload["verdict"], "error")
        self.assertTrue(any("invalid JSON" in error for error in payload["errors"]), payload)

    def test_invalid_plan_is_rejected_before_commands(self) -> None:
        marker = self.repo / "marker.txt"
        self.write_plan(
            commands=[py_command('from pathlib import Path; Path("marker.txt").write_text("x")')],
            acceptance=[],
        )
        result = self.run_verify()
        self.assert_clean_cli(result)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        payload = payload_of(result)
        self.assertEqual(payload["verdict"], "error")
        self.assertTrue(any("acceptance" in error for error in payload["errors"]), payload)
        self.assertFalse(marker.exists())

    def test_missing_unit_reports_error(self) -> None:
        result = self.run_verify(unit="missing")
        self.assert_clean_cli(result)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        payload = payload_of(result)
        self.assertEqual(payload["verdict"], "error")
        self.assertTrue(any("not found" in error for error in payload["errors"]), payload)

    def test_nonfinite_or_nonpositive_timeout_is_rejected(self) -> None:
        marker = self.repo / "marker.txt"
        self.write_plan(
            commands=[py_command('from pathlib import Path; Path("marker.txt").write_text("x")')],
            write_scope=[],
        )
        for timeout in ("nan", "inf", "-inf", "-1", "0"):
            with self.subTest(timeout=timeout):
                result = self.run_verify(timeout=timeout)
                self.assert_clean_cli(result)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                payload = payload_of(result)
                self.assertEqual(payload["verdict"], "error")
                self.assertTrue(
                    any("--timeout" in error for error in payload["errors"]), payload
                )
                self.assertFalse(marker.exists())

    def test_repo_subdirectory_is_rejected(self) -> None:
        result = self.run_verify(repo=self.repo / "src")
        self.assert_clean_cli(result)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        payload = payload_of(result)
        self.assertEqual(payload["verdict"], "error")
        self.assertTrue(any("top level" in error for error in payload["errors"]), payload)

    def test_symlinked_repo_path_is_resolved(self) -> None:
        self.change("src/ok.py")
        link = self.root / "repo-link"
        try:
            os.symlink(self.repo, link, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlinks are not available: {exc}")
        result = self.run_verify(repo=link)
        self.assert_clean_cli(result)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = payload_of(result)
        self.assertEqual(payload["verdict"], "pass")
        self.assertEqual(
            payload["repo_details"]["resolved_path"], str(self.repo.resolve())
        )

    def test_detached_head_is_supported(self) -> None:
        self.change("src/ok.py")
        git_ok(self.repo, "checkout", "--detach", "HEAD")
        result = self.run_verify()
        self.assert_clean_cli(result)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = payload_of(result)
        self.assertEqual(payload["verdict"], "pass")
        self.assertTrue(payload["git"]["detached"])
        self.assertIsNone(payload["git"]["branch"])

    # -- output bounds and shell opt-in -------------------------------------

    def test_output_is_truncated_per_stream(self) -> None:
        self.write_plan(
            commands=[
                py_command(
                    "import sys; "
                    "sys.stdout.write('x' * 5000); "
                    "sys.stderr.write('y' * 5000)"
                )
            ],
            write_scope=[],
        )
        result = self.run_verify(max_output=100)
        self.assert_clean_cli(result)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        command = payload_of(result)["command_results"][0]
        self.assertEqual(command["status"], "pass")
        self.assertEqual(len(command["stdout"].encode("utf-8")), 100)
        self.assertEqual(len(command["stderr"].encode("utf-8")), 100)
        self.assertEqual(command["stdout_bytes"], 5000)
        self.assertEqual(command["stderr_bytes"], 5000)
        self.assertTrue(command["stdout_truncated"])
        self.assertTrue(command["stderr_truncated"])

    def test_shell_execution_requires_explicit_opt_in(self) -> None:
        marker = self.repo / "shell_marker.txt"
        self.write_plan(
            commands=["echo hello > shell_marker.txt"],
            write_scope=[],
        )
        refused = self.run_verify(shell=False)
        self.assert_clean_cli(refused)
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        refused_payload = payload_of(refused)
        self.assertEqual(refused_payload["verdict"], "fail")
        self.assertEqual(refused_payload["command_results"][0]["status"], "refused")
        self.assertFalse(marker.exists())

        allowed = self.run_verify(shell=True)
        self.assert_clean_cli(allowed)
        self.assertEqual(allowed.returncode, 0, allowed.stdout + allowed.stderr)
        allowed_payload = payload_of(allowed)
        self.assertEqual(allowed_payload["verdict"], "pass")
        self.assertEqual(allowed_payload["command_results"][0]["status"], "pass")
        self.assertTrue(marker.exists())


if __name__ == "__main__":
    unittest.main()
