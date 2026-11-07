#!/usr/bin/env python3
"""Read-only Git hygiene scan and confirmation-gated cleanup."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

TIMEOUT = 15


class JanitorError(Exception):
    pass


def run(args: list[str], cwd: Path, check: bool = True) -> str:
    try:
        p = subprocess.run(args, cwd=cwd, text=True, capture_output=True, timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise JanitorError(f"command failed or timed out: {args[0]}") from exc
    if check and p.returncode:
        raise JanitorError(p.stderr.strip() or f"command failed: {' '.join(args)}")
    return p.stdout.strip()


def git(repo: Path, *args: str, check: bool = True) -> str:
    return run(["git", "-C", str(repo), *args], repo, check)


def identity(f: dict) -> str:
    return f.get("path") or f.get("branch") or str(f.get("pr"))


def add(items: list[dict], kind: str, ident: str, reasons: list[str], safe: bool, action: str, **extra: str) -> None:
    raw = f"{kind}:{ident}".encode()
    items.append({"id": hashlib.sha256(raw).hexdigest()[:12], "kind": kind, **extra,
                  "reasons": sorted(set(reasons)), "safe_to_remove": safe,
                  "suggested_action": action})


def resolve_base(repo: Path, base: str) -> str:
    if base == "origin/HEAD":
        symbolic = git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", check=False)
        base = symbolic.removeprefix("refs/remotes/") if symbolic else "main"
    return git(repo, "rev-parse", "--verify", base)


def scan(repo: Path, base_arg: str, stale_days: int) -> list[dict]:
    repo = Path(git(repo, "rev-parse", "--show-toplevel"))
    base = resolve_base(repo, base_arg)
    now = datetime.now(timezone.utc).timestamp()
    items: list[dict] = []
    current = Path(git(repo, "rev-parse", "--show-toplevel"))
    for line in git(repo, "worktree", "list", "--porcelain").splitlines():
        if not line.startswith("worktree "):
            continue
        path = Path(line[9:])
        details = git(repo, "worktree", "list", "--porcelain", "-z")
        block = next((b for b in details.split("\0\0") if b.startswith("worktree " + str(path))), "")
        reasons = []
        if not path.exists(): reasons.append("missing directory")
        if "prunable" in block: reasons.append("prunable")
        if "detached" in block: reasons.append("detached")
        if "locked" in block: reasons.append("locked")
        branch = next((x[7:] for x in block.split("\0") if x.startswith("branch ")), "")
        dirty = bool(run(["git", "-C", str(path), "status", "--porcelain"], repo, check=False)) if path.exists() else False
        if dirty: reasons.append("dirty")
        merged = bool(branch and git(repo, "merge-base", "--is-ancestor", branch, base, check=False) == "")
        unique = bool(branch and git(repo, "rev-list", "--count", f"{base}..{branch}", check=False) not in ("", "0"))
        if merged: reasons.append("branch merged into base")
        last = git(repo, "-C", str(path), "log", "-1", "--format=%ct", check=False) if path.exists() else ""
        if last and now - int(last) >= stale_days * 86400: reasons.append(f"no commits in {stale_days} days")
        if reasons:
            current_path = Path(run(["git", "-C", str(path), "rev-parse", "--show-toplevel"], repo, check=False)) if path.exists() else current
            safe = not dirty and "locked" not in reasons and current_path != current and not unique
            add(items, "worktree", str(path), reasons, safe, "git worktree remove <path>" if path.exists() else "git worktree prune", path=str(path))

    branches = git(repo, "for-each-ref", "--format=%(refname:short)%09%(upstream:track)%09%(upstream)", "refs/heads").splitlines()
    for line in branches:
        name, track, upstream = (line.split("\t") + ["", ""])[:3]
        reasons = []
        merged = git(repo, "merge-base", "--is-ancestor", name, base, check=False) == ""
        if merged: reasons.append("merged into base")
        if upstream and "[gone]" in track: reasons.append("upstream gone")
        if not upstream and git(repo, "rev-list", "--count", f"{base}..{name}", check=False) == "0": reasons.append("no upstream and no unique commits")
        branch_path = git(repo, "worktree", "list", "--porcelain").splitlines()
        current_branch = git(repo, "branch", "--show-current")
        if name == current_branch: reasons = []
        if reasons:
            add(items, "branch", name, reasons, merged or ("upstream gone" in reasons and not git(repo, "rev-list", "--count", f"{base}..{name}")), f"git branch -d {name}", branch=name)

    if shutil.which("gh"):
        auth = run(["gh", "auth", "status"], repo, check=False)
        if "Logged in" in auth:
            data = run(["gh", "pr", "list", "--state", "all", "--limit", "200", "--json", "number,state,headRefName,updatedAt,mergedAt"], repo)
            for pr in json.loads(data):
                state, head, number = pr["state"], pr["headRefName"], str(pr["number"])
                reasons = []
                updated = datetime.fromisoformat(pr["updatedAt"].replace("Z", "+00:00")).timestamp()
                if state == "OPEN" and now - updated >= stale_days * 86400: reasons.append("no activity")
                if state == "OPEN" and git(repo, "merge-base", "--is-ancestor", head, base, check=False) == "": reasons.append("head branch merged into base")
                exists = git(repo, "show-ref", "--verify", f"refs/heads/{head}", check=False)
                if state == "CLOSED" and not pr.get("mergedAt") and exists: reasons.append("closed unmerged; local branch exists")
                if reasons: add(items, "pr", number, reasons, False, f"gh pr close {number} (human decision required)", pr=number)
    return sorted(items, key=lambda f: f["id"])


def clean(repo: Path, findings: list[dict], ids: list[str], apply: bool) -> int:
    by_id = {f["id"]: f for f in findings}
    missing = sorted(set(ids) - by_id.keys())
    if missing: raise ValueError("unknown finding id(s): " + ", ".join(missing))
    failed = False
    for ident in ids:
        f = by_id[ident]
        if not apply:
            print(f"would: {f['suggested_action']} [{ident}]"); continue
        if f["kind"] == "pr":
            print(f"manual only: {f['suggested_action']}"); continue
        if not f["safe_to_remove"]:
            print(f"refused {ident}: finding is not marked safe; re-scan and inspect state"); failed = True; continue
        try:
            if f["kind"] == "worktree":
                path = Path(f["path"])
                if path.exists() and git(repo, "-C", str(path), "status", "--porcelain"):
                    raise JanitorError("worktree is dirty")
                if path.resolve() == Path(git(repo, "rev-parse", "--show-toplevel")).resolve(): raise JanitorError("current worktree")
                git(repo, "worktree", "remove", str(path)) if path.exists() else git(repo, "worktree", "prune")
            else:
                branch = f["branch"]
                if git(repo, "rev-list", "--count", f"{resolve_base(repo, 'main')}..{branch}") not in ("0", ""): raise JanitorError("branch has unique commits")
                git(repo, "branch", "-d", branch)
            print(f"removed {ident}")
        except JanitorError as exc:
            print(f"refused {ident}: {exc}"); failed = True
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    for name in ("scan", "clean"):
        p = sub.add_parser(name)
        p.add_argument("--repo", type=Path, default=Path.cwd())
        p.add_argument("--base", default="origin/HEAD")
        p.add_argument("--stale-days", type=int, default=14)
        p.add_argument("--json", action="store_true")
        if name == "clean": p.add_argument("--apply", action="store_true"); p.add_argument("--id", action="append", required=True)
    args = parser.parse_args()
    if not args.command: args.command = "scan"; args.repo=Path.cwd(); args.base="origin/HEAD"; args.stale_days=14; args.json=False
    if args.stale_days < 0: parser.error("--stale-days must be non-negative")
    try:
        findings = scan(args.repo, args.base, args.stale_days)
        if args.command == "scan":
            print(json.dumps(findings, indent=2) if args.json else "\n".join(f"{f['id']} {f['kind']}: {f['reasons']} — {f['suggested_action']}" for f in findings) or "No findings.")
            return 0
        return clean(args.repo, findings, args.id, args.apply)
    except (JanitorError, ValueError) as exc:
        print(str(exc), file=sys.stderr); return 1


if __name__ == "__main__":
    sys.exit(main())
