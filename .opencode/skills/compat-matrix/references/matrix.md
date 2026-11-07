# Compatibility matrix reference

## Capture live capabilities

Call the host's `orchestrator_capabilities` tool and save its JSON output to a file, for example `caps.json`. The snapshot supplies current provider instances, model ids, option ids and allowed values, provider constraints, disabled status, and `canRunChildTask`. Do not maintain these transient facts in the local data file.

## Join and filter

Run `python3 scripts/compat_matrix.py show --capabilities caps.json --json`. Exact model ids are matched first. If no exact id exists, the matrix strips one leading namespace segment such as `openai/` or `opencode-go/` and tries the remaining id. Snapshot values take precedence where both sources provide a field; options and availability are always live. Unmatched live models are `live-only`. Local entries not offered by the snapshot are `local-only` with availability `not available`. Disabled providers are marked and never returned by `filter`.

Before selecting a target, the orchestrator can call:

```bash
python3 scripts/compat_matrix.py filter --capabilities caps.json \
  --require tool_calling,image_input --min-context 400000 --min-output 16000 --json
```

Only enabled providers that can run child tasks and whose known constraints meet all requirements are returned under `targets`. Unknown facts cannot satisfy a requirement; applicable candidates appear under `unknown` with status `unknown — verify`. Add `--strict` to return exit code 1 when no targets match. Use returned target ids/options as inputs to the existing target selector; this skill does not change selection policy.

## Verify and refresh local facts

`data/constraints.json` contains stable model traits with source URLs. It is seed research and is deliberately shipped with `verified: false`; the claims have not been independently verified. Pricing and rapidly changing rate limits do not belong here. Re-check each source before setting `verified` true.

After research or verification, update the relevant entry, source URLs, and `retrieved` date (`YYYY-MM-DD`), then run:

```bash
python3 scripts/compat_matrix.py validate
python3 scripts/compat_matrix.py stale --max-age-days 60 --json
```

`stale` reports every unverified entry and any verified entry older than the requested age. Re-capture live capabilities whenever provider/model availability or options may have changed.
