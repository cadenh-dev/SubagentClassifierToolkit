# Detection rules and confidence

The builder is deliberately conservative. It records evidence and confidence rather than guessing a single "project language."

## Languages

A language is detected from:

1. **Manifests** — `pyproject.toml`, `pom.xml`, `package.json`, `Cargo.toml`, `go.mod`, `CMakeLists.txt`, `project.godot`, etc.
2. **Source extensions** — `.py`, `.java`, `.ts`, `.go`, `.rs`, `.cs`, `.gd`, etc.
3. **Frameworks** — manifest/dependency names such as Django, FastAPI, Spring Boot, React, Next.js, Godot.

Confidence:

| Confidence | Meaning |
|---|---|
| `high` | Manifest plus at least 3 source files, or a strong framework manifest |
| `medium` | Manifest alone, or at least 3 source files without a manifest |
| `low` | One or two files, no manifest, or only a generated artifact |

A manifest-only language is still listed; it may be a build wrapper or an empty scaffold.

## Polyglot projects

`ambiguous` is true when two or more languages have at least 3 files and the top language is under 70% of the code files. A generated router must route by path/module in that case, not by the repository root.

## Engines and media tools

| Tool | Evidence |
|---|---|
| Blender | `.blend` / `.blend1`, or Python files importing `bpy` |
| Godot | `project.godot`, `.gd`, `.tscn`, `.tres` |
| Unity | `ProjectSettings/ProjectVersion.txt`, `.unity`, `.prefab` |
| ffmpeg | video/audio extensions |
| Docker | `Dockerfile`, `docker-compose.yml`, `compose.yml` |
| Package managers | lockfiles |
| Build tools | Maven, Gradle, CMake, Make, Cargo, Go, .NET manifests |

`available` means the command is on `PATH`. It does not mean the project's version is installed or configured correctly.

## Commands

Commands are suggestions derived from manifests and conventions:

- `package.json` scripts (`test`, `lint`, `build`, `dev`, `typecheck`) with the detected package manager.
- `pytest`, `ruff`, `mypy` when the Python manifest mentions them or test files follow `test_*`.
- `mvn test`, `./gradlew test`, `cargo test`, `go test ./...`, `ctest`, `dotnet test`.
- `make test` and `composer test` are marked as "verify this exists" because not every project defines them.

The builder does not execute commands.

## File scanning

It skips dependency and build directories (`.git`, `node_modules`, `.venv`, `dist`, `build`, `target`, `.next`, `vendor`, caches) and stops at `--max-files` (default 50,000). Counts are a lower bound when the scan is truncated.

It reads text manifests up to 500 KB and Python/GDScript files up to 200 KB for engine imports. It does not read `.env`, credential stores, or private keys.

## Failure modes

- A monorepo root may look polyglot while each package has a clear stack. Generate one router per package with `--root packages/api`.
- A generated client can dominate extension counts. Use path scoping or add exclusions in a future version.
- A framework can be present in a lockfile but unused. Treat `high` confidence as "the manifest says so," not "the code proves it."
- A tool can be used by CI but not installed locally. The runtime column is the local truth.
- `package.json` can describe both JavaScript and TypeScript. The router should use file paths, not the manifest alone.

## Extending

Add a language in `LANGUAGES`, a manifest mapping, or a framework pattern in the builder. Add a test fixture before changing confidence rules.
