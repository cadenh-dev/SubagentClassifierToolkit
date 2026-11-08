# Presets

`presets.json` is schema version 1. Every preset has an `id`, human-readable `summary`, `main_agent.does` and `main_agent.does_not` lists, `delegation` entries, and `policies`.

Each delegation entry names the work role and gives a `providerInstanceId`, `model`, `options` object, and explanatory `notes`. These targets are suggestions resolved against live orchestrator capabilities. If a target is explicitly pinned and unavailable, report that and follow subagent-orchestrator's exact-pin rules; never silently substitute another provider.

Policies describe parent coding limits, whether review is required, acceptance expectations, DeepSeek off-peak handling, cost preference, and provider-limit behavior. `planner` is the built-in default. `builder` codes in the main agent; `reviewer` reads and critiques only; `solo` keeps all work in the main agent without delegation.
