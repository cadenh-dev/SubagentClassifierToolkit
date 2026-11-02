---
name: project-classifier
description: Inspect a repository, detect its real languages, frameworks, engines, build tools, and commands, and generate a project-local tool-router skill. Use when onboarding a repo, resolving Python vs Java, Blender vs Godot, choosing a test/build command, or refreshing a project classifier after a stack change.
---

# Project Classifier

Build a **project-local classifier** from repository evidence, not from the model's assumptions.

The output is a generated skill at:

```text
<project>/.opencode/skills/<project>-tool-router/
├── SKILL.md
├── data/project-profile.json
└── references/evidence.md
```

The generated router answers questions like:

- Is this task Python or Java?
- Is this Blender or Godot work?
- Which test, build, and package-manager commands apply?
- Which manifest or source path is the evidence?

## When to use

- Onboarding a new repository.
- A polyglot repo where the root language is misleading.
- Choosing between two real options, such as Python vs Java or Blender vs Godot.
- Refreshing a project router after adding a service, engine, or build system.
- Giving a subagent a self-contained toolchain handoff.

Do **not** use it to invent a stack the project does not already use. If the evidence is weak, the generated skill should say so.

## Workflow

### 1. Inspect before generating

Check the repository root, manifests, and existing generated router:

```bash
git status --short
find . -maxdepth 2 -name 'pyproject.toml' -o -name 'pom.xml' -o -name 'package.json' \
  -o -name 'project.godot' -o -name '*.blend' -o -name 'Cargo.toml' -o -name 'go.mod'
```

Do not read secrets, `.env` files, credential stores, or private keys.

### 2. Dry-run the detector

```bash
python3 scripts/build_project_classifier.py --root . --dry-run
```

Review:

- `primary_language` and `ambiguous`
- per-language file counts and manifests
- frameworks and engines
- runtime availability (`available` vs `NOT ON PATH`)
- warnings

### 3. Resolve ambiguity before writing

Ask the user when:

- two or more languages have substantial file counts,
- both Blender and Godot are present and the task could use either,
- the primary language comes from one file with no manifest,
- the project uses a runtime that is not installed.

Do not "fix" ambiguity by deleting one side of the evidence. A generated router can say "route by path; ask if the module is unclear."

### 4. Generate

```bash
python3 scripts/build_project_classifier.py \
  --root . \
  --out .opencode/skills/<project>-tool-router
```

Use `--force` only when replacing a previously generated router. The builder refuses to overwrite the project root. It removes the target directory on `--force`, so do not put hand-written content inside a generated skill.

Default output is `<root>/.opencode/skills/<project-slug>-tool-router/`.

### 5. Review the generated skill

Check:

- the frontmatter `name` matches the directory,
- the language table matches the manifests,
- the tie-breakers make sense for this project,
- commands are real (especially `make test` and `composer test`),
- warnings about missing runtimes are visible,
- no secret values or private paths leaked into the evidence file.

Edit the **builder or project files**, not the generated counts. Re-run with `--force`.

### 6. Validate

```bash
python3 - <<'PY'
import re
from pathlib import Path
p = Path('.opencode/skills/<project>-tool-router/SKILL.md')
s = p.read_text()
m = re.match(r'^---\n(.*?)\n---', s, re.S)
print(m.group(1))
assert p.parent.name in s
PY
```

Then test 3–5 representative tasks:

- a Python-only change,
- a Java-only change,
- a shared build/test task,
- a Blender or Godot task if applicable,
- an ambiguous path.

The generated skill should route by path and evidence, not by model preference.

### 7. Commit and refresh

Commit the generated skill with the project. Refresh after:

- adding/removing a language or engine,
- moving modules between languages,
- changing the build system or package manager,
- adding a major framework.

## Rules

- **Evidence over vibes.** File counts and manifests decide; model preference does not.
- **Project use is not installation.** A repo can use Java while `java` is missing from PATH. Keep those facts separate.
- **Do not introduce a stack.** The generated router describes what exists; it does not authorize a new language or engine.
- **Route by path, not root.** In a polyglot repo, "the project is Python" is usually false. Use the task's module.
- **Blender vs Godot is an output question.** Blender is offline 3D/blockout/render; Godot is runtime/playable/parallax. If the output is unclear, ask.
- **Keep generated evidence intact.** Hand-edited counts drift immediately.
- **Do not leak secrets.** Never scan `.env`, credential files, or private keys as "evidence."

## Relationship to subagent-orchestrator

Use this skill first when the **toolchain** is unclear. Use `subagent-orchestrator` when the **model/agent** is unclear.

Order:

```text
project-classifier  -> which language / engine / command
subagent-orchestrator -> which provider / model / isolation
```

Pass the generated handoff into the subagent brief:

```text
Toolchain: Python 3.12, pytest, ruff
Evidence: pyproject.toml, src/api/**
Command: python3 -m pytest
Do not switch to Java or add a new framework.
```

## Files

- [`scripts/build_project_classifier.py`](scripts/build_project_classifier.py) — detector and generator.
- [`references/detection.md`](references/detection.md) — heuristics, evidence, confidence, limitations.
- [`assets/profile.example.json`](assets/profile.example.json) — generated profile shape.

## Limitations

- Detection is heuristic, not a build-system parser.
- It does not run tests or builds.
- It does not inspect package contents or resolve dependency versions.
- It does not prove a binary works; it only checks PATH.
- Monorepos with many independent stacks may need one generated router per subproject. Use `--root packages/api` and a scoped output.
