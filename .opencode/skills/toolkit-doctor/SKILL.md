---
name: toolkit-doctor
description: Check local runtimes, agent CLIs, Git remotes, MCP server names, profile expectations, and installed skill drift with a read-only report.
---

# Toolkit Doctor

Run `python3 scripts/doctor.py` from a repository to inspect the local agent toolchain. By default it checks the current repository and `~/.config/opencode/skills` for installed skill drift.

Use `--repo PATH`, `--skills-dir PATH`, and `--profile PATH` to select inputs. Add `--json` for machine output. Remote checks use `git ls-remote` with prompts disabled; `--fetch` is the only option that updates refs. Every subprocess has a bounded `--timeout` (default 10 seconds).

The report includes local runtimes and CLIs, GitHub CLI authentication status, remote reachability, upstream status, MCP server names, profile expectations, and installed skill hashes. MCP configuration values, URLs, headers, and environment values are never included. Checks are read-only; `--fetch` is the only option that updates Git refs.

## Checks and statuses

Check IDs are `command.<name>` for each runtime and CLI, `auth.gh` for GitHub CLI login, `git.remote.<name>` for remote reachability, `git.upstream` for ahead/behind state, `mcp.config.<source>` for config parsing, `mcp.servers` for discovered names, `profile.expected` for profile commands, and `skills.drift` for installed skill comparison.

Statuses are `ok` (available or healthy), `skip` (not installed, not configured, or no upstream), `warn` (an expected command is missing, config parsing failed, or the branch is behind), and `error` (a configured Git remote cannot be reached). Exit code is 0 when no check is `error`, 1 when any check is `error`, and 2 for invalid CLI input.
