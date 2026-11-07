#!/usr/bin/env python3
"""Inspect Git worktrees, branches, and GitHub PRs; clean only after confirmation.

Scan is read-only. Apply rechecks repository state and uses only ordinary Git
removal commands. All subprocesses have bounded timeouts.

Exit codes: 0 success, 1 refused/failed cleanup, 2 invalid input or scan error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TIMEOUT = 20


class JanitorError(Exception):
    """An actionable repository or command error."""


def command(argv: list[str], cwd: Path, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(argv, cwd=cwd, text=True, capture_output=True,
                                timeout=TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise JanitorError(f"command failed or timed out: {argv[0]}") from exc
    if check and result.returncode:
        raise JanitorError(result.stderr.strip() or f"command failed: {' '.join(argv)}")
    return result


def git(repo: Path, *args: str, check: bool = True) -> str:
    return command(["git", "-C", str(repo), *args], repo, check=check).stdout.strip()


def git_ok(repo: Path, *args: str) -> bool:
    return command(["git", "-C", str(repo), *args], repo, check=False).returncode == 0


def finding(kind: str, identity: str, reasons: list[str], safe: bool,
            action: str, **fields: Any) -> dict[str, Any]:
    prefix = {"worktree": "wt", "branch": "br", "pr": "pr", "skip": "skip"}[kind]
    return {"id": f"{prefix}:{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:12]}", "kind": kind, **fields,
            "reasons": sorted(set(reasons)), "safe_to_remove": safe,
            "suggested_action": action}


def finding_id(kind: str, identity: str) -> str:
    prefix = {"worktree": "wt", "branch": "br", "pr": "pr", "skip": "skip"}[kind]
    return f"{prefix}:{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:12]}"


def resolve_base(repo: Path, requested: str | None) -> str:
    if requested:
        result = command(["git", "-C", str(repo), "rev-parse", "--verify", f"{requested}^{{commit}}"], repo, check=False)
        if result.returncode:
            raise JanitorError(f"base ref not found or not a commit: {requested}")
        return result.stdout.strip()
    symbolic = git(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD", check=False)
    candidates = [symbolic, "main", "master"]
    for ref in candidates:
        if ref and git_ok(repo, "rev-parse", "--verify", f"{ref}^{{commit}}"):
            return git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")
    raise JanitorError("could not resolve base: pass --base, or provide origin/HEAD, local main, or local master")


def base_branch_name(repo: Path, requested: str | None) -> str | None:
    ref = requested
    if not ref:
        ref = git(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD", check=False)
        if not ref:
            for candidate in ("main", "master"):
                if git_ok(repo, "rev-parse", "--verify", f"{candidate}^{{commit}}"):
                    return candidate
    if not ref or ref.startswith("-"):
        return None
    if git_ok(repo, "rev-parse", "--verify", f"refs/heads/{ref}^{{commit}}"):
        return ref
    return ref.rsplit("/", 1)[-1]


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    return git_ok(repo, "merge-base", "--is-ancestor", ancestor, descendant)


def unique_commits(repo: Path, branch: str, base: str) -> bool:
    result = command(["git", "-C", str(repo), "rev-list", "--count", f"{base}..{branch}"], repo, check=False)
    return result.returncode != 0 or result.stdout.strip() not in ("", "0")


def parse_worktrees(repo: Path) -> list[dict[str, Any]]:
    raw = git(repo, "worktree", "list", "--porcelain")
    blocks = [block for block in raw.split("\n\n") if block.strip()]
    parsed = []
    for block in blocks:
        lines = block.splitlines()
        path = Path(lines[0][9:])
        parsed.append({"path": path, "lines": lines,
                       "branch": next((line[7:] for line in lines if line.startswith("branch ")), None),
                       "detached": "detached" in lines,
                       "locked": any(line == "locked" or line.startswith("locked ") for line in lines),
                       "prunable": any(line == "prunable" or line.startswith("prunable ") for line in lines)})
    return parsed


def github_remote(repo: Path) -> bool:
    names = git(repo, "remote", check=False).splitlines()
    for name in names:
        for key in ("remote." + name + ".url", "remote." + name + ".pushurl"):
            urls = git(repo, "config", "--get-all", key, check=False).splitlines()
            for url in urls:
                if "github.com:" in url.lower() or "github.com/" in url.lower():
                    return True
    return False


def scan(repo_arg: Path, base_arg: str | None, stale_days: int,
         gh_executable: str | None = None) -> list[dict[str, Any]]:
    repo = Path(git(repo_arg, "rev-parse", "--show-toplevel")).resolve()
    base = resolve_base(repo, base_arg)
    base_name = base_branch_name(repo, base_arg)
    now = datetime.now(timezone.utc).timestamp()
    items: list[dict[str, Any]] = []
    worktrees = parse_worktrees(repo)
    main_path = worktrees[0]["path"].resolve()
    current_path = Path(git(repo_arg, "rev-parse", "--show-toplevel")).resolve()

    for entry in worktrees:
        path: Path = entry["path"]
        reasons: list[str] = []
        exists = path.is_dir()
        if not exists:
            reasons.extend(["missing directory", "prunable"])
        if entry["prunable"] and "prunable" not in reasons:
            reasons.append("prunable")
        if entry["detached"]:
            reasons.append("detached")
        if entry["locked"]:
            reasons.append("locked")
        dirty = exists and bool(git(path, "status", "--porcelain"))
        if dirty:
            reasons.append("dirty")
        branch = entry["branch"]
        commit = git(path, "rev-parse", "HEAD", check=False) if exists else ""
        if branch and path.resolve() != main_path and git_ok(repo, "merge-base", "--is-ancestor", branch, base):
            reasons.append("branch merged into base")
        elif commit and is_ancestor(repo, commit, base):
            reasons.append("worktree commit merged into base")
        if exists:
            stamp = git(path, "log", "-1", "--format=%ct", check=False)
            if stamp and stamp.isdigit() and now - int(stamp) >= stale_days * 86400:
                reasons.append(f"no commits in {stale_days} days")
        if reasons:
            main = path.resolve() == main_path
            current = path.resolve() == current_path
            has_unique = (unique_commits(repo, branch, base) if branch else
                          bool(commit and not is_ancestor(repo, commit, base)))
            safe = not main and not current and not dirty and not entry["locked"] and not has_unique
            action = f"git worktree remove {path}" if exists else "git worktree prune"
            items.append(finding("worktree", str(path), reasons, safe, action, path=str(path)))

    current_branch = git(repo_arg, "branch", "--show-current")
    branches = git(repo, "for-each-ref", "--format=%(refname:short)%09%(upstream:track)%09%(upstream)", "refs/heads").splitlines()
    for line in branches:
        parts = (line.split("\t") + ["", ""])[:3]
        name, tracking, upstream = parts
        if not name or name == current_branch:
            continue
        if name == base_name:
            continue
        reasons = []
        merged = is_ancestor(repo, name, base)
        if merged:
            reasons.append("merged into base")
        if upstream and "[gone]" in tracking:
            reasons.append("upstream gone")
        no_unique_no_upstream = not upstream and not unique_commits(repo, name, base)
        if no_unique_no_upstream:
            reasons.append("no upstream and no unique commits")
        if unique_commits(repo, name, base):
            reasons.append("unique commits not in base")
        if reasons:
            safe = merged or ("upstream gone" in reasons and not unique_commits(repo, name, base)) or no_unique_no_upstream
            items.append(finding("branch", name, reasons, safe, f"git branch -d {name}", branch=name))

    gh = gh_executable or shutil.which("gh")
    if not gh:
        items.append(finding("skip", "prs", ["gh is not available; PR scan skipped"], False, "install/authenticate gh to scan PRs"))
    elif not github_remote(repo):
        items.append(finding("skip", "prs", ["no GitHub remote; PR scan skipped"], False, "add a GitHub remote to scan PRs"))
    else:
        auth = command([gh, "auth", "status"], repo, check=False)
        if auth.returncode:
            items.append(finding("skip", "prs", ["gh is not authenticated; PR scan skipped"], False, "run gh auth login to scan PRs"))
        else:
            result = command([gh, "pr", "list", "--state", "all", "--limit", "200", "--json",
                              "number,state,headRefName,headRefOid,updatedAt,mergedAt"], repo)
            try:
                prs = json.loads(result.stdout)
                if not isinstance(prs, list):
                    raise ValueError("expected JSON array")
            except ValueError as exc:
                raise JanitorError(f"gh returned invalid PR JSON: {exc}") from None
            for pr in prs:
                state, head, number = pr.get("state"), pr.get("headRefName", ""), str(pr.get("number", ""))
                reasons = []
                updated_text = pr.get("updatedAt")
                if state == "OPEN" and updated_text:
                    updated = datetime.fromisoformat(updated_text.replace("Z", "+00:00")).timestamp()
                    if now - updated >= stale_days * 86400:
                        reasons.append("no activity")
                head_oid = pr.get("headRefOid")
                if state == "OPEN" and head_oid and git_ok(repo, "cat-file", "-e", f"{head_oid}^{{commit}}") and is_ancestor(repo, head_oid, base):
                    reasons.append("head branch merged into base")
                exists = git_ok(repo, "show-ref", "--verify", f"refs/heads/{head}")
                if state == "CLOSED" and not pr.get("mergedAt") and exists:
                    reasons.append("closed unmerged; local branch exists")
                if reasons:
                    items.append(finding("pr", number, reasons, False,
                        f"human review: gh pr view {number} (no PR changes are made)", pr=number))
    return sorted(items, key=lambda item: item["id"])


def clean(repo_arg: Path, findings: list[dict[str, Any]], ids: list[str], apply: bool,
          base_arg: str | None) -> int:
    by_id = {item["id"]: item for item in findings}
    unknown = sorted(set(ids) - by_id.keys())
    if unknown:
        branch_names = git(repo_arg, "for-each-ref", "--format=%(refname:short)", "refs/heads").splitlines()
        current = git(repo_arg, "branch", "--show-current")
        refused = [name for name in branch_names if name == current and finding_id("branch", name) in unknown]
        if refused:
            print("refused: current branch: " + ", ".join(refused))
            return 1
        raise ValueError("unknown finding id(s): " + ", ".join(unknown))
    repo = Path(git(repo_arg, "rev-parse", "--show-toplevel")).resolve()
    base = resolve_base(repo, base_arg)
    failed = False
    for ident in ids:
        item = by_id[ident]
        if not apply:
            print(f"would: {item['suggested_action']} [{ident}]")
            continue
        if item["kind"] in ("pr", "skip"):
            print(f"manual only: {item['suggested_action']}")
            continue
        try:
            if item["kind"] == "worktree":
                path = Path(item["path"])
                entries = parse_worktrees(repo)
                entry = next((entry for entry in entries if str(entry["path"]) == str(path)), None)
                main_path = entries[0]["path"].resolve() if entries else repo
                current_path = Path(git(repo_arg, "rev-parse", "--show-toplevel")).resolve()
                if path.resolve() == main_path or path.resolve() == current_path:
                    raise JanitorError("refusing current or main worktree")
                if entry and entry["locked"]:
                    raise JanitorError("worktree is locked")
                branch = entry["branch"] if entry else None
                if branch and unique_commits(repo, branch, base):
                    raise JanitorError("worktree has commits not in base")
                if path.exists():
                    if git(path, "status", "--porcelain"):
                        raise JanitorError("worktree is dirty")
                    commit = git(path, "rev-parse", "HEAD")
                    if (branch and unique_commits(repo, branch, base)) or (not branch and not is_ancestor(repo, commit, base)):
                        raise JanitorError("worktree has commits not in base")
                    git(repo, "worktree", "remove", str(path))
                else:
                    git(repo, "worktree", "prune")
                print(f"removed {ident}")
            elif item["kind"] == "branch":
                branch = item["branch"]
                if branch == git(repo_arg, "branch", "--show-current"):
                    raise JanitorError("refusing current branch")
                if git_ok(repo, "show-ref", "--verify", f"refs/heads/{branch}") is False:
                    raise JanitorError("branch no longer exists")
                if unique_commits(repo, branch, base):
                    raise JanitorError("branch has commits not in base")
                if branch_in_main_worktree(repo, branch):
                    raise JanitorError("branch is checked out in the main worktree")
                git(repo, "branch", "-d", branch)
                print(f"removed {ident}")
        except JanitorError as exc:
            print(f"refused {ident}: {exc}")
            failed = True
    return 1 if failed else 0


def branch_in_main_worktree(repo: Path, branch: str) -> bool:
    entries = parse_worktrees(repo)
    return any(entry["branch"] == f"refs/heads/{branch}" and entry["path"].resolve() == entries[0]["path"].resolve() for entry in entries)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")
    for name in ("scan", "clean"):
        p = sub.add_parser(name)
        p.add_argument("--repo", type=Path, default=Path.cwd())
        p.add_argument("--base", help="base ref (default: origin/HEAD, main, or master)")
        p.add_argument("--stale-days", type=int, default=14)
        p.add_argument("--json", action="store_true")
        p.add_argument("--gh", help="gh executable path (primarily useful for testing)")
        if name == "clean":
            p.add_argument("--apply", action="store_true")
            p.add_argument("--id", action="append", required=True)
    args = parser.parse_args()
    if args.command is None:
        args.command, args.repo, args.base, args.stale_days, args.json, args.gh = "scan", Path.cwd(), None, 14, False, None
    if args.stale_days < 0:
        parser.error("--stale-days must be non-negative")
    try:
        findings = scan(args.repo, args.base, args.stale_days, args.gh)
        if args.command == "scan":
            if args.json:
                print(json.dumps(findings, indent=2))
            else:
                print("\n".join(f"{f['id']} {f['kind']}: {', '.join(f['reasons'])} — {f['suggested_action']}" for f in findings) or "No findings.")
            return 0
        return clean(args.repo, findings, args.id, args.apply, args.base)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except JanitorError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
