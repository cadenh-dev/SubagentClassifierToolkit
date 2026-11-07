---
name: compat-matrix
description: Join live orchestrator provider/model capabilities with a sourced local constraint reference, filter eligible targets, and identify research that needs verification.
---

# Compatibility Matrix

Use this skill to inspect known provider/model constraints and narrow live targets before routing work.

Save the host's `orchestrator_capabilities` tool output as a JSON file such as `caps.json`, then run:

```bash
python3 scripts/compat_matrix.py show --capabilities caps.json --json
python3 scripts/compat_matrix.py filter --capabilities caps.json --require tool_calling,image_input --min-context 400000 --json
```

The matrix joins live model ids to `data/constraints.json` by exact id, then by trying the id after its first provider namespace prefix. Live capability values take precedence wherever supplied. A live row without a local match is `live-only`; a local entry absent from the live snapshot is `local-only` and marked `not available`. Disabled providers remain visible in `show` and are excluded from `filter`.

The local entries came from unverified research. Treat every `verified: false` claim as tentative until a human checks its sources and corrects or confirms it. Use `stale` to find unverified or old entries and set `verified: true` only after review.

See [the matrix reference](references/matrix.md) for refresh and routing guidance.
