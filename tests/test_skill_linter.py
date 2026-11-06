#!/usr/bin/env python3
"""Tests for the read-only skill linter."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINTER = ROOT / ".opencode" / "skills" / "skill-linter" / "scripts" / "lint_skills.py"


def run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(LINTER), *args],
        text=True,
        capture_output=True,
        cwd=str(cwd or ROOT),
    )


def write(root: Path, relative: str, content: str = "") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def write_skill(
    root: Path,
    name: str,
    description: str = "A valid test skill used by the linter test suite.",
    body: str = "",
    extra_frontmatter: str = "",
) -> Path:
    skill = root / name
    write(
        skill,
        "SKILL.md",
        (
            "---\n"
            f"name: {name}\n"
            f"description: {description}\n"
            f"{extra_frontmatter}"
            "---\n\n"
            f"# {name}\n\n"
            f"{body}\n"
        ),
    )
    return skill


def payload(result: subprocess.CompletedProcess[str]) -> dict:
    return json.loads(result.stdout)


def diagnostic_codes(data: dict) -> list[str]:
    return [item["code"] for item in data["diagnostics"]]


def snapshot(root: Path) -> dict[str, tuple[int, bytes]]:
    return {
        path.relative_to(root).as_posix(): (path.stat().st_mtime_ns, path.read_bytes())
        for path in root.rglob("*")
        if path.is_file()
    }


class ValidSkillTests(unittest.TestCase):
    def test_valid_existing_style_skill(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = write_skill(
                root,
                "sample-skill",
                body=(
                    "See [the guide](references/guide.md), [the helper](scripts/helper.py), "
                    "and [the image](assets/picture.png).\n\n"
                    "A local fragment [works](references/guide.md#section).\n"
                ),
                extra_frontmatter=(
                    "metadata:\n"
                    "  short-description: Sample skill for tests\n"
                ),
            )
            write(skill, "references/guide.md", "# Guide\n\nText.\n")
            write(skill, "assets/picture.png", "not really a png, but it exists\n")
            write(
                skill,
                "scripts/helper.py",
                "def add(left, right):\n    return left + right\n",
            )

            result = run(str(skill), "--json")

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            data = payload(result)
            self.assertEqual(data["summary"]["skills"], 1)
            self.assertEqual(data["summary"]["errors"], 0)
            self.assertEqual(data["summary"]["status"], "pass")
            self.assertEqual(data["skills"][0]["name"], "sample-skill")
            self.assertEqual(data["diagnostics"], [])

    def test_folded_and_literal_descriptions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write(
                root,
                "folded-skill/SKILL.md",
                (
                    "---\n"
                    "name: folded-skill\n"
                    "description: >-\n"
                    "  Validate folded descriptions without pretending\n"
                    "  this is a full YAML parser.\n"
                    "metadata:\n"
                    "  short-description: Folded description\n"
                    "---\n\n"
                    "# Folded\n"
                ),
            )
            write(
                root,
                "literal-skill/SKILL.md",
                (
                    "---\n"
                    "name: literal-skill\n"
                    "description: |\n"
                    "  Validate literal descriptions.\n"
                    "  Keep both lines.\n"
                    "---\n\n"
                    "# Literal\n"
                ),
            )

            result = run(str(root), "--json")

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            data = payload(result)
            self.assertEqual(data["summary"]["skills"], 2)
            self.assertEqual(data["summary"]["errors"], 0)
            self.assertEqual(diagnostic_codes(data), [])
            lengths = {
                item["name"]: item["description_chars"] for item in data["skills"]
            }
            self.assertGreater(lengths["folded-skill"], 20)
            self.assertGreater(lengths["literal-skill"], 20)

    def test_repository_skills_pass(self) -> None:
        result = run(str(ROOT / ".opencode" / "skills"), "--json")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = payload(result)
        self.assertGreaterEqual(data["summary"]["skills"], 3)
        self.assertEqual(data["summary"]["errors"], 0)
        self.assertEqual(data["summary"]["warnings"], 0)


class FrontmatterTests(unittest.TestCase):
    def test_missing_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "no-frontmatter"
            write(skill, "SKILL.md", "# Just a body\n")

            result = run(str(skill), "--json")

            self.assertEqual(result.returncode, 1)
            codes = diagnostic_codes(payload(result))
            self.assertIn("frontmatter.missing", codes)
            self.assertNotIn("field.missing", codes)

    def test_unclosed_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "unclosed"
            write(skill, "SKILL.md", "---\nname: unclosed\ndescription: text\n")

            result = run(str(skill), "--json")

            self.assertEqual(result.returncode, 1)
            codes = diagnostic_codes(payload(result))
            self.assertIn("frontmatter.unclosed", codes)
            self.assertNotIn("field.missing", codes)

    def test_malformed_frontmatter_line(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "malformed"
            write(
                skill,
                "SKILL.md",
                (
                    "---\n"
                    "name: malformed\n"
                    "description: A malformed test skill.\n"
                    "this is not a key value line\n"
                    "---\n\n"
                    "# Body\n"
                ),
            )

            result = run(str(skill), "--json")

            self.assertEqual(result.returncode, 1)
            self.assertIn("frontmatter.malformed", diagnostic_codes(payload(result)))

    def test_unsupported_yaml_is_reported_not_guessed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "anchored"
            write(
                skill,
                "SKILL.md",
                (
                    "---\n"
                    "name: anchored\n"
                    "description: &shared An anchored description.\n"
                    "---\n\n"
                    "# Body\n"
                ),
            )

            result = run(str(skill), "--json")

            self.assertEqual(result.returncode, 1)
            data = payload(result)
            diagnostics = [
                item
                for item in data["diagnostics"]
                if item["code"] == "frontmatter.unsupported"
            ]
            self.assertTrue(diagnostics)
            self.assertTrue(any(item["severity"] == "error" for item in diagnostics))
            self.assertNotIn("field.empty", diagnostic_codes(data))

    def test_flow_collection_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "flow-description"
            write(
                skill,
                "SKILL.md",
                (
                    "---\n"
                    "name: flow-description\n"
                    "description: [one, two]\n"
                    "---\n\n"
                    "# Body\n"
                ),
            )

            result = run(str(skill), "--json")

            self.assertEqual(result.returncode, 1)
            data = payload(result)
            self.assertIn("frontmatter.unsupported", diagnostic_codes(data))
            self.assertNotIn("field.empty", diagnostic_codes(data))


class MetadataTests(unittest.TestCase):
    def test_name_directory_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_skill(root, "wrong-directory", extra_frontmatter="")

            # Overwrite the name without overwriting the directory.
            write(
                root,
                "wrong-directory/SKILL.md",
                (
                    "---\n"
                    "name: other-name\n"
                    "description: A valid description for this skill.\n"
                    "---\n\n"
                    "# Body\n"
                ),
            )

            result = run(str(root / "wrong-directory"), "--json")

            self.assertEqual(result.returncode, 1)
            self.assertIn("name.directory_mismatch", diagnostic_codes(payload(result)))

    def test_invalid_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = "Bad_Name"
            write(
                root,
                f"{skill}/SKILL.md",
                (
                    "---\n"
                    "name: Bad_Name\n"
                    "description: A valid description for this skill.\n"
                    "---\n\n"
                    "# Body\n"
                ),
            )

            result = run(str(root / skill), "--json")

            self.assertEqual(result.returncode, 1)
            self.assertIn("name.invalid", diagnostic_codes(payload(result)))

    def test_description_length_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            at_limit = write_skill(root, "at-limit", description="x" * 1024)
            over_limit = write_skill(root, "over-limit", description="x" * 1025)

            allowed = run(str(at_limit), "--json")
            rejected = run(str(over_limit), "--json")

            self.assertEqual(allowed.returncode, 0, allowed.stdout + allowed.stderr)
            self.assertEqual(rejected.returncode, 1)
            self.assertIn("description.too_long", diagnostic_codes(payload(rejected)))


class LinksAndCodeTests(unittest.TestCase):
    def test_missing_reference_reports_path_line_and_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            skill = write_skill(
                Path(directory),
                "missing-reference",
                body="Read [the missing guide](references/does-not-exist.md).\n",
            )

            result = run(str(skill), "--json")

            self.assertEqual(result.returncode, 1)
            data = payload(result)
            missing = [
                item for item in data["diagnostics"] if item["code"] == "link.missing"
            ]
            self.assertEqual(len(missing), 1)
            self.assertTrue(missing[0]["path"].endswith("SKILL.md"))
            self.assertIsInstance(missing[0]["line"], int)

            text_result = run(str(skill))
            self.assertEqual(text_result.returncode, 1)
            self.assertIn("link.missing", text_result.stdout)
            self.assertIn("Summary:", text_result.stdout)

    def test_urls_anchors_fragments_and_code_examples_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = write_skill(
                root,
                "link-handling",
                body=(
                    "[web](https://example.com/a_(b)#fragment) and "
                    "[mail](mailto:user@example.com) and [anchor](#local).\n\n"
                    "[fragment](references/guide.md#part) and "
                    "[image](<assets/my picture.png>) and "
                    "[encoded](references/other%20notes.md).\n\n"
                    "```text\n[not checked](references/fenced-missing.md)\n```\n\n"
                    "Inline `[not checked](inline-missing.md)` is not a link.\n\n"
                    "<!-- [not checked](comment-missing.md) -->\n\n"
                    "[guide]: references/guide.md\n"
                    "[site]: https://example.com/anything\n"
                ),
            )
            write(skill, "references/guide.md", "# Guide\n")
            write(skill, "references/other notes.md", "# Other\n")
            write(skill, "assets/my picture.png", "placeholder\n")

            result = run(str(skill), "--json")

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(diagnostic_codes(payload(result)), [])

    def test_path_with_spaces(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "root with spaces"
            skill = write_skill(
                root,
                "spacey-skill",
                body=(
                    "[notes](<references/my notes.md>) and "
                    "[other](references/other%20notes.md) and "
                    "[helper](<scripts/my helper.py>).\n"
                ),
            )
            write(skill, "references/my notes.md", "# Notes\n")
            write(skill, "references/other notes.md", "# Other\n")
            write(skill, "scripts/my helper.py", "VALUE = 1\n")

            result = run(str(skill), "--json", cwd=root)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(diagnostic_codes(payload(result)), [])

    def test_reference_definition_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            skill = write_skill(
                Path(directory),
                "definition-link",
                body="[missing]: references/absent.md\n",
            )

            result = run(str(skill), "--json")

            self.assertEqual(result.returncode, 1)
            self.assertIn("link.missing", diagnostic_codes(payload(result)))


class ScriptTests(unittest.TestCase):
    def test_invalid_python_syntax(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            skill = write_skill(Path(directory), "broken-script")
            write(skill, "scripts/broken.py", "def broken(:\n    pass\n")

            result = run(str(skill), "--json")

            self.assertEqual(result.returncode, 1)
            data = payload(result)
            errors = [
                item for item in data["diagnostics"] if item["code"] == "script.syntax"
            ]
            self.assertEqual(len(errors), 1)
            self.assertTrue(errors[0]["path"].endswith("broken.py"))
            self.assertFalse((skill / "scripts" / "__pycache__").exists())

    def test_linked_script_must_exist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            skill = write_skill(
                Path(directory),
                "missing-script",
                body="Run [the helper](scripts/missing.py).\n",
            )

            result = run(str(skill), "--json")

            self.assertEqual(result.returncode, 1)
            self.assertIn("link.missing", diagnostic_codes(payload(result)))

    def test_read_only_and_no_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            skill = write_skill(
                Path(directory),
                "read-only",
                body="[helper](scripts/touch.py) and [importer](scripts/imports_missing.py).\n",
            )
            write(
                skill,
                "scripts/touch.py",
                (
                    "from pathlib import Path\n"
                    'Path(__file__).with_name("executed.marker").write_text("ran")\n'
                ),
            )
            write(skill, "scripts/imports_missing.py", "import module_that_cannot_exist_xyz\n")

            before = snapshot(skill)
            result = run(str(skill), "--json")
            after = snapshot(skill)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(before, after)
            self.assertFalse((skill / "scripts" / "executed.marker").exists())
            self.assertFalse(any(path.name == "__pycache__" for path in skill.rglob("*")))


class DiscoveryAndSensitiveContentTests(unittest.TestCase):
    def test_root_discovery_and_missing_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_skill(root, "first-skill")
            write_skill(root, "second-skill")

            found = run(str(root), "--json")
            missing = run(str(root / "does-not-exist"), "--json")
            empty = root / "empty"
            empty.mkdir()
            no_skills = run(str(empty), "--json")

            self.assertEqual(found.returncode, 0, found.stdout + found.stderr)
            self.assertEqual(payload(found)["summary"]["skills"], 2)
            self.assertEqual(missing.returncode, 1)
            self.assertIn("path.not_found", diagnostic_codes(payload(missing)))
            self.assertEqual(no_skills.returncode, 1)
            self.assertIn("root.no_skills", diagnostic_codes(payload(no_skills)))

    def test_secrets_and_absolute_user_paths_are_reported_without_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            skill = write_skill(Path(directory), "sensitive-content")
            secret = "AKIA" + ("A" * 16)
            write(
                skill,
                "references/leak.md",
                (
                    f"Token: {secret}\n"
                    r"Windows path: C:\Users\alice\project" "\n"
                    "POSIX path: /home/alice/project\n"
                    r"Placeholder: C:\Users\<name>\project" "\n"
                ),
            )

            result = run(str(skill), "--json")

            self.assertEqual(result.returncode, 1)
            data = payload(result)
            codes = diagnostic_codes(data)
            self.assertIn("secret.suspected", codes)
            self.assertIn("path.absolute_user", codes)
            messages = " ".join(item["message"] for item in data["diagnostics"])
            self.assertNotIn(secret, messages)
            self.assertNotIn("alice", messages)

    def test_strict_promotes_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            skill = write_skill(
                Path(directory),
                "warning-skill",
                body="[outside](../outside.md)\n",
            )
            write(Path(directory), "outside.md", "# Outside\n")

            normal = run(str(skill), "--json")
            strict = run(str(skill), "--json", "--strict")

            self.assertEqual(normal.returncode, 0, normal.stdout + normal.stderr)
            self.assertIn(
                "link.outside_skill", diagnostic_codes(payload(normal))
            )
            self.assertEqual(strict.returncode, 1)
            self.assertEqual(payload(strict)["summary"]["status"], "fail")


if __name__ == "__main__":
    unittest.main()
