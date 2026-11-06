#!/usr/bin/env python3
"""Run one delegation unit's declared commands and compare its writes to scope.

This is a deliberately small, dependency-free verification runner. It does not
accept an LLM's summary and it does not treat a green test command as proof that
prose acceptance criteria were met. It answers two narrower, machine-checkable
questions:

  1. Did every command declared by the unit exit 0 within the timeout?
  2. Are all paths changed in ``base..HEAD`` inside the unit's write scope?

The result is a JSON object with the command results, changed paths, scope
violations, duration, and an explicit verdict scope/limitations list. A passing
verdict therefore means "the declared commands passed and the committed diff is
inside the declared write scope", not "the whole unit is accepted".

Usage:
  verify_unit.py --plan plan.json --unit api-auth --repo /work/repo \
      --base 4f2c1a --timeout 600 --allow-commands

  # Shell metacharacters are refused by default. Opt in only for trusted plans:
  verify_unit.py ... --allow-commands --shell

Security
--------
Plan commands are trusted code. With ``--allow-commands`` they execute with the
caller's privileges in the repository working directory. Without that flag this
tool refuses to execute anything and returns a ``blocked`` verdict. Without
``--shell`` each command is parsed as an argv vector and run without a shell;
commands containing unquoted shell operators are refused. The harness never
reads credential files or secret stores; it only reads the plan, git metadata,
and the output produced by the commands it was explicitly allowed to run.

Exit codes:
  0  pass: all declared commands passed and no scope violations were found
  1  fail or unverified: command failure/timeout/refusal, scope violation, or
     the unit declared no commands
  2  error/blocked: bad input, malformed/invalid plan, git/base/repo problem,
     invalid timeout, or commands refused because --allow-commands was omitted
"""

from __future__ import annotations

import argparse
import json
import math
import os
import posixpath
import re
import shlex
import subprocess
import sys
import threading
import time
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

TOOL = "verify_unit"
SCHEMA_VERSION = 1
DEFAULT_TIMEOUT_SECONDS = 300.0
DEFAULT_MAX_OUTPUT_BYTES = 64 * 1024
GIT_TIMEOUT_SECONDS = 30.0
READ_CHUNK_BYTES = 64 * 1024
SHELL_OPERATORS = ";&|<>`\n\r"


class VerifyError(Exception):
    """A user-facing verification error."""


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _round_seconds(value: float) -> float:
    return round(max(0.0, value), 3)


def _positive_finite(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise VerifyError(f"{field} must be a finite positive number")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise VerifyError(f"{field} must be a finite positive number") from None
    if not math.isfinite(number) or number <= 0:
        raise VerifyError(f"{field} must be a finite positive number")
    return number


def _positive_integer(value: Any, field: str) -> int:
    number = _positive_finite(value, field)
    if not number.is_integer():
        raise VerifyError(f"{field} must be a positive whole number")
    return int(number)


def _verdict_scope() -> dict[str, Any]:
    return {
        "checks": ["declared_commands", "write_scope_vs_base_head_diff"],
        "acceptance_prose_verified": False,
        "meaning": (
            "pass means the declared commands exited 0 and committed paths in "
            "base..HEAD are within write_scope; it does not verify acceptance "
            "prose, behavior outside those commands, or uncommitted changes"
        ),
    }


def _base_limitations() -> list[str]:
    return [
        "Acceptance criteria are prose, not machine-executable checks, and are not evaluated.",
        "A pass proves only the declared commands and the committed base..HEAD scope; it does not prove the unit is accepted.",
        "Only tracked committed changes in base..HEAD are compared. Uncommitted and untracked files are not part of the diff.",
        "Commands run with the caller's privileges, inherit the caller's environment, and may modify the working tree; this harness never resets or cleans the repository.",
        "Child commands are not sandboxed and can access anything the caller can. The harness itself never reads credential files or secret stores.",
        "Command stdout/stderr is decoded as UTF-8 with replacement and may contain sensitive data; do not copy raw logs into a shared ledger.",
    ]


def _report(
    *,
    ok: bool,
    verdict: str,
    errors: list[str] | None = None,
    plan: str | None = None,
    unit: str | None = None,
    repo: str | None = None,
    base: str | None = None,
    started: float | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    limitations = _base_limitations()
    report: dict[str, Any] = {
        "tool": TOOL,
        "schema_version": SCHEMA_VERSION,
        "ok": ok,
        "verdict": verdict,
        "errors": errors or [],
        "plan": plan,
        "unit": unit,
        "repo": repo,
        "base": base,
        "verdict_scope": _verdict_scope(),
        "limitations": limitations,
    }
    if started is not None:
        report["duration_seconds"] = _round_seconds(time.monotonic() - started)
    if extra:
        report.update(extra)
    return report


def _emit(report: dict[str, Any], code: int) -> int:
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return code


# ---------------------------------------------------------------------------
# Plan loading and preflight validation
# ---------------------------------------------------------------------------


def _load_plan(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise VerifyError(f"plan file not found: {path}") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise VerifyError(f"could not read plan {path}: {exc}") from None
    try:
        data = json.loads(text)
    except (ValueError, RecursionError) as exc:
        detail = getattr(exc, "msg", None) or str(exc)
        raise VerifyError(f"invalid JSON in plan {path}: {detail}") from None
    if not isinstance(data, dict):
        raise VerifyError("plan must be a JSON object")
    return data


def _run_plan_validator(plan_path: Path) -> dict[str, Any]:
    """Run the sibling validate_plan.py as a preflight.

    Using the shipped validator, rather than a second copy of its rules, keeps
    the harness compatible with the plan contract. The validator is invoked
    without a shell and only reads the plan.
    """

    validator = Path(__file__).resolve().with_name("validate_plan.py")
    if not validator.is_file():
        raise VerifyError(f"plan validator not found beside verify_unit.py: {validator}")
    command = [sys.executable, str(validator), "--plan", str(plan_path), "--json"]
    try:
        proc = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        raise VerifyError("could not start the plan validator: Python interpreter not found") from None
    except subprocess.TimeoutExpired:
        raise VerifyError("plan validator timed out") from None
    except OSError as exc:
        raise VerifyError(f"could not run the plan validator: {exc}") from None

    if proc.returncode not in (0, 1):
        detail = (proc.stderr or proc.stdout or "").strip()
        raise VerifyError(f"plan validator failed with exit {proc.returncode}: {detail}")
    try:
        payload = json.loads(proc.stdout)
    except ValueError:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise VerifyError(f"plan validator returned non-JSON output: {detail}") from None
    if not isinstance(payload, dict) or "ok" not in payload:
        raise VerifyError("plan validator returned an unexpected result")
    if not payload.get("ok"):
        errors = payload.get("errors")
        if not isinstance(errors, list):
            errors = ["plan validation failed"]
        raise VerifyError("plan is invalid: " + "; ".join(str(error) for error in errors))
    return payload


def _select_unit(plan: dict[str, Any], unit_id: str) -> dict[str, Any]:
    units = plan.get("units")
    if not isinstance(units, list):
        raise VerifyError("plan units must be an array")
    matches = [
        unit
        for unit in units
        if isinstance(unit, dict) and unit.get("id") == unit_id
    ]
    if not matches:
        known = ", ".join(
            str(unit.get("id"))
            for unit in units
            if isinstance(unit, dict) and isinstance(unit.get("id"), str)
        )
        raise VerifyError(f"unit {unit_id!r} not found in plan; known units: {known or '(none)'}")
    if len(matches) > 1:
        raise VerifyError(f"unit id {unit_id!r} appears more than once in the plan")
    return matches[0]


def _selected_unit_errors(unit: dict[str, Any]) -> list[str]:
    """Strict checks for the selected unit, independent of the full-plan pass."""

    errors: list[str] = []
    acceptance = unit.get("acceptance")
    if not isinstance(acceptance, list) or not acceptance:
        errors.append("selected unit acceptance must contain at least one criterion")
    elif any(not isinstance(item, str) or not item.strip() for item in acceptance):
        errors.append("selected unit acceptance entries must be non-empty strings")

    write_scope = unit.get("write_scope", [])
    if not isinstance(write_scope, list):
        errors.append("selected unit write_scope must be an array")
    elif any(not isinstance(scope, str) for scope in write_scope):
        errors.append("selected unit write_scope entries must be strings")

    commands = unit.get("commands", [])
    if not isinstance(commands, list):
        errors.append("selected unit commands must be an array")
    else:
        for index, command in enumerate(commands):
            if not isinstance(command, str):
                errors.append(f"selected unit commands[{index}] must be a string")
            elif not command.strip():
                errors.append(f"selected unit commands[{index}] must not be empty")
            elif "\x00" in command:
                errors.append(f"selected unit commands[{index}] must not contain a NUL byte")

    return errors


# ---------------------------------------------------------------------------
# Git and path/scope handling
# ---------------------------------------------------------------------------


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return env


def _git(
    repo: Path,
    args: list[str],
    *,
    timeout: float = GIT_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[bytes]:
    command = ["git", "-C", str(repo), *args]
    try:
        return subprocess.run(
            command,
            cwd=str(repo),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
            env=_git_env(),
        )
    except FileNotFoundError:
        raise VerifyError("git executable not found") from None
    except subprocess.TimeoutExpired:
        raise VerifyError(f"git {' '.join(args)} timed out after {timeout:g}s") from None
    except OSError as exc:
        raise VerifyError(f"could not run git: {exc}") from None


def _git_text(repo: Path, args: list[str], *, timeout: float = GIT_TIMEOUT_SECONDS) -> str:
    proc = _git(repo, args, timeout=timeout)
    if proc.returncode != 0:
        detail = _decode(proc.stderr).strip() or f"exit {proc.returncode}"
        raise VerifyError(f"git {' '.join(args)} failed: {detail}")
    return _decode(proc.stdout).strip()


def _git_optional(repo: Path, args: list[str]) -> subprocess.CompletedProcess[bytes] | None:
    try:
        return _git(repo, args)
    except VerifyError:
        return None


def _same_path(left: Path, right: Path) -> bool:
    try:
        return os.path.samefile(left, right)
    except OSError:
        return os.path.normcase(os.path.normpath(str(left))) == os.path.normcase(
            os.path.normpath(str(right))
        )


def _normalize_scope(scope: str) -> tuple[str | None, str | None]:
    """Normalize a write-scope entry, rejecting escapes and absolutes.

    The plan validator normalizes with posixpath for overlap checks. This
    matcher is stricter for safety: an absolute path, a drive path, or a ``..``
    segment is reported as an invalid scope instead of being coerced into a
    repository-relative path.
    """

    if not isinstance(scope, str):
        return None, "scope must be a string"
    text = scope.strip().replace("\\", "/")
    if not text:
        return None, "scope is empty"
    if "\x00" in text:
        return None, "scope contains a NUL byte"
    if text.startswith("/"):
        return None, "absolute paths are not allowed"
    if re.match(r"^[A-Za-z]:", text):
        return None, "Windows drive paths are not allowed"
    parts = text.split("/")
    if any(part == ".." for part in parts):
        return None, "path traversal ('..') is not allowed"
    normalized = posixpath.normpath(text)
    if normalized in ("", ".", "..") or normalized.startswith("../"):
        return None, "scope resolves outside the repository root"
    return normalized.strip("/"), None


def _normalize_changed_path(path: str) -> tuple[str | None, str | None]:
    """Validate a path reported by git diff. Never resolves it on disk."""

    if not isinstance(path, str) or not path:
        return None, "changed path is empty"
    if "\x00" in path:
        return None, "changed path contains a NUL byte"
    if path.startswith("/"):
        return None, "changed path is absolute"
    if re.match(r"^[A-Za-z]:", path):
        return None, "changed path is a Windows drive path"
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None, "changed path contains an empty, '.' or '..' segment"
    return path, None


def _scope_has_glob(scope: str) -> bool:
    return any(char in scope for char in "*?[")


def _literal_path_under(scope: str, path: str) -> bool:
    if os.name == "nt":
        scope_cmp = scope.casefold()
        path_cmp = path.casefold()
    else:
        scope_cmp = scope
        path_cmp = path
    return path_cmp == scope_cmp or path_cmp.startswith(scope_cmp + "/")


def _path_matches_scope(path: str, scope: str) -> bool:
    """Match a changed path against one normalized scope.

    Literal scopes name a file or a directory subtree. Glob scopes use Python's
    ``fnmatch``, the same primitive ``validate_plan.py`` uses for overlap checks;
    this keeps the harness compatible with the plan contract. A path that does
    not match is reported as out of scope rather than guessed into scope.
    """

    if _scope_has_glob(scope):
        return fnmatch(path, scope)
    return _literal_path_under(scope, path)


def _changed_paths(repo: Path, base_sha: str, head_sha: str) -> list[str]:
    proc = _git(
        repo,
        [
            "diff",
            "--name-only",
            "-z",
            "--no-renames",
            "--no-ext-diff",
            "--no-textconv",
            base_sha,
            head_sha,
            "--",
        ],
    )
    if proc.returncode != 0:
        detail = _decode(proc.stderr).strip() or f"exit {proc.returncode}"
        raise VerifyError(f"git diff {base_sha}..{head_sha} failed: {detail}")
    raw = _decode(proc.stdout)
    return [path for path in raw.split("\x00") if path]


# ---------------------------------------------------------------------------
# Command execution
# ---------------------------------------------------------------------------


def _has_unquoted_shell_operator(command: str) -> bool:
    """Detect shell syntax outside single/double quotes without executing it."""

    in_single = False
    in_double = False
    escaped = False
    for char in command:
        if escaped:
            escaped = False
            continue
        if char == "\\" and not in_single:
            escaped = True
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            continue
        if not in_single and not in_double and char in SHELL_OPERATORS:
            return True
    return False


def _reader(stream: Any, limit: int, holder: dict[str, Any]) -> None:
    data = bytearray()
    total = 0
    try:
        while True:
            chunk = stream.read(READ_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if len(data) < limit:
                data.extend(chunk[: limit - len(data)])
    except Exception:
        # A reader must never turn a verification result into a traceback.
        pass
    finally:
        holder["data"] = bytes(data)
        holder["total"] = total
        holder["truncated"] = total > len(data)
        holder["complete"] = True


def _not_run_result(index: int, command: str) -> dict[str, Any]:
    return {
        "index": index,
        "command": command,
        "status": "not_run",
        "execution": None,
        "exit_code": None,
        "timed_out": False,
        "duration_seconds": None,
        "stdout": "",
        "stderr": "",
        "stdout_bytes": 0,
        "stderr_bytes": 0,
        "stdout_truncated": False,
        "stderr_truncated": False,
        "capture_complete": True,
    }


def _execute_command(
    index: int,
    command: str,
    *,
    repo: Path,
    timeout: float,
    max_output_bytes: int,
    use_shell: bool,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "index": index,
        "command": command,
        "status": "spawn_error",
        "execution": "shell" if use_shell else "argv",
        "exit_code": None,
        "timed_out": False,
        "duration_seconds": None,
        "stdout": "",
        "stderr": "",
        "stdout_bytes": 0,
        "stderr_bytes": 0,
        "stdout_truncated": False,
        "stderr_truncated": False,
        "capture_complete": True,
        "output_limit_bytes": max_output_bytes,
    }

    if use_shell:
        popen_target: Any = command
    else:
        if _has_unquoted_shell_operator(command):
            result["status"] = "refused"
            result["reason"] = (
                "command contains shell syntax; rerun with --shell only if the "
                "plan is trusted"
            )
            return result
        try:
            argv = shlex.split(command, posix=True)
        except ValueError as exc:
            result["status"] = "invalid_command"
            result["reason"] = f"could not parse command as argv: {exc}"
            return result
        if not argv:
            result["status"] = "invalid_command"
            result["reason"] = "command parsed to an empty argv vector"
            return result
        result["argv"] = argv
        popen_target = argv

    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            popen_target,
            cwd=str(repo),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=use_shell,
        )
    except FileNotFoundError:
        result["status"] = "spawn_error"
        result["reason"] = "executable not found"
        result["duration_seconds"] = _round_seconds(time.monotonic() - started)
        return result
    except OSError as exc:
        result["status"] = "spawn_error"
        result["reason"] = str(exc)
        result["duration_seconds"] = _round_seconds(time.monotonic() - started)
        return result

    assert proc.stdout is not None and proc.stderr is not None
    stdout_holder: dict[str, Any] = {"data": b"", "total": 0, "truncated": False, "complete": False}
    stderr_holder: dict[str, Any] = {"data": b"", "total": 0, "truncated": False, "complete": False}
    readers = [
        threading.Thread(target=_reader, args=(proc.stdout, max_output_bytes, stdout_holder), daemon=True),
        threading.Thread(target=_reader, args=(proc.stderr, max_output_bytes, stderr_holder), daemon=True),
    ]
    for reader in readers:
        reader.start()

    timed_out = False
    try:
        exit_code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            proc.kill()
        except OSError:
            pass
        try:
            exit_code = proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            exit_code = None

    for reader in readers:
        reader.join(timeout=5)
    capture_complete = not any(reader.is_alive() for reader in readers)
    if not capture_complete:
        for stream in (proc.stdout, proc.stderr):
            try:
                stream.close()
            except OSError:
                pass
        for reader in readers:
            reader.join(timeout=1)
        capture_complete = not any(reader.is_alive() for reader in readers)

    result["exit_code"] = exit_code
    result["timed_out"] = timed_out
    result["duration_seconds"] = _round_seconds(time.monotonic() - started)
    result["stdout"] = _decode(stdout_holder["data"])
    result["stderr"] = _decode(stderr_holder["data"])
    result["stdout_bytes"] = stdout_holder["total"]
    result["stderr_bytes"] = stderr_holder["total"]
    result["stdout_truncated"] = bool(stdout_holder["truncated"])
    result["stderr_truncated"] = bool(stderr_holder["truncated"])
    result["capture_complete"] = capture_complete
    if timed_out:
        result["status"] = "timeout"
        result["reason"] = f"command exceeded the {timeout:g}s timeout and was killed"
    elif exit_code == 0:
        result["status"] = "pass"
    else:
        result["status"] = "fail"
        result["reason"] = f"command exited with {exit_code}"
    return result


# ---------------------------------------------------------------------------
# Main verification flow
# ---------------------------------------------------------------------------


def _verify(args: argparse.Namespace, started: float) -> tuple[dict[str, Any], int]:
    plan_path = Path(args.plan).expanduser()
    timeout = _positive_finite(args.timeout, "--timeout")
    max_output_bytes = _positive_integer(args.max_output_bytes, "--max-output-bytes")

    plan = _load_plan(plan_path)
    plan_validation = _run_plan_validator(plan_path)
    unit = _select_unit(plan, args.unit)
    unit_errors = _selected_unit_errors(unit)
    if unit_errors:
        raise VerifyError("selected unit is invalid: " + "; ".join(unit_errors))

    # Resolve the repository before trusting it as a working directory. A
    # symlinked path is resolved; the resolved path must be the git top level,
    # not a subdirectory, so diff paths and command cwd refer to the same tree.
    requested_repo = Path(args.repo).expanduser()
    try:
        repo_path = requested_repo.resolve(strict=True)
    except FileNotFoundError:
        raise VerifyError(f"repository path not found: {args.repo}") from None
    except OSError as exc:
        raise VerifyError(f"could not resolve repository path {args.repo}: {exc}") from None
    if not repo_path.is_dir():
        raise VerifyError(f"repository path is not a directory: {args.repo}")

    top_level_text = _git_text(repo_path, ["rev-parse", "--show-toplevel"])
    try:
        top_level = Path(top_level_text).resolve(strict=True)
    except OSError as exc:
        raise VerifyError(f"could not resolve git top level {top_level_text!r}: {exc}") from None
    if not _same_path(top_level, repo_path):
        raise VerifyError(
            f"repo must be the git worktree top level; {repo_path} resolves to {top_level}"
        )

    head_proc = _git(repo_path, ["rev-parse", "--verify", "--quiet", "HEAD"])
    if head_proc.returncode != 0:
        raise VerifyError("HEAD is missing; cannot compare base..HEAD")
    head_sha = _decode(head_proc.stdout).strip()
    if not head_sha:
        raise VerifyError("HEAD is missing; cannot compare base..HEAD")

    base_ref = args.base
    if not isinstance(base_ref, str) or not base_ref.strip():
        raise VerifyError("base commit is empty")
    if base_ref.startswith("-") or "\x00" in base_ref:
        raise VerifyError(f"base commit is not a valid revision: {base_ref!r}")
    base_proc = _git(
        repo_path,
        ["rev-parse", "--verify", "--quiet", f"{base_ref}^{{commit}}"],
    )
    if base_proc.returncode != 0:
        raise VerifyError(f"base commit not found: {base_ref!r}")
    base_sha = _decode(base_proc.stdout).strip()
    if not base_sha:
        raise VerifyError(f"base commit not found: {base_ref!r}")

    branch_proc = _git(repo_path, ["symbolic-ref", "--short", "-q", "HEAD"])
    branch = _decode(branch_proc.stdout).strip() if branch_proc.returncode == 0 else None
    detached = branch is None

    ancestor_proc = _git_optional(repo_path, ["merge-base", "--is-ancestor", base_sha, head_sha])
    base_is_ancestor: bool | None
    if ancestor_proc is None:
        base_is_ancestor = None
    elif ancestor_proc.returncode == 0:
        base_is_ancestor = True
    elif ancestor_proc.returncode == 1:
        base_is_ancestor = False
    else:
        base_is_ancestor = None

    # Working-tree status is read-only and only used to state a limitation. The
    # diff below intentionally covers committed base..HEAD only.
    dirty_proc = _git_optional(
        repo_path,
        ["diff", "--quiet", "--no-ext-diff", "--no-textconv", "HEAD", "--"],
    )
    if dirty_proc is None or dirty_proc.returncode not in (0, 1):
        working_tree_dirty = None
    else:
        working_tree_dirty = dirty_proc.returncode == 1
    untracked_proc = _git_optional(repo_path, ["ls-files", "--others", "--exclude-standard", "-z"])
    if untracked_proc is None or untracked_proc.returncode != 0:
        untracked_before = None
    else:
        untracked_before = bool(
            [path for path in _decode(untracked_proc.stdout).split("\x00") if path]
        )

    changed_paths = _changed_paths(repo_path, base_sha, head_sha)

    raw_write_scope = unit.get("write_scope", [])
    if not isinstance(raw_write_scope, list):
        raw_write_scope = []
    normalized_scopes: list[tuple[str, str]] = []
    invalid_write_scope: list[dict[str, str]] = []
    for raw_scope in raw_write_scope:
        normalized, error = _normalize_scope(raw_scope)
        if error is not None:
            invalid_write_scope.append({"scope": str(raw_scope), "reason": error})
        else:
            assert normalized is not None
            normalized_scopes.append((raw_scope, normalized))

    in_scope_paths: list[dict[str, str]] = []
    scope_violations: list[dict[str, str]] = []
    path_errors: list[dict[str, str]] = []
    for path in changed_paths:
        normalized_path, error = _normalize_changed_path(path)
        if error is not None:
            path_errors.append({"path": path, "reason": error})
            continue
        assert normalized_path is not None
        matched_scope = next(
            (
                raw_scope
                for raw_scope, normalized in normalized_scopes
                if _path_matches_scope(normalized_path, normalized)
            ),
            None,
        )
        if matched_scope is None:
            scope_violations.append(
                {
                    "path": path,
                    "reason": "outside declared write_scope"
                    if raw_write_scope
                    else "no write_scope declared",
                }
            )
        else:
            in_scope_paths.append({"path": path, "matched_scope": matched_scope})

    scope_clean = not scope_violations and not invalid_write_scope and not path_errors
    declared_commands = unit.get("commands", [])
    if not isinstance(declared_commands, list):
        declared_commands = []

    command_results: list[dict[str, Any]] = []
    blocked = bool(declared_commands) and not args.allow_commands
    if blocked:
        command_results = [
            _not_run_result(index, command)
            for index, command in enumerate(declared_commands)
        ]
    else:
        for index, command in enumerate(declared_commands):
            if blocked:
                continue
            command_results.append(
                _execute_command(
                    index,
                    command,
                    repo=repo_path,
                    timeout=timeout,
                    max_output_bytes=max_output_bytes,
                    use_shell=bool(args.shell),
                )
            )

    command_failed = any(item.get("status") != "pass" for item in command_results)
    limitations = _base_limitations()
    if not declared_commands:
        limitations.append(
            "The unit declared no commands; command verification was not performed and the verdict is unverified."
        )
    if not raw_write_scope:
        limitations.append(
            "The unit declared no write_scope; any changed committed path is reported as out of scope."
        )
    if working_tree_dirty:
        limitations.append(
            "The working tree had uncommitted tracked changes before verification. They are not included in the base..HEAD scope check."
        )
    if untracked_before:
        limitations.append(
            "Untracked files existed before verification. They are not included in the base..HEAD scope check."
        )
    if base_is_ancestor is False:
        limitations.append(
            "The base commit is not an ancestor of HEAD; the base..HEAD diff is between two divergent endpoints."
        )
    if args.shell:
        limitations.append(
            "Commands ran through the platform shell (--shell). Plan commands are trusted code and execute with the caller's privileges."
        )
    else:
        limitations.append(
            "Commands were parsed as argv and executed without a shell. Shell operators are refused unless --shell is passed."
        )
    if blocked:
        limitations.append(
            "Commands were not run because --allow-commands was not provided; this is a refusal, not a test failure."
        )
    limitations.append(
        f"Captured stdout and stderr are each limited to {max_output_bytes} bytes; truncation flags are per stream."
    )

    if blocked:
        verdict = "blocked"
        exit_code = 2
        errors: list[str] = ["refused to execute commands without --allow-commands"]
    elif command_failed:
        verdict = "fail"
        exit_code = 1
        errors = [
            f"command {item.get('index')} {item.get('status')}: {item.get('reason', '')}".strip()
            for item in command_results
            if item.get("status") != "pass"
        ]
        if not scope_clean:
            errors.append(
                "write scope check failed: "
                f"{len(scope_violations)} violation(s), "
                f"{len(invalid_write_scope)} invalid scope(s), "
                f"{len(path_errors)} path error(s)"
            )
    elif not scope_clean:
        verdict = "fail"
        exit_code = 1
        errors = [
            "write scope check failed: "
            f"{len(scope_violations)} violation(s), "
            f"{len(invalid_write_scope)} invalid scope(s), "
            f"{len(path_errors)} path error(s)"
        ]
    elif not declared_commands:
        verdict = "unverified"
        exit_code = 1
        errors = ["unit declared no commands; command verification was not performed"]
    else:
        verdict = "pass"
        exit_code = 0
        errors = []

    report = _report(
        ok=verdict == "pass",
        verdict=verdict,
        errors=errors,
        plan=str(plan_path),
        unit=args.unit,
        repo=str(repo_path),
        base=base_ref,
        started=started,
        extra={
            "plan_validation": plan_validation,
            "unit_details": {
                "id": unit.get("id"),
                "title": unit.get("title"),
                "role": unit.get("role"),
                "isolation": unit.get("isolation"),
                "mode": unit.get("mode"),
                "acceptance": unit.get("acceptance", []),
                "acceptance_evaluated": False,
            },
            "repo_details": {
                "requested_path": str(requested_repo),
                "resolved_path": str(repo_path),
                "top_level": str(top_level),
            },
            "git": {
                "base_ref": base_ref,
                "base_commit": base_sha,
                "head_commit": head_sha,
                "branch": branch,
                "detached": detached,
                "base_is_ancestor": base_is_ancestor,
                "range": f"{base_sha}..{head_sha}",
                "working_tree_dirty_before": working_tree_dirty,
                "untracked_before": untracked_before,
            },
            "changed_paths": changed_paths,
            "write_scope": raw_write_scope,
            "write_scope_declared": bool(raw_write_scope),
            "scope": {
                "clean": scope_clean,
                "in_scope_paths": in_scope_paths,
                "violations": scope_violations,
                "invalid_write_scope": invalid_write_scope,
                "path_errors": path_errors,
            },
            "scope_violations": scope_violations,
            "invalid_write_scope": invalid_write_scope,
            "path_errors": path_errors,
            "command_results": command_results,
            "commands_allowed": bool(args.allow_commands),
            "shell": bool(args.shell),
            "timeout_seconds": timeout,
            "max_output_bytes": max_output_bytes,
            "limitations": limitations,
        },
    )
    return report, exit_code


def main(argv: list[str] | None = None) -> int:
    started = time.monotonic()
    parser = argparse.ArgumentParser(
        description="Verify one delegation unit's declared commands and write scope.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--plan", required=True, help="Path to the delegation plan JSON.")
    parser.add_argument("--unit", required=True, help="Unit id to verify.")
    parser.add_argument("--repo", required=True, help="Git worktree top-level path.")
    parser.add_argument("--base", required=True, help="Base commit-ish for the diff.")
    parser.add_argument(
        "--timeout",
        default=str(DEFAULT_TIMEOUT_SECONDS),
        help=f"Per-command timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS:g}).",
    )
    parser.add_argument(
        "--allow-commands",
        action="store_true",
        help="Allow plan commands (trusted code) to execute with the caller's privileges.",
    )
    parser.add_argument(
        "--shell",
        action="store_true",
        help="Run commands through the platform shell instead of parsing argv. Requires --allow-commands.",
    )
    parser.add_argument(
        "--max-output-bytes",
        default=str(DEFAULT_MAX_OUTPUT_BYTES),
        help=f"Maximum captured bytes per stdout/stderr stream (default: {DEFAULT_MAX_OUTPUT_BYTES}).",
    )
    args = parser.parse_args(argv)

    try:
        report, exit_code = _verify(args, started)
    except VerifyError as exc:
        report = _report(
            ok=False,
            verdict="error",
            errors=[str(exc)],
            plan=args.plan,
            unit=args.unit,
            repo=args.repo,
            base=args.base,
            started=started,
        )
        exit_code = 2
    except Exception as exc:  # Keep the CLI machine-readable instead of dumping a traceback.
        report = _report(
            ok=False,
            verdict="error",
            errors=[f"internal error: {type(exc).__name__}: {exc}"],
            plan=args.plan,
            unit=args.unit,
            repo=args.repo,
            base=args.base,
            started=started,
        )
        exit_code = 2
    return _emit(report, exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
