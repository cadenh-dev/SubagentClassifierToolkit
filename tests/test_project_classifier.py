#!/usr/bin/env python3
"""Tests for the project-classifier builder."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT_SKILL = ROOT / ".opencode" / "skills" / "project-classifier"
BUILDER = PROJECT_SKILL / "scripts" / "build_project_classifier.py"


def run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(BUILDER), *args],
        text=True,
        capture_output=True,
        cwd=str(ROOT),
        env=env,
    )


def write(root: Path, rel: str, content: str = "") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def python_runtime_command(profile: dict) -> str:
    for item in profile.get("languages", []):
        if item.get("name") == "python" and item.get("runtime_available"):
            return item.get("runtime_command") or "python3"
    return "python3"


def broken_python3(directory: Path) -> Path:
    """Create an executable named like python3 that fails a version probe."""
    if os.name == "nt":
        path = directory / "python3.cmd"
        path.write_text("@exit /b 9009\r\n", encoding="utf-8")
        return path
    path = directory / "python3"
    path.write_text("#!/bin/sh\nexit 9009\n", encoding="utf-8")
    path.chmod(0o755)
    return path


class ProjectClassifierMetadataTests(unittest.TestCase):
    def test_skill_frontmatter_and_links(self) -> None:
        text = (PROJECT_SKILL / "SKILL.md").read_text(encoding="utf-8")
        match = re.match(r"^---\n(.*?)\n---", text, re.S)
        self.assertIsNotNone(match)
        name = re.search(r"^name:\s*(.+)$", match.group(1), re.M).group(1).strip()
        description = re.search(r"^description:\s*(.+)$", match.group(1), re.M).group(1).strip()
        self.assertEqual(name, PROJECT_SKILL.name)
        self.assertLessEqual(len(description), 1024)
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
            if target.startswith(("http://", "https://", "#")):
                continue
            self.assertTrue((PROJECT_SKILL / target).exists(), target)

    def test_profile_example_is_valid_json(self) -> None:
        json.loads((PROJECT_SKILL / "assets" / "profile.example.json").read_text(encoding="utf-8"))


class ProjectClassifierTests(unittest.TestCase):
    def test_python_project(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write(root, "pyproject.toml", """
[project]
name = "sample"
dependencies = ["pytest", "ruff"]
""")
            write(root, "src/app/main.py", "def main():\n    return 1\n")
            write(root, "tests/test_main.py", "def test_main():\n    assert True\n")
            write(root, "README.md", "# sample\n")
            out = root / ".opencode" / "skills" / "sample-tool-router"
            result = run("--root", str(root), "--name", "sample-tool-router")
            self.assertEqual(result.returncode, 0, result.stderr)
            profile = json.loads((out / "data" / "project-profile.json").read_text())
            self.assertEqual(profile["primary_language"], "python")
            self.assertEqual(profile["kind"], "software")
            python_command = python_runtime_command(profile)
            self.assertIn("python", (out / "SKILL.md").read_text().lower())
            self.assertIn(f"{python_command} -m pytest", (out / "SKILL.md").read_text())

    def test_java_project(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write(root, "pom.xml", "<project><dependency>spring-boot</dependency><dependency>junit</dependency></project>")
            for i in range(3):
                write(root, f"src/main/java/com/example/App{i}.java", "class App {}\n")
            out = root / ".opencode" / "skills" / "java-tool-router"
            result = run("--root", str(root), "--name", "java-tool-router")
            self.assertEqual(result.returncode, 0, result.stderr)
            profile = json.loads((out / "data" / "project-profile.json").read_text())
            self.assertEqual(profile["primary_language"], "java")
            text = (out / "SKILL.md").read_text()
            self.assertIn("Spring Boot", text)
            self.assertIn("mvn test", text)

    def test_unittest_command_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write(root, "main.py", "x = 1\n")
            write(root, "tests/test_main.py", "import unittest\n\nclass T(unittest.TestCase):\n    pass\n")
            out = root / ".opencode" / "skills" / "unit-tool-router"
            result = run("--root", str(root), "--name", "unit-tool-router")
            self.assertEqual(result.returncode, 0, result.stderr)
            text = (out / "SKILL.md").read_text()
            profile = json.loads((out / "data" / "project-profile.json").read_text())
            self.assertIn(f"{python_runtime_command(profile)} -m unittest discover -s tests -v", text)

    def test_node_framework_and_package_manager(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write(root, "package.json", json.dumps({
                "dependencies": {"react": "^19.0.0"},
                "scripts": {"test": "vitest", "build": "vite build"},
            }))
            write(root, "pnpm-lock.yaml", "lockfileVersion: '9.0'\n")
            write(root, "src/App.tsx", "export const App = () => null;\n")
            out = root / ".opencode" / "skills" / "web-tool-router"
            result = run("--root", str(root), "--name", "web-tool-router")
            self.assertEqual(result.returncode, 0, result.stderr)
            profile = json.loads((out / "data" / "project-profile.json").read_text())
            self.assertEqual(profile["kind"], "web")
            self.assertIn("React", {f["name"] for f in profile["frameworks"]})
            text = (out / "SKILL.md").read_text()
            self.assertIn("pnpm test", text)

    def test_multiple_engine_tools_are_detected(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write(root, "scene.blend")
            write(root, "scripts/build.py", "import bpy\n")
            write(root, "project.godot", "config_version=5\n")
            write(root, "scenes/main.tscn", "[gd_scene]\n")
            write(root, "scripts/player.gd", "extends Node\n")
            out = root / ".opencode" / "skills" / "game-tool-router"
            result = run("--root", str(root), "--name", "game-tool-router")
            self.assertEqual(result.returncode, 0, result.stderr)
            profile = json.loads((out / "data" / "project-profile.json").read_text())
            tools = {tool["name"] for tool in profile["tools"]}
            self.assertIn("blender", tools)
            self.assertIn("godot", tools)
            text = (out / "SKILL.md").read_text()
            self.assertIn("Multiple toolchains", text)
            self.assertIn("blender", text.lower())
            self.assertIn("godot", text.lower())

    def test_polyglot_project_is_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write(root, "pyproject.toml", "[project]\nname='api'\n")
            write(root, "pom.xml", "<project/>")
            for i in range(4):
                write(root, f"api/app/mod{i}.py", "x = 1\n")
                write(root, f"worker/src/main/java/Worker{i}.java", "class Worker {}\n")
            out = root / ".opencode" / "skills" / "poly-tool-router"
            result = run("--root", str(root), "--name", "poly-tool-router")
            self.assertEqual(result.returncode, 0, result.stderr)
            profile = json.loads((out / "data" / "project-profile.json").read_text())
            self.assertTrue(profile["ambiguous"])
            text = (out / "SKILL.md").read_text()
            self.assertIn("Multiple substantial languages", text)
            self.assertIn("Multiple substantial languages", text)

    def test_empty_project_is_low_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            out = root / ".opencode" / "skills" / "empty-tool-router"
            result = run("--root", str(root), "--name", "empty-tool-router")
            self.assertEqual(result.returncode, 0, result.stderr)
            profile = json.loads((out / "data" / "project-profile.json").read_text())
            self.assertIsNone(profile["primary_language"])
            self.assertTrue(profile["warnings"])
            self.assertIn("No confident toolchain", (out / "SKILL.md").read_text())

    def test_overwrite_requires_force(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write(root, "main.py", "x = 1\n")
            out = root / ".opencode" / "skills" / "x-tool-router"
            self.assertEqual(run("--root", str(root), "--name", "x-tool-router").returncode, 0)
            blocked = run("--root", str(root), "--name", "x-tool-router")
            self.assertEqual(blocked.returncode, 2)
            forced = run("--root", str(root), "--name", "x-tool-router", "--force")
            self.assertEqual(forced.returncode, 0, forced.stderr)

    def test_refuses_to_write_over_root(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write(root, "main.py", "x = 1\n")
            result = run("--root", str(root), "--name", "x-tool-router", "--out", str(root), "--force")
            self.assertEqual(result.returncode, 2)
            self.assertIn("refusing", result.stderr)

    def test_generated_skill_frontmatter_and_links(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write(root, "main.py", "x = 1\n")
            out = root / ".opencode" / "skills" / "link-tool-router"
            result = run("--root", str(root), "--name", "link-tool-router")
            self.assertEqual(result.returncode, 0, result.stderr)
            text = (out / "SKILL.md").read_text()
            match = re.match(r"^---\n(.*?)\n---", text, re.S)
            self.assertIsNotNone(match)
            name = re.search(r"^name:\s*(.+)$", match.group(1), re.M).group(1).strip()
            self.assertEqual(name, "link-tool-router")
            self.assertTrue((out / "data" / "project-profile.json").exists())
            self.assertTrue((out / "references" / "evidence.md").exists())

    def test_dry_run_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write(root, "main.py", "x = 1\n")
            out = root / ".opencode" / "skills" / "dry-tool-router"
            result = run("--root", str(root), "--name", "dry-tool-router", "--dry-run")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(out.exists())
            self.assertIn('"primary_language": "python"', result.stdout)


class ProjectClassifierRefreshTests(unittest.TestCase):
    def test_force_preserves_unrelated_files_inside_generated_skill(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write(root, "main.py", "x = 1\n")
            out = root / ".opencode" / "skills" / "keep-tool-router"
            self.assertEqual(run("--root", str(root), "--name", "keep-tool-router").returncode, 0)
            write(out, "notes.md", "keep me\n")
            write(out, "data/extra.json", '{"keep": true}\n')

            forced = run("--root", str(root), "--name", "keep-tool-router", "--force")
            self.assertEqual(forced.returncode, 0, forced.stderr)
            self.assertEqual((out / "notes.md").read_text(encoding="utf-8"), "keep me\n")
            self.assertEqual((out / "data" / "extra.json").read_text(encoding="utf-8"), '{"keep": true}\n')
            self.assertTrue((out / "SKILL.md").exists())
            self.assertTrue((out / "data" / "project-profile.json").exists())
            self.assertTrue((out / "references" / "evidence.md").exists())

    def test_repeated_legitimate_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write(root, "main.py", "x = 1\n")
            out = root / ".opencode" / "skills" / "repeat-tool-router"
            self.assertEqual(run("--root", str(root), "--name", "repeat-tool-router").returncode, 0)
            write(out, "sentinel.txt", "still here\n")
            for _ in range(2):
                result = run("--root", str(root), "--name", "repeat-tool-router", "--force")
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((out / "sentinel.txt").read_text(encoding="utf-8"), "still here\n")

    def test_force_refuses_unrelated_existing_destination(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write(root, "main.py", "x = 1\n")
            out = root / ".opencode" / "skills" / "unrelated-tool-router"
            write(out, "notes.txt", "do not delete\n")

            result = run("--root", str(root), "--name", "unrelated-tool-router", "--force")
            self.assertEqual(result.returncode, 2)
            self.assertIn("refusing", result.stderr)
            self.assertEqual((out / "notes.txt").read_text(encoding="utf-8"), "do not delete\n")
            self.assertFalse((out / "SKILL.md").exists())

    def test_force_refuses_malformed_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write(root, "main.py", "x = 1\n")
            out = root / ".opencode" / "skills" / "malformed-tool-router"
            write(out, "SKILL.md", "---\nname: malformed-tool-router\n---\n\n# Hand written\n")

            result = run("--root", str(root), "--name", "malformed-tool-router", "--force")
            self.assertEqual(result.returncode, 2)
            self.assertIn("refusing", result.stderr)
            self.assertEqual(
                (out / "SKILL.md").read_text(encoding="utf-8"),
                "---\nname: malformed-tool-router\n---\n\n# Hand written\n",
            )
            self.assertFalse((out / "data").exists())

    def test_force_refuses_output_file(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write(root, "main.py", "x = 1\n")
            out = root / ".opencode" / "skills" / "file-tool-router"
            out.parent.mkdir(parents=True)
            out.write_text("not a directory\n", encoding="utf-8")

            result = run("--root", str(root), "--name", "file-tool-router", "--force")
            self.assertEqual(result.returncode, 2)
            self.assertIn("refusing", result.stderr)
            self.assertEqual(out.read_text(encoding="utf-8"), "not a directory\n")

    def test_force_refuses_ancestor_of_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            root = base / "project"
            write(root, "main.py", "x = 1\n")
            write(base, "sentinel.txt", "keep\n")

            result = run("--root", str(root), "--name", "x-tool-router", "--out", str(base), "--force")
            self.assertEqual(result.returncode, 2)
            self.assertIn("refusing", result.stderr)
            self.assertEqual((base / "sentinel.txt").read_text(encoding="utf-8"), "keep\n")
            self.assertFalse((base / "SKILL.md").exists())
            self.assertTrue((root / "main.py").exists())

    def test_force_refuses_output_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write(root, "main.py", "x = 1\n")
            external = root / "external"
            external.mkdir()
            write(external, "sentinel.txt", "keep\n")
            out = root / ".opencode" / "skills" / "link-tool-router"
            out.parent.mkdir(parents=True)
            try:
                out.symlink_to(external, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation is not supported on this platform")

            result = run("--root", str(root), "--name", "link-tool-router", "--force")
            self.assertEqual(result.returncode, 2)
            self.assertIn("refusing", result.stderr)
            self.assertEqual((external / "sentinel.txt").read_text(encoding="utf-8"), "keep\n")
            self.assertFalse((external / "SKILL.md").exists())

    def test_force_refuses_linked_managed_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write(root, "main.py", "x = 1\n")
            out = root / ".opencode" / "skills" / "linked-artifact-tool-router"
            self.assertEqual(run("--root", str(root), "--name", "linked-artifact-tool-router").returncode, 0)
            external = root / "external-skill.md"
            shutil.copyfile(out / "SKILL.md", external)
            (out / "SKILL.md").unlink()
            try:
                (out / "SKILL.md").symlink_to(external)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation is not supported on this platform")
            original = external.read_text(encoding="utf-8")

            result = run("--root", str(root), "--name", "linked-artifact-tool-router", "--force")
            self.assertEqual(result.returncode, 2)
            self.assertIn("refusing", result.stderr)
            self.assertEqual(external.read_text(encoding="utf-8"), original)


class ProjectClassifierRuntimeTests(unittest.TestCase):
    def test_broken_python3_alias_falls_back_to_working_python(self) -> None:
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as b:
            root = Path(d)
            bin_dir = Path(b)
            write(root, "main.py", "x = 1\n")
            write(root, "tests/test_main.py", "import unittest\n\nclass T(unittest.TestCase):\n    pass\n")
            broken_python3(bin_dir)
            if os.name != "nt":
                (bin_dir / "python").symlink_to(sys.executable)
            env = os.environ.copy()
            env["PATH"] = os.pathsep.join([str(bin_dir), str(Path(sys.executable).parent)])

            result = run("--root", str(root), "--name", "runtime-tool-router", env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            profile = json.loads((root / ".opencode" / "skills" / "runtime-tool-router" / "data" / "project-profile.json").read_text())
            python = next(item for item in profile["languages"] if item["name"] == "python")
            self.assertTrue(python["runtime_on_path"])
            self.assertTrue(python["runtime_available"])
            self.assertEqual(python["runtime_command"], "python")
            self.assertTrue(python["runtime_rejected"])
            self.assertEqual(python["runtime_rejected"][0]["command"], "python3")
            commands = {item["command"] for item in profile["commands"]}
            self.assertIn("python -m unittest discover -s tests -v", commands)
            self.assertNotIn("python3 -m unittest discover -s tests -v", commands)
            text = (root / ".opencode" / "skills" / "runtime-tool-router" / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("python3 on PATH is not usable; using python", text)

    def test_broken_alias_without_fallback_is_not_usable(self) -> None:
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as b:
            root = Path(d)
            bin_dir = Path(b)
            write(root, "main.py", "x = 1\n")
            write(root, "tests/test_main.py", "import unittest\n")
            broken_python3(bin_dir)
            env = os.environ.copy()
            env["PATH"] = str(bin_dir)

            result = run("--root", str(root), "--name", "broken-tool-router", env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            profile = json.loads((root / ".opencode" / "skills" / "broken-tool-router" / "data" / "project-profile.json").read_text())
            python = next(item for item in profile["languages"] if item["name"] == "python")
            self.assertTrue(python["runtime_on_path"])
            self.assertFalse(python["runtime_available"])
            self.assertIsNone(python["runtime_command"])
            self.assertTrue(any("on PATH but no usable runtime" in warning for warning in profile["warnings"]))

    def test_no_runtime_keeps_portable_command(self) -> None:
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as b:
            root = Path(d)
            empty_bin = Path(b)
            write(root, "main.py", "x = 1\n")
            write(root, "tests/test_main.py", "import unittest\n")
            env = os.environ.copy()
            env["PATH"] = str(empty_bin)

            result = run("--root", str(root), "--name", "none-tool-router", env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            profile = json.loads((root / ".opencode" / "skills" / "none-tool-router" / "data" / "project-profile.json").read_text())
            python = next(item for item in profile["languages"] if item["name"] == "python")
            self.assertFalse(python["runtime_on_path"])
            self.assertFalse(python["runtime_available"])
            self.assertIsNone(python["runtime_command"])
            self.assertTrue(any("runtime is not on PATH" in warning for warning in profile["warnings"]))
            commands = {item["command"] for item in profile["commands"]}
            self.assertIn("python3 -m unittest discover -s tests -v", commands)


if __name__ == "__main__":
    unittest.main()
