---
name: toolkit-doctor
description: Check local runtimes, agent CLIs, Git remotes, MCP server names, profile expectations, and installed skill drift with a read-only report.
---

# Toolkit Doctor

Run `python3 scripts/doctor.py` from a repository to inspect the local agent toolchain. By default it checks the current repository and `~/.config/opencode/skills` for installed skill drift.

Use `--repo PATH`, `--skills-dir PATH`, and `--profile PATH` to select inputs. Add `--json` for machine output. Remote checks use `git ls-remote` with prompts disabled; `--fetch` is the only option that updates refs. Every subprocess has a bounded `--timeout` (default 10 seconds).

The report includes local runtimes and CLIs, GitHub CLI authentication status, remote reachability, MCP server names, missing profile commands/runtimes, and installed skill hashes. MCP configuration values, URLs, headers, and environment values are never included.
