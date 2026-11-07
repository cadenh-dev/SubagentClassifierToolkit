---
name: secret-preflight
description: Check credential availability before starting repository work, and request missing credentials through the agent host without exposing values.
---

# Secret Preflight

Before work that needs credentials, inspect the project's manifest and run the checker:

```bash
python3 scripts/preflight.py check --manifest .agent-secrets.json
python3 scripts/preflight.py scan-repo --manifest .agent-secrets.json
```

Use the installed skill script path if running outside this skill directory. A sample manifest is in [`assets/secrets.example.json`](assets/secrets.example.json); schema details are in [`references/manifest.md`](references/manifest.md).

Manifest command checks do not run by default. Add `--allow-commands` only after reviewing and trusting every command in the manifest; they run with only `PATH` and `HOME`, closed stdin, a bounded timeout, and discarded output:

```bash
python3 scripts/preflight.py check --manifest .agent-secrets.json --allow-commands
```

For each missing required credential, call the host's `request_secret` tool for the named credential. Never ask the user to paste a secret into chat. Accept its `secretRef` or file delivery as directed by the host. Never print a value, put a value in a file or command argument, or include one in logs. Python cannot call `request_secret`; it only checks presence.

The checker never reads file source contents. Review scan findings and remove committed credentials; rotate any credential that may have been exposed. Exit codes are `0` when required credentials are present and the scan is clean, `1` when a required credential is missing or scan findings exist, and `2` for an invalid manifest, invalid timeout, or operational error.

Tests build token-shaped data at runtime from string fragments instead of storing realistic credential literals. The checked-in fixture contains only ordinary explanatory text, so scanning the repository does not report test data as a real credential.
