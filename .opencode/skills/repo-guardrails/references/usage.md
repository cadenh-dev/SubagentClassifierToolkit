# Usage and safety

The profile must be schema version 1 as emitted by `project-classifier`. The generator does not run discovery: the builder has no fixed profile destination, so callers pass `--profile` explicitly. `--repo` selects the AGENTS.md destination, not the profile source.

Overrides JSON accepts `do_not_edit`, `architecture_boundaries`, and `review_expectations`, each a list of strings. Keep these facts repository-specific; the classifier cannot infer ownership or review policy reliably.

`--stdout` is a render-only mode. `--check` computes the prospective file and never writes. The only managed content is between the exact marker lines. Existing content outside them is retained as original UTF-8 bytes. An unmarked nonempty file is not assumed to be safe to rewrite; explicitly opt into appending with `--append`.
