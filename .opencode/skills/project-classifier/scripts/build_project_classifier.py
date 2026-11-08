#!/usr/bin/env python3
"""Build a project-local tool router skill from repository evidence.

The builder is intentionally conservative:
- it detects languages from file extensions and manifests,
- distinguishes "the project uses this" from "the binary is installed",
- records evidence and confidence,
- emits a project-scoped SKILL.md plus a machine-readable profile.

Usage:
  python3 build_project_classifier.py --root .
  python3 build_project_classifier.py --root . --dry-run
  python3 build_project_classifier.py --root . --name acme-tool-router --force

The generated skill is written to:
  <root>/.opencode/skills/<name>/
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "vendor", ".venv", "venv", "env",
    "dist", "build", "target", ".next", ".nuxt", ".cache", "__pycache__",
    "coverage", ".idea", ".vscode", "out", "tmp",
}

LANGUAGES: dict[str, dict[str, Any]] = {
    "python": {
        "exts": {".py", ".pyi", ".ipynb"},
        "manifests": {"pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "Pipfile", "poetry.lock", "uv.lock"},
        "runtime": ["python3", "python"],
    },
    "java": {
        "exts": {".java"},
        "manifests": {"pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts", "gradlew", "mvnw"},
        "runtime": ["java"],
    },
    "kotlin": {
        "exts": {".kt", ".kts"},
        "manifests": {"build.gradle.kts", "settings.gradle.kts"},
        "runtime": ["kotlinc"],
    },
    "typescript": {
        "exts": {".ts", ".tsx"},
        "manifests": {"package.json", "tsconfig.json"},
        "runtime": ["node"],
    },
    "javascript": {
        "exts": {".js", ".jsx", ".mjs", ".cjs"},
        "manifests": {"package.json"},
        "runtime": ["node"],
    },
    "go": {
        "exts": {".go"},
        "manifests": {"go.mod", "go.sum"},
        "runtime": ["go"],
    },
    "rust": {
        "exts": {".rs"},
        "manifests": {"Cargo.toml", "Cargo.lock"},
        "runtime": ["cargo"],
    },
    "c": {
        "exts": {".c", ".h"},
        "manifests": {"CMakeLists.txt", "Makefile"},
        "runtime": ["cc", "gcc", "clang"],
    },
    "cpp": {
        "exts": {".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx"},
        "manifests": {"CMakeLists.txt", "Makefile"},
        "runtime": ["c++", "g++", "clang++"],
    },
    "csharp": {
        "exts": {".cs"},
        "manifests": {".csproj", ".sln"},
        "runtime": ["dotnet"],
    },
    "ruby": {
        "exts": {".rb"},
        "manifests": {"Gemfile", "Gemfile.lock", "Rakefile"},
        "runtime": ["ruby"],
    },
    "php": {
        "exts": {".php"},
        "manifests": {"composer.json", "composer.lock"},
        "runtime": ["php"],
    },
    "swift": {
        "exts": {".swift"},
        "manifests": {"Package.swift"},
        "runtime": ["swift"],
    },
    "dart": {
        "exts": {".dart"},
        "manifests": {"pubspec.yaml"},
        "runtime": ["dart"],
    },
    "scala": {
        "exts": {".scala"},
        "manifests": {"build.sbt"},
        "runtime": ["scala"],
    },
    "r": {
        "exts": {".r", ".rmd"},
        "manifests": {"DESCRIPTION"},
        "runtime": ["Rscript"],
    },
    "lua": {
        "exts": {".lua"},
        "manifests": {},
        "runtime": ["lua"],
    },
    "gdscript": {
        "exts": {".gd"},
        "manifests": {"project.godot"},
        "runtime": ["godot", "godot4"],
    },
    "sql": {
        "exts": {".sql"},
        "manifests": {},
        "runtime": [],
    },
    "shell": {
        "exts": {".sh", ".bash", ".zsh"},
        "manifests": {},
        "runtime": ["bash"],
    },
    "glsl": {
        "exts": {".glsl", ".vert", ".frag", ".comp"},
        "manifests": {},
        "runtime": [],
    },
}

MANIFEST_TO_LANG = {
    manifest: lang for lang, spec in LANGUAGES.items() for manifest in spec["manifests"]
}
EXT_TO_LANG = {
    ext: lang for lang, spec in LANGUAGES.items() for ext in spec["exts"]
}
MEDIA_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg"}
FRAMEWORK_PATTERNS = {
    "django": ("Django", "python"),
    "fastapi": ("FastAPI", "python"),
    "flask": ("Flask", "python"),
    "pytest": ("pytest", "python"),
    "ruff": ("ruff", "python"),
    "mypy": ("mypy", "python"),
    "pydantic": ("Pydantic", "python"),
    "torch": ("PyTorch", "python"),
    "pandas": ("pandas", "python"),
    "numpy": ("NumPy", "python"),
    "spring-boot": ("Spring Boot", "java"),
    "quarkus": ("Quarkus", "java"),
    "micronaut": ("Micronaut", "java"),
    "junit": ("JUnit", "java"),
    "react": ("React", "javascript"),
    "next": ("Next.js", "javascript"),
    "vue": ("Vue", "javascript"),
    "nuxt": ("Nuxt", "javascript"),
    "@angular/core": ("Angular", "typescript"),
    "svelte": ("Svelte", "javascript"),
    "astro": ("Astro", "javascript"),
    "express": ("Express", "javascript"),
    "fastify": ("Fastify", "javascript"),
    "@nestjs/core": ("NestJS", "typescript"),
    "jest": ("Jest", "javascript"),
    "vitest": ("Vitest", "javascript"),
    "playwright": ("Playwright", "javascript"),
    "tailwindcss": ("Tailwind CSS", "javascript"),
    "tokio": ("Tokio", "rust"),
    "axum": ("axum", "rust"),
    "actix-web": ("Actix Web", "rust"),
    "bevy": ("Bevy", "rust"),
    "gin-gonic": ("Gin", "go"),
    "labstack/echo": ("Echo", "go"),
    "gofiber": ("Fiber", "go"),
    "rails": ("Rails", "ruby"),
    "rspec": ("RSpec", "ruby"),
    "laravel": ("Laravel", "php"),
    "symfony": ("Symfony", "php"),
    "unityengine": ("Unity", "csharp"),
    "xunit": ("xUnit", "csharp"),
    "nunit": ("NUnit", "csharp"),
}


def slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or "project"


def rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def read_text(path: Path, limit: int = 500_000) -> str:
    try:
        if path.stat().st_size > limit:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def has_binary(names: list[str]) -> tuple[bool, str | None]:
    for name in names:
        found = shutil.which(name)
        if found:
            return True, found
    return False, None


PYTHON_PROBE_TIMEOUT = 5
PYTHON_PROBE_SCRIPT = "import sys; print(sys.version_info[0])"
PYTHON_DEFAULT_COMMAND = "python3"


def _is_within(path: Path, root: Path) -> bool:
    """Return True when path is the same as, or inside, root."""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _probe_python(path: str, root: Path) -> tuple[bool, str]:
    """Return whether a Python interpreter answers a fixed, side-effect-free probe.

    Only the interpreter itself is executed, with a literal argument list in
    isolated mode (``-I``) so repository sitecustomize/PYTHONPATH hooks do not
    run. The repository is never asked to provide a script or binary for the
    probe, and a candidate that resolves inside the scanned repository is
    skipped.
    """
    try:
        executable = Path(path).resolve()
    except OSError:
        return False, "path could not be resolved"
    if _is_within(executable, root):
        return False, "skipped repository-local executable"
    try:
        result = subprocess.run(
            [str(executable), "-I", "-c", PYTHON_PROBE_SCRIPT],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=PYTHON_PROBE_TIMEOUT,
            cwd=str(root),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"probe could not run ({exc.__class__.__name__})"
    if result.returncode != 0:
        return False, f"probe exited with status {result.returncode}"
    output = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not output or not output[-1].isdigit():
        return False, "probe did not report a Python version"
    if int(output[-1]) < 3:
        return False, "interpreter is not Python 3"
    return True, ""


def resolve_runtime(language: str, root: Path) -> dict[str, Any]:
    """Resolve a runtime using PATH presence plus evidence about usability.

    Python is the only language probed: a broken Microsoft Store alias or an
    unrelated executable can be present on PATH without being a working
    interpreter. Other tools stay PATH-only because probing arbitrary
    compilers/tools is neither necessary nor safe here.
    """
    names = LANGUAGES[language]["runtime"]
    result: dict[str, Any] = {
        "available": False,
        "on_path": False,
        "path": None,
        "command": None,
        "rejected": [],
    }
    if not names:
        return result

    if language != "python":
        for name in names:
            found = shutil.which(name)
            if found:
                result.update({
                    "available": True,
                    "on_path": True,
                    "path": found,
                    "command": name,
                })
                return result
        return result

    for name in names:
        found = shutil.which(name)
        if not found:
            continue
        result["on_path"] = True
        usable, reason = _probe_python(found, root)
        if usable:
            result.update({
                "available": True,
                "path": found,
                "command": name,
            })
            return result
        result["rejected"].append({
            "command": name,
            "path": found,
            "reason": reason,
        })
        if result["path"] is None:
            result["path"] = found
    return result


def runtime_label(item: dict[str, Any]) -> str:
    if item.get("runtime_available"):
        return "available"
    if item.get("runtime_on_path"):
        return "NOT USABLE"
    return "NOT ON PATH"


def profile_python_command(profile: dict[str, Any]) -> str:
    for item in profile.get("languages", []):
        if item.get("name") == "python" and item.get("runtime_available"):
            command = item.get("runtime_command")
            if command:
                return str(command)
    return PYTHON_DEFAULT_COMMAND


def scan(root: Path, max_files: int) -> tuple[list[Path], bool]:
    files: list[Path] = []
    truncated = False
    for current, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".cache")]
        for name in names:
            files.append(Path(current) / name)
            if len(files) >= max_files:
                truncated = True
                return files, truncated
    return files, truncated


def detect_frameworks(root: Path, files: list[Path]) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    package_paths = [p for p in files if p.name == "package.json"]
    for path in package_paths:
        try:
            data = json.loads(read_text(path) or "{}")
        except json.JSONDecodeError:
            continue
        deps: dict[str, Any] = {}
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            if isinstance(data.get(key), dict):
                deps.update(data[key])
        for dep in deps:
            key = dep.lower()
            hit = FRAMEWORK_PATTERNS.get(key)
            if hit:
                name, lang = hit
                entry = found.setdefault(name, {"name": name, "language": lang, "evidence": [], "confidence": "high"})
                entry["evidence"].append(rel(path, root))
    text_manifests = [
        p for p in files
        if p.name in {"pyproject.toml", "requirements.txt", "setup.py", "setup.cfg", "Pipfile", "pom.xml", "build.gradle", "build.gradle.kts", "Cargo.toml", "go.mod", "Gemfile", "composer.json", "project.godot"}
    ]
    for path in text_manifests:
        text = read_text(path).lower()
        for pattern, (name, lang) in FRAMEWORK_PATTERNS.items():
            if pattern in text:
                entry = found.setdefault(name, {"name": name, "language": lang, "evidence": [], "confidence": "high"})
                entry["evidence"].append(rel(path, root))
    for item in found.values():
        item["evidence"] = sorted(set(item["evidence"]))
        if not item["evidence"]:
            item["confidence"] = "low"
    return sorted(found.values(), key=lambda x: (x["language"], x["name"]))


def detect_tools(root: Path, files: list[Path], text_files: dict[str, str]) -> list[dict[str, Any]]:
    tools: dict[str, dict[str, Any]] = {}

    def add(name: str, evidence: list[str], commands: list[str], note: str = "") -> None:
        if not evidence:
            return
        available, path = has_binary(commands)
        entry = tools.setdefault(name, {"name": name, "evidence": [], "available": available, "path": path, "note": note})
        entry["evidence"].extend(evidence)
        entry["evidence"] = sorted(set(entry["evidence"]))
        entry["available"] = entry["available"] or available
        if path and not entry["path"]:
            entry["path"] = path

    blend = [rel(p, root) for p in files if p.suffix.lower() in {".blend", ".blend1"}]
    bpy = [rel(p, root) for p, text in text_files.items() if p.suffix == ".py" and re.search(r"(?m)^\s*(?:import bpy|from bpy)", text)]
    add("blender", blend + bpy, ["blender"], "Offline 3D, blockout, render passes")

    godot = [rel(p, root) for p in files if p.name == "project.godot" or p.suffix.lower() in {".gd", ".tscn", ".tres"}]
    add("godot", godot, ["godot", "godot4"], "Runtime, playable scenes, 2D parallax")

    unity = [rel(p, root) for p in files if p.name == "ProjectVersion.txt" and p.parent.name == "ProjectSettings"]
    unity += [rel(p, root) for p in files if p.suffix.lower() in {".unity", ".prefab"}]
    add("unity", unity, ["unity"], "Unity project assets and editor workflow")

    media = [rel(p, root) for p in files if p.suffix.lower() in MEDIA_EXTS]
    add("ffmpeg", media, ["ffmpeg"], "Audio/video probe, transcode, encode")

    docker = [rel(p, root) for p in files if p.name in {"Dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}]
    add("docker", docker, ["docker"], "Containerized build/run")

    if (root / ".git").exists():
        add("git", [".git"], ["git"], "Version control")

    if any(p.name in {"pnpm-lock.yaml"} for p in files):
        add("pnpm", [rel(p, root) for p in files if p.name == "pnpm-lock.yaml"], ["pnpm"], "Package manager")
    if any(p.name in {"yarn.lock"} for p in files):
        add("yarn", [rel(p, root) for p in files if p.name == "yarn.lock"], ["yarn"], "Package manager")
    if any(p.name in {"package-lock.json"} for p in files):
        add("npm", [rel(p, root) for p in files if p.name == "package-lock.json"], ["npm"], "Package manager")
    if any(p.name in {"bun.lockb"} for p in files):
        add("bun", [rel(p, root) for p in files if p.name == "bun.lockb"], ["bun"], "Package manager")

    add("maven", [rel(p, root) for p in files if p.name in {"pom.xml", "mvnw"}], ["mvn"], "Java build/test")
    add("gradle", [rel(p, root) for p in files if p.name in {"build.gradle", "build.gradle.kts", "gradlew"}], ["gradle"], "Java/Kotlin build/test")
    add("cmake", [rel(p, root) for p in files if p.name == "CMakeLists.txt"], ["cmake"], "C/C++ build")
    add("make", [rel(p, root) for p in files if p.name in {"Makefile", "makefile"}], ["make"], "Make targets")
    add("cargo", [rel(p, root) for p in files if p.name == "Cargo.toml"], ["cargo"], "Rust build/test")
    add("go", [rel(p, root) for p in files if p.name == "go.mod"], ["go"], "Go build/test")
    add("dotnet", [rel(p, root) for p in files if p.suffix.lower() in {".csproj", ".sln"}], ["dotnet"], ".NET build/test")

    for item in tools.values():
        item["evidence"] = sorted(set(item["evidence"]))
    return sorted(tools.values(), key=lambda x: x["name"])


def detect_commands(root: Path, files: list[Path], python_command: str = PYTHON_DEFAULT_COMMAND) -> list[dict[str, str]]:
    commands: dict[str, str] = {}

    package = next((p for p in files if p.name == "package.json"), None)
    if package:
        try:
            data = json.loads(read_text(package) or "{}")
        except json.JSONDecodeError:
            data = {}
        scripts = data.get("scripts", {}) if isinstance(data.get("scripts"), dict) else {}
        if any(p.name == "pnpm-lock.yaml" for p in files):
            pm = "pnpm"
        elif any(p.name == "yarn.lock" for p in files):
            pm = "yarn"
        elif any(p.name == "bun.lockb" for p in files):
            pm = "bun"
        else:
            pm = "npm"
        for name in ("test", "lint", "build", "dev", "typecheck"):
            if name in scripts:
                commands[f"{pm} {name}"] = f"package.json scripts.{name}"

    python_manifests = [p for p in files if p.name in {"pyproject.toml", "requirements.txt", "setup.py", "setup.cfg", "Pipfile"}]
    py_text = "\n".join(read_text(p).lower() for p in python_manifests)
    python_files = [p for p in files if p.suffix == ".py"]
    if python_files:
        test_files = [p for p in python_files if p.name.startswith("test_") or p.name.endswith("_test.py")]
        test_text = "\n".join(read_text(p, 200_000).lower() for p in test_files[:200])
        if "pytest" in py_text or any(p.name == "pytest.ini" or p.name == "conftest.py" for p in files):
            commands[f"{python_command} -m pytest"] = "pytest dependency or config"
        if "import unittest" in test_text or "from unittest" in test_text:
            commands[f"{python_command} -m unittest discover -s tests -v"] = "unittest test files"
        if "ruff" in py_text:
            commands["ruff check ."] = "ruff dependency"
        if "mypy" in py_text:
            commands["mypy ."] = "mypy dependency"

    if any(p.name == "pom.xml" for p in files):
        commands["mvn test"] = "Maven project"
    if any(p.name in {"build.gradle", "build.gradle.kts"} for p in files):
        gradlew = any(p.name == "gradlew" for p in files)
        commands["./gradlew test" if gradlew else "gradle test"] = "Gradle project"
    if any(p.name == "Cargo.toml" for p in files):
        commands["cargo test"] = "Cargo project"
    if any(p.name == "go.mod" for p in files):
        commands["go test ./..."] = "Go module"
    if any(p.name == "CMakeLists.txt" for p in files):
        commands["cmake -S . -B build && cmake --build build && ctest --test-dir build"] = "CMake project"
    if any(p.name in {"Makefile", "makefile"} for p in files):
        commands["make test"] = "Makefile (verify a test target exists)"
    if any(p.suffix.lower() == ".csproj" for p in files):
        commands["dotnet test"] = ".NET project"
    if any(p.name == "Gemfile" for p in files):
        commands["bundle exec rspec"] = "Ruby/RSpec (verify rspec is present)"
    if any(p.name == "composer.json" for p in files):
        commands["composer test"] = "PHP composer script (verify it exists)"
    return [{"command": k, "source": v} for k, v in commands.items()]


def build_profile(root: Path, max_files: int = 50000) -> dict[str, Any]:
    root = root.resolve()
    if not root.is_dir():
        raise SystemExit(f"not a directory: {root}")
    files, truncated = scan(root, max_files)
    counts: Counter[str] = Counter()
    examples: dict[str, list[str]] = defaultdict(list)
    manifests: dict[str, list[str]] = defaultdict(list)
    text_files: dict[Path, str] = {}
    for path in files:
        ext = path.suffix.lower()
        lang = EXT_TO_LANG.get(ext)
        if lang:
            counts[lang] += 1
            if len(examples[lang]) < 12:
                examples[lang].append(rel(path, root))
        if path.name in MANIFEST_TO_LANG:
            manifests[MANIFEST_TO_LANG[path.name]].append(rel(path, root))
        elif path.suffix.lower() in {".csproj", ".sln"}:
            manifests["csharp"].append(rel(path, root))
        if ext in {".py", ".gd"} and len(text_files) < 500:
            text_files[path] = read_text(path, 200_000)

    frameworks = detect_frameworks(root, files)
    tools = detect_tools(root, files, text_files)
    runtime_cache: dict[str, dict[str, Any]] = {}

    def runtime_for(lang: str) -> dict[str, Any]:
        if lang not in runtime_cache:
            runtime_cache[lang] = resolve_runtime(lang, root)
        return runtime_cache[lang]

    languages: list[dict[str, Any]] = []
    for lang, count in counts.most_common():
        manifest_hits = sorted(set(manifests.get(lang, [])))
        framework_hits = [f["name"] for f in frameworks if f["language"] == lang]
        runtime = runtime_for(lang)
        if manifest_hits and count >= 3:
            confidence = "high"
        elif manifest_hits or count >= 3:
            confidence = "medium"
        else:
            confidence = "low"
        languages.append({
            "name": lang,
            "files": count,
            "examples": examples[lang],
            "manifests": manifest_hits,
            "frameworks": framework_hits,
            "runtime_available": runtime["available"],
            "runtime_path": runtime["path"],
            "runtime_command": runtime["command"],
            "runtime_on_path": runtime["on_path"],
            "runtime_rejected": runtime["rejected"],
            "confidence": confidence,
        })

    # Manifests can identify a stack even when source files are sparse.
    for lang, paths in manifests.items():
        if any(item["name"] == lang for item in languages):
            continue
        runtime = runtime_for(lang)
        languages.append({
            "name": lang,
            "files": 0,
            "examples": [],
            "manifests": sorted(set(paths)),
            "frameworks": [f["name"] for f in frameworks if f["language"] == lang],
            "runtime_available": runtime["available"],
            "runtime_path": runtime["path"],
            "runtime_command": runtime["command"],
            "runtime_on_path": runtime["on_path"],
            "runtime_rejected": runtime["rejected"],
            "confidence": "medium" if paths else "low",
        })

    languages.sort(key=lambda x: (x["files"], len(x["manifests"])), reverse=True)

    python_item = next((item for item in languages if item["name"] == "python"), None)
    python_command = (
        python_item["runtime_command"]
        if python_item and python_item["runtime_available"] and python_item["runtime_command"]
        else PYTHON_DEFAULT_COMMAND
    )
    commands = detect_commands(root, files, python_command)
    primary = languages[0]["name"] if languages else None
    top_files = languages[0]["files"] if languages else 0
    total_code = sum(item["files"] for item in languages) or 1
    substantial = [
        item for item in languages
        if item["files"] >= 3 or (item["manifests"] and item["files"] >= 1)
    ]
    ambiguous = len(substantial) >= 2 and (top_files / total_code) < 0.7

    if any(t["name"] in {"godot", "unity"} for t in tools):
        kind = "game"
    elif any(t["name"] == "blender" for t in tools):
        kind = "3d"
    elif any(f["name"] in {"Next.js", "React", "Vue", "Nuxt", "Angular", "Svelte", "Astro"} for f in frameworks):
        kind = "web"
    elif any(f["name"] in {"Django", "FastAPI", "Flask", "Spring Boot", "Rails", "Laravel"} for f in frameworks):
        kind = "backend"
    elif any(f["name"] in {"PyTorch", "pandas", "NumPy"} for f in frameworks):
        kind = "data"
    else:
        kind = "software"

    warnings: list[str] = []
    if truncated:
        warnings.append(f"scan stopped at {max_files} files; counts are a lower bound")
    if not languages:
        warnings.append("no source files or known manifests detected; generated router is intentionally low-confidence")
    if ambiguous:
        warnings.append("polyglot project: route by path/extension, not by repository root")
    for item in languages:
        if not item["runtime_on_path"]:
            warnings.append(f"{item['name']} is used by the project but its runtime is not on PATH")
        elif not item["runtime_available"]:
            warnings.append(
                f"{item['name']} is on PATH but no usable runtime answered a fixed probe; "
                "do not rely on it until the installation is repaired"
            )
        else:
            rejected = item.get("runtime_rejected") or []
            if rejected:
                names = ", ".join(str(entry["command"]) for entry in rejected)
                warnings.append(
                    f"{item['name']}: {names} on PATH is not usable; using "
                    f"{item['runtime_command']} for generated commands"
                )

    profile = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "root": root.name,
        "kind": kind,
        "primary_language": primary,
        "ambiguous": ambiguous,
        "languages": languages,
        "frameworks": frameworks,
        "tools": tools,
        "commands": commands,
        "warnings": warnings,
        "scan": {"files": len(files), "truncated": truncated},
    }
    return profile


def table_row(cells: list[str]) -> str:
    return "| " + " | ".join(str(c).replace("|", "\\|") for c in cells) + " |"


def render_skill(name: str, profile: dict[str, Any], out_dir: Path) -> str:
    root_name = profile["root"]
    lines: list[str] = []
    lines += [
        "---",
        f"name: {name}",
        f"description: Use when working in {root_name} and choosing between competing languages, engines, build systems, test runners, or package managers, or when picking the command to build, test, or run part of the project.",
        "---",
        "",
        f"# {root_name} Tool Router",
        "",
        f"Generated from repository evidence on {profile['generated_at']}.",
        "This is a **project-local** router. It describes what this repository uses; it does not",
        "prove a tool is installed. Check the runtime column before relying on a command.",
        "",
        "## What this project uses",
        "",
    ]
    lines.append(table_row(["Language", "Files", "Manifests", "Frameworks", "Confidence", "Runtime"]))
    lines.append(table_row(["---", "---:", "---", "---", "---", "---"]))
    for item in profile["languages"]:
        lines.append(table_row([
            item["name"],
            item["files"],
            ", ".join(item["manifests"][:4]) or "—",
            ", ".join(item["frameworks"][:4]) or "—",
            item["confidence"],
            runtime_label(item),
        ]))
    if not profile["languages"]:
        lines.append(table_row(["—", "0", "—", "—", "low", "unknown"]))

    lines += ["", "## Route tasks", ""]
    lines.append(table_row(["If the task is about...", "Use", "Evidence"]))
    lines.append(table_row(["---", "---", "---"]))
    for item in profile["languages"]:
        ext = ", ".join(sorted(LANGUAGES[item["name"]]["exts"]))
        lines.append(table_row([
            f"Files under the {item['name']} toolchain, or extensions {ext}",
            f"`{item['name']}` toolchain",
            ", ".join(item["examples"][:3]) or ", ".join(item["manifests"][:3]) or "no examples",
        ]))
    for framework in profile["frameworks"]:
        lines.append(table_row([
            f"{framework['name']} behavior",
            framework["name"],
            ", ".join(framework["evidence"][:3]) or "manifest",
        ]))
    for tool in profile["tools"]:
        lines.append(table_row([
            f"{tool['name']} work",
            f"`{tool['name']}`",
            ", ".join(tool["evidence"][:3]),
        ]))

    lines += ["", "### Routing tie-breakers", ""]
    if profile["ambiguous"]:
        lines.append("- **Multiple substantial languages:** route by the task's path/module, not by the repository root. If the module is unclear, ask.")
    if len(profile["languages"]) > 1 or len(profile["tools"]) > 1:
        lines.append("- **Multiple toolchains:** route by the task's path or requested artifact. If two options remain plausible, ask; do not choose from the repository root.")
    lines.append("- **Evidence over defaults:** a detected tool being present does not mean every task should use it. Use the path and manifest above.")
    if not profile["languages"]:
        lines.append("- **No confident toolchain:** stop and ask before introducing a language or engine.")

    lines += ["", "## Commands", ""]
    if profile["commands"]:
        for command in profile["commands"]:
            lines.append(f"- `{command['command']}` — {command['source']}")
    else:
        lines.append("- No standard commands detected. Ask before inventing one.")

    lines += ["", "## Guardrails", ""]
    lines += [
        "- Do not introduce a new language, engine, package manager, or build system without approval.",
        "- Prefer the project's existing path/manifest evidence over a global default.",
        "- If the runtime column says `NOT ON PATH` or `NOT USABLE`, do not claim the tool is available; report the gap.",
        "- Keep generated evidence intact. Edit the builder or re-run it rather than hand-editing counts.",
        "- For a delegated task, pass the selected toolchain and command in the brief; do not make the child re-detect the stack.",
        "",
        "## Delegation handoff",
        "",
        "When using `subagent-orchestrator`, include:",
        "",
        "- the task's path/module,",
        "- the selected language and engine,",
        "- the exact test/build command from this page,",
        "- the relevant manifest and runtime availability.",
        "",
        "The subagent should not choose a different stack because its model has a preference.",
        "",
        "## Evidence",
        "",
        f"- Machine-readable profile: [`data/project-profile.json`](data/project-profile.json)",
        f"- Detailed evidence: [`references/evidence.md`](references/evidence.md)",
        f"- Scan: {profile['scan']['files']} files; truncated={str(profile['scan']['truncated']).lower()}",
        "",
    ]
    if profile["warnings"]:
        lines += ["## Warnings", ""]
        lines += [f"- {warning}" for warning in profile["warnings"]]
        lines += [""]
    lines += [
        "## Refresh",
        "",
        "Re-run the project-classifier builder after a stack change:",
        "",
        "```bash",
        f"{profile_python_command(profile)} <path-to-project-classifier>/scripts/build_project_classifier.py --root . --out {out_dir.as_posix()} --force",
        "```",
        "",
    ]
    return "\n".join(lines)


def render_evidence(profile: dict[str, Any]) -> str:
    lines = ["# Evidence", "", f"Generated: {profile['generated_at']}", ""]
    lines += ["## Languages", ""]
    for item in profile["languages"]:
        lines.append(f"### {item['name']} ({item['confidence']})")
        lines.append("")
        lines.append(f"- Files: {item['files']}")
        lines.append(f"- Manifests: {', '.join(item['manifests']) or '—'}")
        lines.append(f"- Frameworks: {', '.join(item['frameworks']) or '—'}")
        lines.append(f"- Runtime on PATH: {item.get('runtime_on_path', item['runtime_available'])} ({item['runtime_path'] or '—'})")
        lines.append(f"- Runtime usable: {item['runtime_available']} (command: {item.get('runtime_command') or '—'})")
        for rejected in item.get("runtime_rejected") or []:
            lines.append(
                f"- Rejected runtime candidate: `{rejected['command']}` at "
                f"`{rejected['path']}` ({rejected['reason']})"
            )
        if item["examples"]:
            lines.append("- Examples:")
            lines += [f"  - `{ex}`" for ex in item["examples"]]
        lines.append("")
    lines += ["## Tools", ""]
    for tool in profile["tools"]:
        lines.append(f"- **{tool['name']}**: available={tool['available']} path={tool['path'] or '—'} evidence={', '.join(tool['evidence'][:8])}")
    lines += ["", "## Frameworks", ""]
    for framework in profile["frameworks"]:
        lines.append(f"- **{framework['name']}** ({framework['language']}): {', '.join(framework['evidence'][:8])}")
    lines += ["", "## Commands", ""]
    for command in profile["commands"]:
        lines.append(f"- `{command['command']}` — {command['source']}")
    return "\n".join(lines) + "\n"


GENERATED_ARTIFACTS = (
    Path("SKILL.md"),
    Path("data") / "project-profile.json",
    Path("references") / "evidence.md",
)


def _is_link(path: Path) -> bool:
    """Return True for a symlink or (where supported) a Windows junction."""
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
        if os.name == "nt":
            try:
                info = os.lstat(path)
            except FileNotFoundError:
                return False
            tag = getattr(info, "st_reparse_tag", 0)
            reparse_tags = {
                value for value in (
                    getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0),
                    getattr(stat, "IO_REPARSE_TAG_SYMLINK", 0),
                )
                if value
            }
            if tag and tag in reparse_tags:
                return True
    except FileNotFoundError:
        return False
    except OSError:
        # An unreadable path cannot be validated safely.
        return True
    return False


def _validate_output_destination(out_dir: Path) -> str | None:
    """Validate the destination before any mutation. Return an error message or None."""
    if _is_link(out_dir):
        return f"refusing to write through a symbolic link or junction: {out_dir}"
    if out_dir.exists() and not out_dir.is_dir():
        return f"refusing to replace a file with a generated skill directory: {out_dir}"
    for relative in GENERATED_ARTIFACTS:
        target = out_dir / relative
        if _is_link(target):
            return f"refusing to replace a linked generated artifact: {target}"
        if target.exists() and target.is_dir():
            return f"refusing to replace a directory with a generated file: {target}"
        parent = target.parent
        if _is_link(parent):
            return f"refusing to write through a linked directory: {parent}"
        if parent.exists() and not parent.is_dir():
            return f"refusing to write next to a non-directory artifact: {parent}"
    return None


def _looks_generated_skill(out_dir: Path) -> bool:
    """Recognize output written by this builder, including older generated skills.

    Recognition requires all managed artifacts and a consistent generated
    signature. This prevents --force from treating an arbitrary directory that
    merely has a SKILL.md (or a malformed partial output) as replaceable.
    """
    for relative in GENERATED_ARTIFACTS:
        target = out_dir / relative
        if not target.is_file() or _is_link(target):
            return False

    skill_text = read_text(out_dir / "SKILL.md")
    frontmatter = re.match(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", skill_text, re.S)
    if not frontmatter:
        return False
    if not re.search(r"^name:\s*[a-z0-9]+(?:-[a-z0-9]+)*\s*$", frontmatter.group(1), re.M):
        return False
    if "Generated from repository evidence on " not in skill_text:
        return False
    if "This is a **project-local** router." not in skill_text:
        return False

    try:
        generated_profile = json.loads(read_text(out_dir / "data" / "project-profile.json"))
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(generated_profile, dict) or generated_profile.get("schema_version") != 1:
        return False
    if not all(key in generated_profile for key in ("generated_at", "root", "languages", "commands", "scan")):
        return False
    if not isinstance(generated_profile["languages"], list) or not isinstance(generated_profile["commands"], list):
        return False

    evidence_text = read_text(out_dir / "references" / "evidence.md")
    if not evidence_text.startswith("# Evidence\n"):
        return False
    if "\nGenerated: " not in evidence_text:
        return False
    return True


def write_skill(name: str, profile: dict[str, Any], out_dir: Path, force: bool) -> None:
    problem = _validate_output_destination(out_dir)
    if problem:
        print(problem, file=sys.stderr)
        raise SystemExit(2)

    if out_dir.exists():
        if not force:
            print(f"output exists: {out_dir} (use --force to refresh a generated skill)", file=sys.stderr)
            raise SystemExit(2)
        if not _looks_generated_skill(out_dir):
            print(
                f"refusing to replace unrelated directory {out_dir}: "
                "it is not a generated project-classifier skill",
                file=sys.stderr,
            )
            raise SystemExit(2)

    # Render everything before mutating the destination. Managed artifacts are
    # overwritten in place; unrelated files in a generated skill are preserved.
    skill_text = render_skill(name, profile, out_dir)
    profile_text = json.dumps(profile, indent=2)
    evidence_text = render_evidence(profile)

    (out_dir / "data").mkdir(parents=True, exist_ok=True)
    (out_dir / "references").mkdir(parents=True, exist_ok=True)
    (out_dir / "SKILL.md").write_text(skill_text, encoding="utf-8")
    (out_dir / "data" / "project-profile.json").write_text(profile_text, encoding="utf-8")
    (out_dir / "references" / "evidence.md").write_text(evidence_text, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    ap.add_argument("--out", help="output skill directory; default <root>/.opencode/skills/<name>")
    ap.add_argument("--name", help="skill name; default <project-slug>-tool-router")
    ap.add_argument("--force", action="store_true", help="refresh an existing generated skill, preserving unrelated files")
    ap.add_argument("--max-files", type=int, default=50000)
    ap.add_argument("--dry-run", action="store_true", help="print the profile without writing")
    ap.add_argument("--json", action="store_true", help="print the profile after writing")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    profile = build_profile(root, args.max_files)
    base_slug = slugify(root.name)[:48].strip("-")
    default_name = f"{base_slug}-tool-router"
    name = args.name or default_name
    if not re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", name) or len(name) > 64:
        print(f"invalid skill name {name!r}; use lowercase letters, digits, and single hyphens (max 64)", file=sys.stderr)
        return 2
    raw_out = Path(args.out).expanduser() if args.out else root / ".opencode" / "skills" / name
    if _is_link(raw_out):
        print(f"refusing to write through a symbolic link or junction: {raw_out}", file=sys.stderr)
        return 2
    out_dir = raw_out.resolve()
    if out_dir == root or out_dir in root.parents:
        print(f"refusing to replace the project root or an ancestor: {out_dir}", file=sys.stderr)
        return 2
    if args.max_files < 1:
        print("--max-files must be positive", file=sys.stderr)
        return 2

    if args.dry_run:
        print(json.dumps(profile, indent=2))
        print(f"\nwould write: {out_dir}", file=sys.stderr)
        return 0

    write_skill(name, profile, out_dir, args.force)
    print(f"wrote {out_dir}")
    print(f"generated skill: {name}; primary={profile['primary_language'] or 'unknown'}; ambiguous={profile['ambiguous']}")
    if args.json:
        print(json.dumps(profile, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
