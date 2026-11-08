#!/usr/bin/env python3
"""Install the delegation-check bootstrap into a project's instructions file.

A skill only loads when the agent decides its description matches the task.
This snippet sits in AGENTS.md (or CLAUDE.md), which agents read every session,
and lists the situations that should load subagent-orchestrator. It also says
when not to, so it does not push agents toward delegating small work.

The snippet lives between marker comments. Re-running replaces it in place;
the rest of the file is never touched.

Examples:
  install_bootstrap.py --project /path/to/repo              # AGENTS.md
  install_bootstrap.py --project /path/to/repo --file CLAUDE.md
  install_bootstrap.py --project /path/to/repo --check      # exit 1 if missing or stale
  install_bootstrap.py --project /path/to/repo --remove

Exit codes:
  0  installed, updated, removed, or already current
  1  --check found the snippet missing or out of date
  2  bad input
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SNIPPET_PATH = Path(__file__).resolve().parents[1] / "assets" / "bootstrap.md"
START = "<!-- subagent-orchestrator:bootstrap"
END = "<!-- /subagent-orchestrator:bootstrap -->"
BLOCK = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)


def read_snippet() -> str:
    snippet = SNIPPET_PATH.read_text(encoding="utf-8").strip()
    if not snippet.startswith(START) or not snippet.endswith(END):
        raise ValueError(f"{SNIPPET_PATH} is missing its marker comments")
    return snippet


def plan_change(current: str | None, snippet: str, remove: bool) -> tuple[str | None, str]:
    """Return (new_text or None if unchanged, action)."""
    newline = "\r\n" if current and "\r\n" in current else "\n"
    block = snippet.replace("\n", newline)
    if current is None:
        if remove:
            return None, "absent"
        return block + newline, "created"
    blocks = BLOCK.findall(current)
    if len(blocks) > 1:
        raise ValueError("the file has more than one bootstrap block; remove the extras by hand")
    if remove:
        if not blocks:
            return None, "absent"
        text = BLOCK.sub("", current)
        text = re.sub(r"(\r?\n){3,}", newline * 2, text).strip() + newline
        return (text if text.strip() else ""), "removed"
    if blocks:
        if blocks[0] == block:
            return None, "current"
        return BLOCK.sub(lambda _m: block, current), "updated"
    if not current.strip():
        return block + newline, "appended"
    # Leave exactly one blank line between the existing text and the block.
    if current.endswith(newline * 2):
        separator = ""
    elif current.endswith(newline):
        separator = newline
    else:
        separator = newline * 2
    return current + separator + block + newline, "appended"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", type=Path, required=True, help="project root")
    parser.add_argument("--file", default="AGENTS.md", help="instructions file relative to the project (default AGENTS.md)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="report only; exit 1 if missing or out of date")
    mode.add_argument("--remove", action="store_true", help="remove the snippet")
    parser.add_argument("--dry-run", action="store_true", help="print what would change without writing")
    args = parser.parse_args(argv)

    project = args.project.resolve()
    if not project.is_dir():
        print(f"error: {project} is not a directory", file=sys.stderr)
        return 2
    rel = Path(args.file)
    if rel.is_absolute() or ".." in rel.parts:
        print("error: --file must be a path inside the project", file=sys.stderr)
        return 2
    target = project / rel
    if target.is_symlink():
        print(f"error: {target} is a symlink; edit the real file instead", file=sys.stderr)
        return 2

    try:
        snippet = read_snippet()
        current = target.read_bytes().decode("utf-8") if target.exists() else None  # keep CRLF as-is
        new_text, action = plan_change(current, snippet, args.remove)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.check:
        ok = action == "current"
        print(f"{target}: {'current' if ok else 'missing' if action in {'created', 'appended'} else 'out of date'}")
        return 0 if ok else 1
    if new_text is None:
        print(f"{target}: {action}, nothing to do")
        return 0
    if args.dry_run:
        print(f"{target}: would be {action}")
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as handle:
        handle.write(new_text)
    print(f"{target}: {action}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
