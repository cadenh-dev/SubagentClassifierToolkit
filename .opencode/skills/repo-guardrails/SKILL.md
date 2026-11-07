---
name: repo-guardrails
description: Generate or refresh a managed AGENTS.md section from a project-classifier profile and repository-specific overrides.
---

# Repo guardrails

Use this skill after classifying a repository when its agents need consistent stack commands, routing guidance, protected paths, architecture boundaries, and review expectations.

Run the generator with an explicit profile because the project-classifier writes profiles only when its generated skill is built:

```bash
python3 .opencode/skills/repo-guardrails/scripts/generate_agents_md.py --profile .opencode/skills/<project>-tool-router/data/project-profile.json --repo .
```

Add optional repository-specific facts with `--overrides overrides.json`. Supported keys are arrays: `do_not_edit`, `architecture_boundaries`, and `review_expectations`. Profile facts are rendered in sorted order without timestamps.

The generator creates `AGENTS.md`, replaces only a balanced managed section, and preserves outside content byte-for-byte. An existing unmarked file requires `--append`. Duplicate, reversed, or unbalanced markers are refused. `--check` reports drift with exit 1 without writing; `--stdout` prints only the section. Exit 2 indicates invalid usage; exit 1 indicates invalid profile, refusal, or drift.

See [operation details](references/usage.md).
