---
name: skill-linter
description: Validate Agent Skills before install or after editing. Use when creating, reviewing, or installing a skill and you need a deterministic read-only check of frontmatter, name and directory agreement, description length, local links, Python helper syntax, secrets, or leaked user paths.
---

# Skill Linter

Run a deterministic, read-only check on one skill or every skill under a tree. The linter catches packaging defects; it does not judge whether the instructions make good decisions.

## Run it

Lint one skill:

```bash
python3 scripts/lint_skills.py .opencode/skills/my-skill
```

Lint every skill under a root:

```bash
python3 scripts/lint_skills.py .opencode/skills
python3 scripts/lint_skills.py .
```

Machine-readable output and stricter exit behavior:

```bash
python3 scripts/lint_skills.py .opencode/skills/my-skill --json
python3 scripts/lint_skills.py .opencode/skills --strict
```

A path may be a skill folder containing `SKILL.md`, a directory tree containing skills, or a `SKILL.md` file. The default path is the current directory. Results are grouped by skill; every diagnostic includes a stable code, severity, path, and line when one applies.

## What it checks

- Frontmatter starts and ends with `---`, and required `name` and `description` fields are present and nonempty.
- `name` uses lowercase letters, digits, and single hyphens, is at most 64 characters, and matches the skill directory.
- `description` is at most 1024 characters.
- Local Markdown links and images exist. URLs, `#anchors`, and URL fragments are not fetched or resolved; fenced code blocks and inline code spans are ignored.
- Python files under `scripts/`, plus local linked `.py` files, compile through `compile()`. Imported code is never executed and no `__pycache__` is written.
- High-confidence secrets and absolute user paths are reported without printing the matched value.
- YAML constructs outside the supported subset produce an explicit diagnostic instead of being guessed.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | No errors. Warnings are allowed unless `--strict` is set. |
| `1` | One or more errors, or warnings with `--strict`. |
| `2` | Invalid command-line usage. |

## Read-only contract

The checker only reads the inspected tree. It does not edit skills, execute inspected scripts, import inspected modules, fetch remote links, or generate bytecode. The script itself uses only the Python standard library and supports Python 3.10+.

## Limits

- The frontmatter reader is a small YAML subset, not a YAML parser. It supports plain and quoted scalars, ordinary folded (`>`) and literal (`|`) descriptions, and nested mappings such as `metadata`. Anchors, aliases, tags, flow collections, multi-line plain scalars, multi-line quoted scalars, sequences, and explicit keys are reported as unsupported.
- Folded and literal scalars are approximated for prose; exotic indentation and chomping combinations may differ from a full YAML implementation.
- Link checking covers inline links, images, and reference definitions. Reference-style usages are not resolved to their definitions, and link anchors are not validated.
- Fenced code blocks and inline code spans are excluded from link checks. Four-space indented code blocks are not detected as code.
- Link targets containing spaces must use `<...>` or percent-encoding, as Markdown requires.
- Secret and absolute-path checks are high-confidence heuristics, not a security audit.
- Script compilation proves syntax, not behavior, imports, type correctness, or runtime success.

See [`references/checks.md`](references/checks.md) for the diagnostic codes, YAML subset details, and output schema.

## Files

- [`scripts/lint_skills.py`](scripts/lint_skills.py) — the read-only checker.
- [`references/checks.md`](references/checks.md) — codes, parsing rules, and limits.
