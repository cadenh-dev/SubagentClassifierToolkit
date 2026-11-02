#!/usr/bin/env python3
"""Tests for the project-classifier builder."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT_SKILL = ROOT / ".opencode" / "skills" / "project-classifier"
BUILDER = PROJECT_SKILL / "scripts" / "build_project_classifier.py"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(BUILDER), *args], text=True, capture_output=True, cwd=str(ROOT))


def write(root: Path, rel: str, content: str = "") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


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
            self.assertIn("python", (out / "SKILL.md").read_text().lower())
            self.assertIn("python3 -m pytest", (out / "SKILL.md").read_text())

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
            self.assertIn("python3 -m unittest discover -s tests -v", text)

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


if __name__ == "__main__":
    unittest.main()
