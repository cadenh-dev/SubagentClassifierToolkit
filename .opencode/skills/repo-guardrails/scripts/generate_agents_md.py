#!/usr/bin/env python3
"""Generate a managed AGENTS.md guardrails section from a project profile."""
import argparse
import json
import sys
from pathlib import Path

BEGIN = "<!-- toolkit:guardrails:begin -->"
END = "<!-- toolkit:guardrails:end -->"


def render(profile: dict, overrides: dict) -> str:
    lines = [BEGIN, "## Project guardrails", "", "### Stack", ""]
    langs = sorted(profile.get("languages", []), key=lambda x: x.get("name", ""))
    lines.append("- Languages: " + (", ".join(x["name"] for x in langs) or "not detected"))
    frameworks = sorted({x.get("name", "") for x in profile.get("frameworks", []) if x.get("name")})
    lines.append("- Frameworks: " + (", ".join(frameworks) or "not detected"))
    managers = sorted({x for x in ("npm", "pnpm", "yarn", "bun", "poetry", "uv", "pip", "cargo", "maven", "gradle") if any(x in " ".join(m for l in langs for m in l.get("manifests", [])).lower() for _ in [0])})
    # Infer package managers only from explicit lock/config manifest names.
    manifest_text = " ".join(m.lower() for l in langs for m in l.get("manifests", []))
    managers = [m for m, needles in (("npm", ("package-lock",)), ("pnpm", ("pnpm-lock",)), ("yarn", ("yarn.lock",)), ("bun", ("bun.lock",)), ("poetry", ("poetry.lock",)), ("uv", ("uv.lock",)), ("pip", ("requirements",)), ("cargo", ("cargo.toml",)), ("maven", ("pom.xml",)), ("gradle", ("gradle",))) if any(n in manifest_text for n in needles)]
    lines.append("- Package/build managers evidenced by manifests: " + (", ".join(sorted(managers)) or "not identified; follow the relevant project manifest"))
    lines += ["", "### Build, test, and lint commands", ""]
    commands = sorted({(x.get("command", "").strip(), x.get("source", "").strip()) for x in profile.get("commands", []) if x.get("command")})
    lines.extend(f"- `{cmd}`" + (f" — {src}" if src else "") for cmd, src in commands)
    if not commands: lines.append("- No standard commands detected. Ask before inventing one.")
    lines += ["", "### Routing", ""]
    if profile.get("ambiguous") or len(langs) > 1:
        lines.append("- Multiple toolchains are present. Route work by the affected path and its nearest manifest; when ownership is unclear, ask before choosing a toolchain.")
    else:
        lines.append("- Use the detected stack and commands relevant to the files being changed.")
    lines += ["", "### Profile warnings", ""]
    warnings = sorted(set(profile.get("warnings", [])))
    lines.extend(f"- {w}" for w in warnings)
    if not warnings: lines.append("- None reported by the classifier.")
    lines += ["", "### Do not edit", ""]
    paths = sorted(set(overrides.get("do_not_edit", [])))
    lines.extend(f"- `{p}`" for p in paths)
    if not paths: lines.append("- No paths specified. Check task-specific instructions before editing generated or protected files.")
    lines += ["", "### Architecture boundaries", ""]
    bounds = sorted(set(overrides.get("architecture_boundaries", [])))
    lines.extend(f"- {x}" for x in bounds)
    if not bounds: lines.append("- No additional boundaries specified. Follow existing module ownership and dependency direction.")
    lines += ["", "### Review expectations", ""]
    reviews = sorted(set(overrides.get("review_expectations", [])))
    lines.extend(f"- {x}" for x in reviews)
    if not reviews: lines.append("- Run the relevant commands above and review the diff for unintended changes.")
    lines += ["", END]
    return "\n".join(lines)


def update(existing: bytes, section: bytes, append: bool) -> bytes:
    text = existing.decode("utf-8")
    b, e = text.count(BEGIN), text.count(END)
    if b != e or b > 1:
        raise ValueError("malformed guardrail markers: expected zero or one balanced pair")
    if b == 1:
        start, finish = text.index(BEGIN), text.index(END)
        if start > finish: raise ValueError("malformed guardrail markers: end marker precedes begin marker")
        return (text[:start] + section.decode() + text[finish + len(END):]).encode()
    if text and not append: raise ValueError("AGENTS.md has no guardrail markers; use --append to add the section")
    return existing + (b"\n" if existing and not existing.endswith(b"\n") else b"") + section


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--repo", default=".")
    ap.add_argument("--overrides")
    ap.add_argument("--append", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--stdout", action="store_true")
    args = ap.parse_args()
    try:
        profile = json.loads(Path(args.profile).read_text(encoding="utf-8"))
        if not isinstance(profile, dict) or profile.get("schema_version") != 1: raise ValueError("profile must be a schema_version 1 object")
        overrides = json.loads(Path(args.overrides).read_text(encoding="utf-8")) if args.overrides else {}
        if not isinstance(overrides, dict): raise ValueError("overrides must be a JSON object")
        section = render(profile, overrides).encode()
        if args.stdout:
            sys.stdout.buffer.write(section); return 0
        repo = Path(args.repo).resolve(); target = repo / "AGENTS.md"
        if target.is_symlink(): raise ValueError(f"refusing to write through symlink: {target}")
        old = target.read_bytes() if target.exists() else b""
        new = update(old, section, args.append)
        if args.check: return 0 if old == new else 1
        target.write_bytes(new)
        return 0
    except (OSError, json.JSONDecodeError, UnicodeError, ValueError) as exc:
        print(str(exc), file=sys.stderr); return 1

if __name__ == "__main__": raise SystemExit(main())
