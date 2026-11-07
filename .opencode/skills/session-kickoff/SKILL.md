---
name: session-kickoff
description: Set and persist the main agent's working role per project or globally, and provide a thread-start brief. Use for new threads or projects, role requests such as planner or reviewer, or switching roles.
---

# Session Kickoff

Use when starting a new project/thread, when the user says “act as planner”, “you only plan”, “switch role”, or asks to persist an agent role. A user instruction in the current thread always overrides the saved role for that thread.

## Flow

1. List available presets with `python3 scripts/kickoff.py presets`.
2. Persist a role using `python3 scripts/kickoff.py set --preset planner --scope project --repo .` (or `--scope global`). Add dotted overrides with `--set key.path=value`.
3. At thread start, run `python3 scripts/kickoff.py brief --repo .`, adopt the role, and announce it in one line.
4. For a new repository, print the ordered checklist with `python3 scripts/kickoff.py onboard --repo .`; it is read-only and never runs setup commands.

Roles resolve in this order: `<repo>/.agent-role.json`, global default (`$XDG_CONFIG_HOME/agent-role/default.json`, or `~/.config/agent-role/default.json`), then built-in `planner`. See [presets.md](references/presets.md) for preset fields and target handling.

`assets/global-rules.md` contains the managed start-of-thread rule. Install it into the agent's global instruction file with `python3 scripts/kickoff.py install-rules --file PATH`. The rule cannot fire unless it has been installed and the agent reads that instruction file. A user-specified role for the current thread wins over persistence.

Suggested delegation targets are resolved against live capabilities; consult subagent-orchestrator before delegating, honor exact user pins, and explain unavailable targets rather than silently switching providers.
