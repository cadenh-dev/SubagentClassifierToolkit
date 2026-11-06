#!/usr/bin/env python3
"""Read-only linter for Agent Skills.

The linter checks a skill folder, or a directory tree containing skill folders,
without executing inspected code, importing inspected modules, fetching remote
links, or writing any files.

Usage:
  python3 lint_skills.py .opencode/skills/skill-linter
  python3 lint_skills.py .opencode/skills
  python3 lint_skills.py . --json
  python3 lint_skills.py .opencode/skills --strict

Exit codes:
  0  no errors (warnings allowed unless --strict)
  1  one or more errors, or warnings with --strict
  2  invalid command-line usage

The frontmatter reader is deliberately a small YAML subset, not a YAML
implementation. Unsupported constructs are reported with a diagnostic instead
of being silently misinterpreted.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import tokenize
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence
from urllib.parse import unquote

TOOL = "skill-linter"
SCHEMA_VERSION = 1

NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
NAME_MAX = 64
DESCRIPTION_MAX = 1024

SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    "vendor",
    ".venv",
    "venv",
    "env",
}

MARKDOWN_SUFFIXES = {".md", ".markdown"}
TEXT_SUFFIXES = MARKDOWN_SUFFIXES | {
    ".py",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".txt",
    ".sh",
    ".bash",
    ".ps1",
    ".bat",
    ".cmd",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".jsx",
    ".ini",
    ".cfg",
}

# High-confidence token shapes only. The linter does not try to detect generic
# passwords or short test keys.
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("OpenAI-style key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----")),
    (
        "JSON Web Token",
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    ),
)

USER_PATH_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "Windows user path",
        re.compile(r"(?i)(?<![\w/:])[A-Z]:[\\/]Users[\\/]([^\\/\s\"'<>]+)"),
    ),
    (
        "POSIX user path",
        re.compile(r"(?<![\w./:])/(?:home|Users)/([^/\s\"'<>]+)"),
    ),
)

# Common documentation placeholders are not treated as leaked personal paths.
USER_PATH_PLACEHOLDERS = {
    "all",
    "default",
    "example",
    "me",
    "name",
    "public",
    "shared",
    "someone",
    "user",
    "username",
    "you",
    "yourname",
}

BLOCK_HEADER = re.compile(r"^([|>])(?:(?:([+-])([1-9])?)|(?:([1-9])([+-])?))?$")
TOP_LEVEL_KEY = re.compile(r"^([A-Za-z0-9_.-]+):(?:[ \t]*(.*))?$")
NESTED_KEY = re.compile(r"^([A-Za-z0-9_.-]+):(?:[ \t]*(.*))?$")
INLINE_LINK = re.compile(r"(?<!\\)!?\[[^\]\n]*\]\(")
REFERENCE_DEFINITION = re.compile(r"^[ \t]{0,3}\[([^\]]+)\]:[ \t]*(.*)$")


class UnsupportedYaml(ValueError):
    """A YAML feature the small frontmatter reader does not implement."""


UNSUPPORTED = object()


@dataclass
class Diagnostic:
    code: str
    message: str
    path: Path
    line: int | None = None
    severity: str = "error"

    def as_dict(self, skill: Path | None = None) -> dict[str, Any]:
        data: dict[str, Any] = {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "path": str(self.path),
        }
        if self.line is not None:
            data["line"] = self.line
        if skill is not None:
            data["skill"] = str(skill)
        return data


@dataclass
class SkillResult:
    path: Path
    name: str | None = None
    description_chars: int | None = None
    diagnostics: list[Diagnostic] = field(default_factory=list)

    def errors(self) -> int:
        return sum(1 for item in self.diagnostics if item.severity == "error")

    def warnings(self) -> int:
        return sum(1 for item in self.diagnostics if item.severity == "warning")

    def status(self, strict: bool = False) -> str:
        return "fail" if self.errors() or (strict and self.warnings()) else "pass"


def _line_number(frontmatter_start: int, index: int) -> int:
    """Return a 1-based file line number for a frontmatter-relative line."""
    return frontmatter_start + index + 1


def _leading_spaces(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _blank_span(text: str) -> str:
    return "".join("\n" if char == "\n" else " " for char in text)


def _strip_yaml_comment(value: str) -> str:
    """Strip a YAML comment while respecting single and double quotes."""
    quote: str | None = None
    index = 0
    while index < len(value):
        char = value[index]
        if quote == "'":
            if char == "'":
                if index + 1 < len(value) and value[index + 1] == "'":
                    index += 2
                    continue
                quote = None
        elif quote == '"':
            if char == "\\":
                index += 2
                continue
            if char == '"':
                quote = None
        else:
            if char in ("'", '"'):
                quote = char
            elif char == "#" and (index == 0 or value[index - 1].isspace()):
                return value[:index].rstrip()
        index += 1
    return value.rstrip()


def _unescape_double_quoted(value: str) -> str:
    escapes = {
        "0": "\0",
        "a": "\a",
        "b": "\b",
        "t": "\t",
        "n": "\n",
        "v": "\v",
        "f": "\f",
        "r": "\r",
        "e": "\x1b",
        " ": " ",
        '"': '"',
        "/": "/",
        "\\": "\\",
        "N": "\x85",
        "_": "\xa0",
        "L": "\u2028",
        "P": "\u2029",
    }
    output: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char != "\\":
            output.append(char)
            index += 1
            continue
        if index + 1 >= len(value):
            raise UnsupportedYaml("a double-quoted scalar ends with a lone backslash")
        escape = value[index + 1]
        if escape in escapes:
            output.append(escapes[escape])
            index += 2
            continue
        if escape in ("x", "u", "U"):
            width = {"x": 2, "u": 4, "U": 8}[escape]
            digits = value[index + 2 : index + 2 + width]
            if len(digits) != width or not re.fullmatch(r"[0-9A-Fa-f]+", digits):
                raise UnsupportedYaml(f"unsupported \\{escape} escape in a double-quoted scalar")
            output.append(chr(int(digits, 16)))
            index += 2 + width
            continue
        raise UnsupportedYaml(f"unsupported escape \\\\{escape} in a double-quoted scalar")
    return "".join(output)


def _parse_scalar(raw: str) -> str:
    value = _strip_yaml_comment(raw).strip()
    if not value:
        return ""
    if value[0] in ("&", "*", "!"):
        raise UnsupportedYaml("anchors, aliases, and tags are not supported")
    if value[0] in ("[", "{"):
        raise UnsupportedYaml("flow collections are not supported; use block YAML")
    if value[0] in ("|", ">"):
        raise UnsupportedYaml("a block scalar header must be the whole value")
    if value[0] == "'":
        if len(value) >= 2 and value.endswith("'"):
            return value[1:-1].replace("''", "'")
        raise UnsupportedYaml("multi-line single-quoted scalars are not supported")
    if value[0] == '"':
        if len(value) >= 2 and value.endswith('"'):
            return _unescape_double_quoted(value[1:-1])
        raise UnsupportedYaml("multi-line double-quoted scalars are not supported")
    return value


def _folded_text(lines: list[str]) -> str:
    """Approximate YAML folded style for ordinary prose descriptions."""
    output: list[str] = []
    blank_run = 0
    for line in lines:
        if line == "":
            blank_run += 1
            continue
        if output:
            if blank_run:
                output.append("\n" * blank_run)
            else:
                output.append(" ")
        output.append(line)
        blank_run = 0
    return "".join(output)


def _parse_block(
    lines: list[str],
    start: int,
    parent_indent: int,
    style: str,
    chomp: str,
    indent_hint: int | None,
) -> tuple[str, int]:
    content_lines: list[str] = []
    index = start
    content_indent = parent_indent + indent_hint if indent_hint else None

    while index < len(lines):
        line = lines[index]
        if line.strip() == "":
            content_lines.append("")
            index += 1
            continue
        indent = _leading_spaces(line)
        if indent <= parent_indent:
            break
        if content_indent is None:
            content_indent = indent
        if indent < content_indent:
            break
        content_lines.append(line[content_indent:])
        index += 1

    trailing_blank_lines = 0
    while content_lines and content_lines[-1] == "":
        content_lines.pop()
        trailing_blank_lines += 1

    if style == "|":
        content = "\n".join(content_lines)
    else:
        content = _folded_text(content_lines)

    if chomp == "-":
        pass
    elif chomp == "+":
        if content:
            content += "\n" * (trailing_blank_lines + 1)
        else:
            content = "\n" * trailing_blank_lines
    elif content:
        content += "\n"

    return content, index


def parse_frontmatter(text: str, path: Path) -> tuple[dict[str, Any], list[Diagnostic]]:
    """Parse the supported frontmatter subset and report everything else."""
    lines = text.splitlines()
    diagnostics: list[Diagnostic] = []
    if not lines or lines[0].rstrip() != "---":
        return {}, [
            Diagnostic(
                "frontmatter.missing",
                "SKILL.md must start with a YAML frontmatter block delimited by ---",
                path,
                1,
            )
        ]

    closing_index: int | None = None
    for index in range(1, len(lines)):
        if lines[index].rstrip() == "---":
            closing_index = index
            break
    if closing_index is None:
        return {}, [
            Diagnostic(
                "frontmatter.unclosed",
                "frontmatter is missing its closing --- delimiter",
                path,
                1,
            )
        ]

    frontmatter_start = 1
    frontmatter_lines = lines[frontmatter_start:closing_index]
    values: dict[str, Any] = {}
    pending_section: str | None = None
    index = 0

    while index < len(frontmatter_lines):
        line = frontmatter_lines[index]
        line_number = _line_number(frontmatter_start, index)
        if line.strip() == "" or line.lstrip().startswith("#"):
            index += 1
            continue

        leading = line[: len(line) - len(line.lstrip())]
        if "\t" in leading:
            diagnostics.append(
                Diagnostic(
                    "frontmatter.unsupported",
                    "tab indentation is not supported in frontmatter; use spaces",
                    path,
                    line_number,
                )
            )
            index += 1
            continue

        indent = _leading_spaces(line)
        stripped = line[indent:]

        if indent == 0:
            pending_section = None
            if (
                stripped == "-"
                or stripped.startswith(("- ", "-\t", "? ", "%", "..."))
            ):
                diagnostics.append(
                    Diagnostic(
                        "frontmatter.unsupported",
                        "YAML sequences, explicit keys, directives, and document "
                        "markers are not supported",
                        path,
                        line_number,
                    )
                )
                index += 1
                continue
            match = TOP_LEVEL_KEY.match(stripped)
            if not match:
                diagnostics.append(
                    Diagnostic(
                        "frontmatter.malformed",
                        f"expected a top-level 'key: value' line, got {stripped[:60]!r}",
                        path,
                        line_number,
                    )
                )
                index += 1
                continue

            key = match.group(1)
            raw = (match.group(2) or "").strip()
            raw_without_comment = _strip_yaml_comment(raw).strip()
            if key in values:
                diagnostics.append(
                    Diagnostic(
                        "frontmatter.duplicate_key",
                        f"frontmatter key {key!r} is defined more than once",
                        path,
                        line_number,
                    )
                )

            if raw_without_comment == "":
                values.setdefault(key, "")
                pending_section = key
                index += 1
                continue

            block_match = BLOCK_HEADER.fullmatch(raw_without_comment)
            if block_match:
                style = block_match.group(1)
                chomp = block_match.group(2) or block_match.group(5) or ""
                indent_hint = (
                    int(block_match.group(3))
                    if block_match.group(3)
                    else int(block_match.group(4)) if block_match.group(4) else None
                )
                content, index = _parse_block(
                    frontmatter_lines,
                    index + 1,
                    indent,
                    style,
                    chomp,
                    indent_hint,
                )
                if key not in values:
                    values[key] = content
                continue

            try:
                parsed = _parse_scalar(raw)
            except UnsupportedYaml as error:
                severity = "error" if key in {"name", "description"} else "warning"
                diagnostics.append(
                    Diagnostic(
                        "frontmatter.unsupported",
                        f"unsupported YAML for {key!r}: {error}",
                        path,
                        line_number,
                        severity,
                    )
                )
                if key not in values:
                    values[key] = UNSUPPORTED
                index += 1
                continue

            if key not in values:
                values[key] = parsed
            index += 1
            continue

        # Indented lines are accepted only as a nested mapping after 'key:'.
        if pending_section is None:
            diagnostics.append(
                Diagnostic(
                    "frontmatter.unsupported",
                    "multi-line plain scalars are not supported; use a '|' or '>' block scalar",
                    path,
                    line_number,
                )
            )
            index += 1
            continue

        nested_match = NESTED_KEY.match(stripped)
        if not nested_match:
            diagnostics.append(
                Diagnostic(
                    "frontmatter.unsupported",
                    f"unsupported nested YAML in {pending_section!r}: {stripped[:60]!r}",
                    path,
                    line_number,
                )
            )
            index += 1
            continue

        nested_raw = (nested_match.group(2) or "").strip()
        nested_without_comment = _strip_yaml_comment(nested_raw).strip()
        if nested_without_comment and (
            nested_without_comment[0] in ("&", "*", "!", "[", "{")
        ):
            diagnostics.append(
                Diagnostic(
                    "frontmatter.unsupported",
                    f"unsupported YAML in nested key {nested_match.group(1)!r}; "
                    "anchors, aliases, tags, and flow collections are not supported",
                    path,
                    line_number,
                    "warning",
                )
            )
        nested_block = BLOCK_HEADER.fullmatch(nested_without_comment)
        if nested_block:
            style = nested_block.group(1)
            chomp = (
                nested_block.group(2) or nested_block.group(5) or ""
            )
            indent_hint = (
                int(nested_block.group(3))
                if nested_block.group(3)
                else int(nested_block.group(4)) if nested_block.group(4) else None
            )
            _, index = _parse_block(
                frontmatter_lines,
                index + 1,
                indent,
                style,
                chomp,
                indent_hint,
            )
            continue
        index += 1

    return values, diagnostics


def _validate_metadata(
    values: dict[str, Any],
    skill_dir: Path,
    skill_md: Path,
    diagnostics: list[Diagnostic],
) -> tuple[str | None, int | None]:
    name_value = values.get("name", UNSUPPORTED)
    description_value = values.get("description", UNSUPPORTED)
    name: str | None = None
    description_chars: int | None = None

    if "name" not in values:
        diagnostics.append(
            Diagnostic("field.missing", "frontmatter is missing required field 'name'", skill_md, 1)
        )
    elif name_value is not UNSUPPORTED:
        if not isinstance(name_value, str) or not name_value.strip():
            diagnostics.append(
                Diagnostic("field.empty", "frontmatter field 'name' must not be empty", skill_md, 1)
            )
        else:
            name = name_value.strip()
            if len(name) > NAME_MAX:
                diagnostics.append(
                    Diagnostic(
                        "name.too_long",
                        f"name is {len(name)} characters; the limit is {NAME_MAX}",
                        skill_md,
                        1,
                    )
                )
            if not NAME_PATTERN.fullmatch(name):
                diagnostics.append(
                    Diagnostic(
                        "name.invalid",
                        "name must use lowercase letters, digits, and single hyphens "
                        "(for example: my-skill)",
                        skill_md,
                        1,
                    )
                )
            if name != skill_dir.name:
                diagnostics.append(
                    Diagnostic(
                        "name.directory_mismatch",
                        f"frontmatter name {name!r} does not match directory {skill_dir.name!r}",
                        skill_md,
                        1,
                    )
                )

    if "description" not in values:
        diagnostics.append(
            Diagnostic(
                "field.missing",
                "frontmatter is missing required field 'description'",
                skill_md,
                1,
            )
        )
    elif description_value is not UNSUPPORTED:
        if not isinstance(description_value, str) or not description_value.strip():
            diagnostics.append(
                Diagnostic(
                    "field.empty",
                    "frontmatter field 'description' must not be empty",
                    skill_md,
                    1,
                )
            )
        else:
            description = description_value.strip()
            description_chars = len(description)
            if description_chars > DESCRIPTION_MAX:
                diagnostics.append(
                    Diagnostic(
                        "description.too_long",
                        f"description is {description_chars} characters; "
                        f"the limit is {DESCRIPTION_MAX}",
                        skill_md,
                        1,
                    )
                )

    return name, description_chars


def _strip_fenced_code(text: str) -> list[str]:
    """Blank fenced code blocks while preserving line numbering."""
    output: list[str] = []
    fence_char: str | None = None
    fence_length = 0

    for line in text.splitlines():
        if fence_char is None:
            match = re.match(r"^[ \t]{0,3}(`{3,}|~{3,})", line)
            if match:
                marker = match.group(1)
                fence_char = marker[0]
                fence_length = len(marker)
                output.append("")
            else:
                output.append(line)
        else:
            candidate = re.match(
                r"^[ \t]{0,3}(" + re.escape(fence_char) + r"{3,})[ \t]*$", line
            )
            if candidate and len(candidate.group(1)) >= fence_length:
                fence_char = None
                fence_length = 0
            output.append("")

    return output


def _strip_inline_code(line: str) -> str:
    """Blank inline code spans while keeping the surrounding text intact."""
    characters = list(line)
    index = 0
    while index < len(line):
        if line[index] != "`":
            index += 1
            continue
        run_end = index
        while run_end < len(line) and line[run_end] == "`":
            run_end += 1
        run_length = run_end - index

        closing_end: int | None = None
        cursor = run_end
        while cursor < len(line):
            if line[cursor] == "`":
                candidate_end = cursor
                while candidate_end < len(line) and line[candidate_end] == "`":
                    candidate_end += 1
                if candidate_end - cursor == run_length:
                    closing_end = candidate_end
                    break
                cursor = candidate_end
            else:
                cursor += 1

        if closing_end is None:
            index = run_end
        else:
            for position in range(index, closing_end):
                characters[position] = " "
            index = closing_end

    return "".join(characters)


def _find_closing_parenthesis(text: str, start: int) -> int | None:
    depth = 1
    index = start
    in_angle = False
    quote: str | None = None

    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = None
        elif in_angle:
            if char == ">":
                in_angle = False
        elif char == "<" and not text[start:index].strip():
            in_angle = True
        elif (
            char in ("'", '"')
            and index > start
            and text[index - 1].isspace()
        ):
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
        index += 1

    return None


def _extract_target(raw: str) -> str | None:
    value = raw.strip()
    if not value:
        return ""
    if value.startswith("<"):
        closing = value.find(">")
        if closing < 0:
            return None
        return value[1:closing]
    return value.split()[0]


def _iter_link_targets(text: str) -> Iterator[tuple[int, str]]:
    """Yield (line, target) for inline links, images, and reference definitions."""
    cleaned_text = re.sub(r"<!--.*?-->", lambda match: _blank_span(match.group(0)), text, flags=re.S)
    for line_number, line in enumerate(_strip_fenced_code(cleaned_text), 1):
        cleaned_line = _strip_inline_code(line)
        for match in INLINE_LINK.finditer(cleaned_line):
            closing = _find_closing_parenthesis(cleaned_line, match.end())
            if closing is None:
                continue
            target = _extract_target(cleaned_line[match.end() : closing])
            if target is not None:
                yield line_number, target

        definition = REFERENCE_DEFINITION.match(cleaned_line)
        if definition:
            target = _extract_target(definition.group(2))
            if target is not None:
                yield line_number, target


def _looks_external(target: str) -> bool:
    if target.startswith("//"):
        return True
    if re.match(r"^[A-Za-z]:[\\/]", target):
        return False
    return bool(re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", target))


def _looks_absolute(target: str) -> bool:
    return target.startswith(("/", "\\")) or bool(
        re.match(r"^[A-Za-z]:[\\/]", target)
    )


def _read_text(path: Path) -> tuple[str | None, Diagnostic | None]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        return None, Diagnostic(
            "file.read_error", f"could not read file: {error}", path, 1
        )
    try:
        return raw.decode("utf-8-sig"), None
    except UnicodeDecodeError as error:
        return None, Diagnostic(
            "file.encoding", f"file is not valid UTF-8: {error}", path, 1
        )


def _check_links(
    skill_dir: Path,
    markdown_files: Sequence[Path],
    diagnostics: list[Diagnostic],
    linked_python: set[Path],
) -> None:
    skill_root = skill_dir.resolve()
    for markdown_file in markdown_files:
        text, error = _read_text(markdown_file)
        if error:
            diagnostics.append(error)
            continue

        for line_number, target in _iter_link_targets(text):
            if not target:
                diagnostics.append(
                    Diagnostic(
                        "link.empty",
                        "Markdown link has an empty target",
                        markdown_file,
                        line_number,
                    )
                )
                continue
            if target.startswith("#"):
                continue

            target_without_fragment = target.split("#", 1)[0]
            if not target_without_fragment:
                continue
            decoded_target = unquote(target_without_fragment)
            if decoded_target.startswith("#") or _looks_external(decoded_target):
                continue
            if _looks_absolute(decoded_target):
                diagnostics.append(
                    Diagnostic(
                        "link.absolute",
                        f"local link uses an absolute path, which is not portable: {target}",
                        markdown_file,
                        line_number,
                        "warning",
                    )
                )
                continue

            candidate = (markdown_file.parent / decoded_target).resolve()
            if not candidate.exists():
                diagnostics.append(
                    Diagnostic(
                        "link.missing",
                        f"local link target not found: {target}",
                        markdown_file,
                        line_number,
                    )
                )
                continue

            try:
                candidate.relative_to(skill_root)
            except ValueError:
                diagnostics.append(
                    Diagnostic(
                        "link.outside_skill",
                        f"local link points outside the skill folder: {target}",
                        markdown_file,
                        line_number,
                        "warning",
                    )
                )

            if candidate.is_file() and candidate.suffix.lower() == ".py":
                linked_python.add(candidate)


def _check_python(path: Path, diagnostics: list[Diagnostic]) -> None:
    try:
        raw = path.read_bytes()
    except OSError as error:
        diagnostics.append(
            Diagnostic("script.read_error", f"could not read script: {error}", path, 1)
        )
        return

    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
        source = raw.decode(encoding)
    except (SyntaxError, UnicodeDecodeError) as error:
        diagnostics.append(
            Diagnostic("script.encoding", f"could not decode script: {error}", path, 1)
        )
        return

    try:
        # compile() parses without importing the module or executing its code.
        compile(source, str(path), "exec", dont_inherit=True)
    except SyntaxError as error:
        diagnostics.append(
            Diagnostic(
                "script.syntax",
                f"Python syntax error: {error.msg}",
                path,
                error.lineno or 1,
            )
        )
    except ValueError as error:
        diagnostics.append(
            Diagnostic("script.syntax", f"Python syntax error: {error}", path, 1)
        )


def _text_files(skill_dir: Path) -> list[Path]:
    files: list[Path] = []
    for path in skill_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() in TEXT_SUFFIXES:
            files.append(path)
    return sorted(files, key=lambda item: str(item).lower())


def _scan_sensitive_content(path: Path, diagnostics: list[Diagnostic]) -> None:
    text, error = _read_text(path)
    if error:
        # The file was already read for another check in most cases. Do not
        # duplicate read failures during the sensitive-content scan.
        return

    for line_number, line in enumerate(text.splitlines(), 1):
        for label, pattern in SECRET_PATTERNS:
            if pattern.search(line):
                diagnostics.append(
                    Diagnostic(
                        "secret.suspected",
                        f"possible {label} detected; remove it or use a placeholder",
                        path,
                        line_number,
                    )
                )
                break

        for label, pattern in USER_PATH_PATTERNS:
            matched = False
            for match in pattern.finditer(line):
                user_name = match.group(1).strip()
                if user_name.lower() in USER_PATH_PLACEHOLDERS:
                    continue
                diagnostics.append(
                    Diagnostic(
                        "path.absolute_user",
                        f"absolute {label} detected; replace it with a portable placeholder",
                        path,
                        line_number,
                    )
                )
                matched = True
                break
            if matched:
                break


def _deduplicate(diagnostics: Iterable[Diagnostic]) -> list[Diagnostic]:
    unique: dict[tuple[str, str, str, int | None, str], Diagnostic] = {}
    for item in diagnostics:
        key = (item.code, item.severity, str(item.path), item.line, item.message)
        unique.setdefault(key, item)
    return sorted(
        unique.values(),
        key=lambda item: (
            str(item.path).lower(),
            item.line or 0,
            item.code,
            item.message,
        ),
    )


def lint_skill(skill_dir: Path) -> SkillResult:
    skill_dir = skill_dir.resolve()
    result = SkillResult(path=skill_dir)
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        result.diagnostics.append(
            Diagnostic(
                "skill.missing_skill_md",
                "skill folder has no SKILL.md",
                skill_md,
                1,
            )
        )
        return result

    text, error = _read_text(skill_md)
    if error:
        result.diagnostics.append(error)
        return result

    values, frontmatter_diagnostics = parse_frontmatter(text, skill_md)
    result.diagnostics.extend(frontmatter_diagnostics)
    structural_frontmatter_error = any(
        item.code in {"frontmatter.missing", "frontmatter.unclosed"}
        for item in frontmatter_diagnostics
    )
    if not structural_frontmatter_error:
        result.name, result.description_chars = _validate_metadata(
            values, skill_dir, skill_md, result.diagnostics
        )

    markdown_files = sorted(
        (path for path in skill_dir.rglob("*") if path.is_file() and path.suffix.lower() in MARKDOWN_SUFFIXES),
        key=lambda item: str(item).lower(),
    )
    linked_python: set[Path] = set()
    _check_links(skill_dir, markdown_files, result.diagnostics, linked_python)

    scripts_dir = skill_dir / "scripts"
    python_files: set[Path] = set()
    if scripts_dir.is_dir():
        python_files.update(
            path.resolve()
            for path in scripts_dir.rglob("*.py")
            if path.is_file()
        )
    python_files.update(linked_python)
    for python_file in sorted(python_files, key=lambda item: str(item).lower()):
        _check_python(python_file, result.diagnostics)

    for text_file in _text_files(skill_dir):
        _scan_sensitive_content(text_file, result.diagnostics)

    result.diagnostics = _deduplicate(result.diagnostics)
    return result


def _discover_skills(path: Path) -> tuple[list[Path], list[Diagnostic]]:
    diagnostics: list[Diagnostic] = []
    resolved = path.resolve()
    if not resolved.exists():
        return [], [
            Diagnostic("path.not_found", f"path does not exist: {path}", resolved, 1)
        ]

    if resolved.is_file():
        if resolved.name == "SKILL.md":
            return [resolved.parent], diagnostics
        return [], [
            Diagnostic(
                "path.not_skill",
                "path is a file, but not a SKILL.md; pass a skill folder or a directory containing skills",
                resolved,
                1,
            )
        ]

    if (resolved / "SKILL.md").is_file():
        return [resolved], diagnostics

    found: list[Path] = []
    for directory, dirnames, filenames in os.walk(resolved):
        dirnames[:] = sorted(
            name for name in dirnames if name not in SKIP_DIRS
        )
        if "SKILL.md" in filenames:
            found.append(Path(directory).resolve())
            dirnames[:] = []

    if not found:
        return [], [
            Diagnostic(
                "root.no_skills",
                "no SKILL.md found under this path",
                resolved,
                1,
            )
        ]

    unique: list[Path] = []
    seen: set[Path] = set()
    for item in found:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return sorted(unique, key=lambda item: str(item).lower()), diagnostics


def _format_diagnostic(diagnostic: Diagnostic) -> str:
    location = str(diagnostic.path)
    if diagnostic.line is not None:
        location = f"{location}:{diagnostic.line}"
    label = diagnostic.severity.upper()
    return f"  {label} [{diagnostic.code}] {location}: {diagnostic.message}"


def _write_text_report(
    results: Sequence[SkillResult],
    root_diagnostics: Sequence[Diagnostic],
    strict: bool,
) -> None:
    total_errors = sum(item.errors() for item in results) + sum(
        1 for item in root_diagnostics if item.severity == "error"
    )
    total_warnings = sum(item.warnings() for item in results) + sum(
        1 for item in root_diagnostics if item.severity == "warning"
    )

    print(f"{TOOL}: checked {len(results)} skill(s)")
    for diagnostic in root_diagnostics:
        print(_format_diagnostic(diagnostic))
    for result in results:
        if result.errors() or (strict and result.warnings()):
            label = "FAIL"
        elif result.warnings():
            label = "WARN"
        else:
            label = "PASS"
        print(f"{label} {result.path}")
        for diagnostic in result.diagnostics:
            print(_format_diagnostic(diagnostic))
    print(
        f"Summary: {len(results)} skill(s), {total_errors} error(s), "
        f"{total_warnings} warning(s)"
    )


def _json_payload(
    results: Sequence[SkillResult],
    root_diagnostics: Sequence[Diagnostic],
    inputs: Sequence[Path],
    strict: bool,
) -> dict[str, Any]:
    skill_diagnostics = [
        diagnostic.as_dict(skill=result.path)
        for result in results
        for diagnostic in result.diagnostics
    ]
    root_items = [diagnostic.as_dict() for diagnostic in root_diagnostics]
    errors = sum(item.errors() for item in results) + sum(
        1 for item in root_diagnostics if item.severity == "error"
    )
    warnings = sum(item.warnings() for item in results) + sum(
        1 for item in root_diagnostics if item.severity == "warning"
    )
    failed = errors > 0 or (strict and warnings > 0)
    return {
        "tool": TOOL,
        "version": SCHEMA_VERSION,
        "inputs": [str(item) for item in inputs],
        "skills": [
            {
                "path": str(result.path),
                "name": result.name,
                "description_chars": result.description_chars,
                "status": result.status(strict),
                "diagnostics": [
                    diagnostic.as_dict(skill=result.path)
                    for diagnostic in result.diagnostics
                ],
            }
            for result in results
        ],
        "diagnostics": skill_diagnostics + root_items,
        "summary": {
            "skills": len(results),
            "errors": errors,
            "warnings": warnings,
            "status": "fail" if failed else "pass",
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only linter for Agent Skills.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "PATH may be a skill folder containing SKILL.md or a directory tree "
            "containing skill folders. Defaults to the current directory.\n\n"
            "Exit codes: 0 clean, 1 diagnostics, 2 usage error."
        ),
    )
    parser.add_argument(
        "paths",
        nargs="*",
        metavar="PATH",
        help="skill folder or root containing skills (default: .)",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="treat warnings as failures (exit 1)",
    )
    parser.add_argument("--version", action="version", version=f"{TOOL} {SCHEMA_VERSION}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    inputs = [Path(item) for item in (args.paths or ["."])]
    root_diagnostics: list[Diagnostic] = []
    skill_paths: list[Path] = []
    seen: set[Path] = set()

    for input_path in inputs:
        discovered, errors = _discover_skills(input_path)
        root_diagnostics.extend(errors)
        for skill_path in discovered:
            if skill_path not in seen:
                seen.add(skill_path)
                skill_paths.append(skill_path)

    results = [lint_skill(skill_path) for skill_path in skill_paths]
    root_diagnostics = _deduplicate(root_diagnostics)
    total_errors = sum(item.errors() for item in results) + sum(
        1 for item in root_diagnostics if item.severity == "error"
    )
    total_warnings = sum(item.warnings() for item in results) + sum(
        1 for item in root_diagnostics if item.severity == "warning"
    )
    failed = total_errors > 0 or (args.strict and total_warnings > 0)

    if args.json:
        json.dump(
            _json_payload(results, root_diagnostics, inputs, args.strict),
            sys.stdout,
            indent=2,
            sort_keys=False,
        )
        sys.stdout.write("\n")
    else:
        _write_text_report(results, root_diagnostics, args.strict)

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
