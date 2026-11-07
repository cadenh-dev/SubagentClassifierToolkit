#!/usr/bin/env python3
"""Inspect a local agent toolchain without changing it.

Usage: python3 scripts/doctor.py [--repo PATH] [--profile PATH] [--json] [--fetch]
Exit codes: 0 when no checks are errors, 1 for a reported error, 2 for invalid input.
Only --fetch updates Git refs; all other checks are read-only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

try:
    import tomllib
except ImportError:
    tomllib = None

RUNTIMES = ["python3", "node", "npm", "pnpm", "yarn", "bun", "go", "rustc", "cargo", "java", "dotnet"]
CLIS = ["git", "gh", "codex", "opencode", "claude", "cursor-agent", "gemini"]


def report(check_id: str, status: str, summary: str, details: dict | None = None) -> dict:
    return {"id": check_id, "status": status, "summary": summary, "details": details or {}}


def run(argv: list[str], cwd: Path | None = None, timeout: float = 10, env: dict | None = None):
    try:
        return subprocess.run(argv, cwd=cwd, env=env, text=True, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        return error


def safe_error(message: str) -> str:
    message = re.sub(r"https?://[^\s]+", "remote URL", message)
    message = re.sub(r"(?i)(token|password|authorization|secret)[=: ]+\S+", r"\1=[redacted]", message)
    return message.strip()[:400]


def strip_jsonc_comments(source: str) -> str:
    result: list[str] = []
    index = 0
    quoted = False
    escaped = False
    while index < len(source):
        char = source[index]
        if quoted:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            index += 1
        elif char == '"':
            quoted = True
            result.append(char)
            index += 1
        elif source.startswith("//", index):
            while index < len(source) and source[index] != "\n":
                index += 1
        elif source.startswith("/*", index):
            end = source.find("*/", index + 2)
            index = len(source) if end < 0 else end + 2
        else:
            result.append(char)
            index += 1
    return "".join(result)


def servers_from(obj: object, allow_top_mcp: bool = False) -> list[str]:
    names: list[str] = []
    if not isinstance(obj, dict):
        return names
    for key in ("mcpServers", "mcp_servers"):
        if isinstance(obj.get(key), dict):
            names.extend(str(name) for name in obj[key])
    if allow_top_mcp and isinstance(obj.get("mcp"), dict):
        names.extend(str(name) for name in obj["mcp"])
    return names


def digest(directory: Path) -> str:
    value = hashlib.sha256()
    for file_path in sorted(directory.rglob("*")):
        if file_path.is_file() and "__pycache__" not in file_path.parts and file_path.suffix != ".pyc":
            value.update(file_path.relative_to(directory).as_posix().encode())
            value.update(file_path.read_bytes())
    return value.hexdigest()


def doctor(repo: Path, skills_dir: Path, profile: Path | None, home: Path, path: str, timeout: float,
           fetch: bool = False, runner: Callable = run, which: Callable = shutil.which) -> list[dict]:
    out = []
    expected: set[str] = set()
    profile_data = {}
    if profile:
        profile_data = json.loads(profile.read_text())
        for language in profile_data.get("languages", []):
            expected.add(language.get("runtime_command") or language.get("name", ""))
        for command in profile_data.get("commands", []):
            try:
                expected.add(shlex.split(command.get("command", ""))[0])
            except (ValueError, IndexError):
                pass
    for command in RUNTIMES + CLIS:
        present = which(command, path=path)
        status = "ok" if present else ("warn" if command in expected else "skip")
        out.append(report("command." + command, status, command + (" present" if present else " not found")))
    gh = which("gh", path=path)
    env = {**os.environ, "PATH": path}
    auth_result = runner([gh, "auth", "status"], timeout=timeout, env=env) if gh else None
    authenticated = bool(auth_result and not isinstance(auth_result, Exception) and auth_result.returncode == 0)
    out.append(report("auth.gh", "ok" if authenticated else ("warn" if gh else "skip"),
                      "gh authenticated" if authenticated else ("gh not authenticated" if gh else "gh not found")))
    git = which("git", path=path)
    if git:
        remotes_result = runner([git, "remote"], cwd=repo, timeout=timeout, env=env)
        remotes = remotes_result.stdout.split() if not isinstance(remotes_result, Exception) and remotes_result.returncode == 0 else []
        for name in remotes:
            url = runner([git, "remote", "get-url", name], cwd=repo, timeout=timeout, env=env)
            if isinstance(url, Exception) or url.returncode:
                continue
            address = url.stdout.strip()
            git_env = {**env, "GIT_TERMINAL_PROMPT": "0", "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o ConnectTimeout=5"}
            if fetch:
                runner([git, "fetch", name], cwd=repo, timeout=timeout, env=git_env)
            result = runner([git, "ls-remote", "--heads", address], cwd=repo, timeout=timeout, env=git_env)
            if isinstance(result, Exception) or result.returncode:
                error = "timeout" if isinstance(result, subprocess.TimeoutExpired) else safe_error(getattr(result, "stderr", "") or str(result))
                details = {"reason": error}
                if ("git@" in address or address.startswith("ssh://")) and authenticated:
                    details["suggestion"] = "Switch this remote to its HTTPS URL; gh is authenticated."
                out.append(report("git.remote." + name, "error", "remote unreachable", details))
            else:
                out.append(report("git.remote." + name, "ok", "remote reachable"))
        upstream = runner([git, "rev-list", "--left-right", "--count", "@{u}...HEAD"], cwd=repo, timeout=timeout, env=env)
        if isinstance(upstream, Exception) or upstream.returncode:
            out.append(report("git.upstream", "skip", "no upstream"))
        else:
            behind, ahead = map(int, upstream.stdout.split())
            out.append(report("git.upstream", "warn" if behind else "ok", "branch is behind upstream" if behind else "branch matches upstream", {"behind": behind, "ahead": ahead}))
    candidates = [(home / ".config/opencode/opencode.json", "opencode", True), (home / ".config/opencode/opencode.jsonc", "opencode", True),
                  (home / ".codex/config.toml", "codex", False), (home / ".claude.json", "claude", False),
                  (repo / ".mcp.json", "claude", False), (home / ".gemini/settings.json", "gemini", False)]
    grouped: dict[str, set[str]] = {}
    for config, label, top_mcp in candidates:
        if not config.is_file():
            continue
        try:
            raw = config.read_text(errors="replace")
            if config.suffix == ".toml":
                if tomllib is None:
                    out.append(report("mcp.config." + label, "skip", "toml parser unavailable"))
                    continue
                names = servers_from(tomllib.loads(raw))
            else:
                names = servers_from(json.loads(strip_jsonc_comments(raw)), top_mcp)
            grouped.setdefault(label, set()).update(names)
        except Exception:
            out.append(report("mcp.config." + label, "warn", "Could not parse " + label + " config"))
    out.append(report("mcp.servers", "ok" if grouped else "skip", "MCP servers discovered" if grouped else "No MCP servers found",
                      {"names": {key: sorted(value) for key, value in grouped.items()}}))
    if profile:
        missing = sorted(command for command in expected if command and not which(command, path=path))
        out.append(report("profile.expected", "warn" if missing else "ok", "Expected commands missing" if missing else "Profile expectations available", {"missing": missing}))
    source = repo / ".opencode/skills"
    if source.is_dir() and skills_dir.is_dir():
        drift = []
        for directory in source.iterdir():
            if directory.is_dir():
                installed = skills_dir / directory.name
                status = "missing" if not installed.is_dir() else ("ok" if digest(directory) == digest(installed) else "stale")
                drift.append({"skill": directory.name, "status": status})
        for directory in skills_dir.iterdir():
            if directory.is_dir() and not (source / directory.name).exists():
                drift.append({"skill": directory.name, "status": "extra"})
        status = "warn" if any(item["status"] in ("missing", "stale") for item in drift) else "ok"
        out.append(report("skills.drift", status, "Installed skill comparison", {"skills": drift}))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--skills-dir", type=Path)
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--fetch", action="store_true")
    args = parser.parse_args(argv)
    home = Path.home()
    path = os.environ.get("PATH", "")
    install = args.skills_dir or home / ".config/opencode/skills"
    if args.timeout <= 0 or not args.repo.is_dir() or (args.profile and not args.profile.is_file()):
        parser.error("invalid repo, profile, or timeout")
    try:
        items = doctor(args.repo.resolve(), install, args.profile, home, path, args.timeout, args.fetch)
    except ValueError as error:
        parser.error(str(error))
    if args.json:
        print(json.dumps(items, indent=2))
    else:
        print(f"{'ID':28} {'STATUS':7} SUMMARY")
        for item in items:
            print(f"{item['id']:28} {item['status']:7} {item['summary']}")
            if item["status"] == "error":
                print("  " + str(item["details"]))
    return 1 if any(item["status"] == "error" for item in items) else 0


if __name__ == "__main__":
    sys.exit(main())
