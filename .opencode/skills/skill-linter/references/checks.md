# Skill Linter Checks

This reference describes the checks, diagnostic codes, and deliberate limits of [`scripts/lint_skills.py`](../scripts/lint_skills.py).

## Frontmatter

The reader recognizes a deliberately small YAML subset between the opening and closing `---` lines.

Supported:

- top-level `key: value` scalars;
- single-quoted and double-quoted scalars on one line;
- trailing comments outside quotes;
- block scalars with `|`, `>`, `|-`, `>-`, `|+`, `>+`, indentation indicators such as `|2`, and combinations such as `|2-`;
- nested mappings, which is enough for existing `metadata` fields;
- blank lines and comment-only lines.

Reported as unsupported rather than guessed:

- anchors (`&`), aliases (`*`), and tags (`!`);
- flow mappings and sequences (`{}` and `[]`);
- multi-line plain scalars;
- multi-line quoted scalars;
- top-level or nested sequences (`- item`);
- explicit keys (`? key`);
- YAML directives (`%YAML`) and document markers inside the frontmatter;
- tab indentation.

Fold and literal blocks are approximated for ordinary prose. More-indented literal lines, unusual chomping, and complex indentation indicators can differ from a full YAML implementation.

## Diagnostic codes

| Code | Severity | Meaning |
|---|---|---|
| `path.not_found` | error | An input path does not exist. |
| `path.not_skill` | error | An input file is not named `SKILL.md`. |
| `root.no_skills` | error | No `SKILL.md` was found under a directory input. |
| `skill.missing_skill_md` | error | A discovered skill folder has no `SKILL.md`. |
| `file.read_error` | error | A Markdown or text file could not be read. |
| `file.encoding` | error | `SKILL.md` is not valid UTF-8. |
| `frontmatter.missing` | error | The file does not start with `---`. |
| `frontmatter.unclosed` | error | The opening `---` has no closing delimiter. |
| `frontmatter.malformed` | error | A line is not a supported top-level `key: value` entry. |
| `frontmatter.duplicate_key` | error | A top-level key appears more than once. |
| `frontmatter.unsupported` | error or warning | A YAML construct outside the supported subset was found. Required fields are errors; optional and nested fields are warnings. |
| `field.missing` | error | `name` or `description` is absent. |
| `field.empty` | error | `name` or `description` is present but empty. |
| `name.invalid` | error | `name` is not lowercase letters, digits, and single hyphens. |
| `name.too_long` | error | `name` exceeds 64 characters. |
| `name.directory_mismatch` | error | `name` does not match the containing folder. |
| `description.too_long` | error | `description` exceeds 1024 characters. |
| `link.empty` | error | A Markdown link has no target. |
| `link.missing` | error | A local link target does not exist. |
| `link.absolute` | warning | A local link uses an absolute path and is not portable. |
| `link.outside_skill` | warning | An existing local link points outside the skill folder. |
| `script.syntax` | error | A Python helper does not compile. |
| `script.encoding` | error | A Python helper cannot be decoded using its declared encoding. |
| `script.read_error` | error | A Python helper could not be read. |
| `secret.suspected` | error | A high-confidence token shape was found. The value is not printed. |
| `path.absolute_user` | error | An absolute Windows or POSIX user path was found. The value is not printed. |

## Name and description rules

- `name` must match `^[a-z0-9]+(?:-[a-z0-9]+)*$`.
- `name` must be at most 64 characters.
- `name` must equal the skill directory name. A folder named `Tool Router` would fail both the name rule and the directory match.
- `description` must be nonempty and at most 1024 characters after trimming surrounding whitespace.

These rules match the Agent Skills conventions used by the rest of this toolkit. Keep the description discriminating: describe what the skill does and when it applies, not every capability it has.

## Link rules

The linter scans every Markdown file in the skill, not only `SKILL.md`.

Checked:

- inline links: `[text](references/example.md)`;
- images: `![alt](assets/example.png)`;
- reference definitions: `[label]: references/example.md`;
- links with titles: `[text](references/example.md "Title")`;
- angle-bracket targets with spaces: `[text](<references/my notes.md>)`;
- percent-encoded targets: `[text](references/my%20notes.md)`;
- fragments on local files: `[text](references/example.md#section)` checks the file, not the anchor.

Ignored:

- `http:`, `https:`, `mailto:`, and other URL schemes;
- protocol-relative URLs such as `//example.com/path`;
- pure anchors such as `#section`;
- URLs with fragments such as `https://example.com/page#part`;
- link-like text inside fenced code blocks and inline code spans;
- links inside HTML comments.

Anchors are not resolved. Reference-style usages such as `[text][label]` are not resolved to their definitions; the definitions themselves are checked as link targets.

## Python helper checks

The linter compiles:

- every `.py` file under `scripts/`, recursively;
- every existing local `.py` link target, even outside `scripts/`.

It uses `tokenize.detect_encoding` to honor a PEP 263 coding cookie, then `compile(source, filename, "exec", dont_inherit=True)`. This parses syntax only:

- imported modules are not imported;
- module-level and function-level code is not executed;
- no `__pycache__` or `.pyc` is written;
- runtime errors, missing dependencies, and type errors are not detected.

## Secret and user-path checks

The linter scans text files in the skill, including Markdown, scripts, JSON, YAML, TOML, shell, and common source suffixes.

Secret heuristics are limited to high-confidence shapes such as AWS access key IDs, GitHub tokens, Slack tokens, Google API keys, OpenAI-style keys, PEM private-key headers, and JSON Web Tokens. The matched value is never included in the diagnostic.

Absolute user paths are reported for shapes such as a Windows drive path under the users directory and POSIX home or users paths. Common documentation placeholders (`user`, `username`, `name`, `example`, `public`, `shared`, and similar) are ignored. Use placeholders such as `$HOME`, `%USERPROFILE%`, `<name>`, or a project-relative path instead of a real account name.

These checks are heuristics. They can miss unusual credentials and can still flag an intentional example. Review each finding.

## Output

Text output groups diagnostics under each skill and ends with a summary. `--json` emits a single object:

```json
{
  "tool": "skill-linter",
  "version": 1,
  "inputs": ["path/to/input"],
  "skills": [
    {
      "path": "path/to/skill",
      "name": "skill-name",
      "description_chars": 123,
      "status": "pass",
      "diagnostics": []
    }
  ],
  "diagnostics": [],
  "summary": {
    "skills": 1,
    "errors": 0,
    "warnings": 0,
    "status": "pass"
  }
}
```

Each diagnostic contains `code`, `severity`, `message`, `path`, and optionally `line` and `skill`. The top-level `diagnostics` array flattens skill diagnostics and input-path diagnostics.

## Read-only and side-effect limits

The checker never writes to the inspected tree. It does not:

- edit `SKILL.md` or supporting files;
- create caches or bytecode;
- execute scripts from inspected skills;
- import inspected modules;
- fetch remote links;
- install dependencies;
- validate link anchors or rendered Markdown.

Run it in CI or before install when packaging correctness matters. It is not a substitute for reading the skill's instructions or testing its scripts.
