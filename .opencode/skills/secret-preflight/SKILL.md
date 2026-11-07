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

For each missing required credential, call the host's `request_secret` tool for the named credential. Accept its `secretRef` or file delivery as directed by the host. Never ask the user to paste a secret into chat, print a value, put a value in a file or command argument, or include one in logs. Python cannot call `request_secret`; it only checks presence.

The checker never reads file source contents. Command checks have bounded timeouts and discard all output. Review scan findings and remove committed credentials; rotate any credential that may have been exposed.
