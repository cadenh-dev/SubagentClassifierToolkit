# Manifest

The manifest is JSON with a top-level `secrets` array. Every entry has a unique `name`, `purpose`, boolean `required`, `sources`, and `used_by` list. Sources:

- `{"type":"env","var":"SERVICE_TOKEN"}` checks only whether the variable exists and is nonempty.
- `{"type":"file","path":".secrets/service"}` checks existence, regular-file status, and permission mode. Relative paths resolve from the manifest directory. Contents are never opened.
- `{"type":"command","check":["gh","auth","status"]}` runs argv directly with a timeout, discards stdout/stderr, and reports only success/failure status. Use only trusted commands.

Run `check` for source presence and `scan-repo` for tracked files. Reports identify names and statuses, never values. The scanner uses high-confidence token patterns and checks manifest-listed environment variable assignments. It also warns about tracked `.env`-like files that are not ignored.
