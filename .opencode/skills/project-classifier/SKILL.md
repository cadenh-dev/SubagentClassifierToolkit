---
name: project-classifier
description: Inspect a repository, detect its real languages, frameworks, engines, build tools, and commands, and generate a project-local tool-router skill. Use when onboarding a repo, resolving competing toolchain candidates, choosing a test/build command, or refreshing a project classifier after a stack change.
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

- Which detected language or toolchain owns this task?
- Which engine or build tool applies?
- Which test, build, and package-manager commands apply?
- Which manifest or source path is the evidence?

## When to use

- Onboarding a new repository.
- A polyglot repo where the root language is misleading.
- Choosing between two or more real toolchain candidates.
- Refreshing a project router after adding a service, engine, or build system.
- Giving a subagent a self-contained toolchain handoff.

Do **not** use it to invent a stack the project does not already use. If the evidence is weak, the generated skill should say so.

## Workflow

### 1. Inspect before generating

Check the repository root, manifests, and existing generated router:

```bash
git status --short
# The builder scans manifests and source files; no manual extension list is required.
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
- two or more engines or toolchains could produce the requested artifact,
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
- the routing rules make sense for this project,
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

- a change in each detected language,
- a shared build/test task,
- a task for each detected engine or tool,
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
- **Project use is not installation.** A repo can use a language or engine while its runtime is missing from PATH. Keep those facts separate.
- **Do not introduce a stack.** The generated router describes what exists; it does not authorize a new language or engine.
- **Route by path, not root.** In a polyglot repo, "the project is <one language>" is usually false. Use the task's module.
- **Competing tools are an output question.** Choose the tool that produces the requested artifact; if two tools could, ask which output is required.
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
Toolchain: <detected language/tool>, <detected commands>
Evidence: <manifest/path>
Command: <detected test/build command>
Do not switch stacks or add a new framework.
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
