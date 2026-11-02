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


def detect_commands(root: Path, files: list[Path]) -> list[dict[str, str]]:
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
            commands["python3 -m pytest"] = "pytest dependency or config"
        if "import unittest" in test_text or "from unittest" in test_text:
            commands["python3 -m unittest discover -s tests -v"] = "unittest test files"
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
    commands = detect_commands(root, files)

    languages: list[dict[str, Any]] = []
    for lang, count in counts.most_common():
        manifest_hits = sorted(set(manifests.get(lang, [])))
        framework_hits = [f["name"] for f in frameworks if f["language"] == lang]
        runtime_available, runtime_path = has_binary(LANGUAGES[lang]["runtime"])
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
            "runtime_available": runtime_available,
            "runtime_path": runtime_path,
            "confidence": confidence,
        })

    # Manifests can identify a stack even when source files are sparse.
    for lang, paths in manifests.items():
        if any(item["name"] == lang for item in languages):
            continue
        languages.append({
            "name": lang,
            "files": 0,
            "examples": [],
            "manifests": sorted(set(paths)),
            "frameworks": [f["name"] for f in frameworks if f["language"] == lang],
            "runtime_available": has_binary(LANGUAGES[lang]["runtime"])[0],
            "runtime_path": has_binary(LANGUAGES[lang]["runtime"])[1],
            "confidence": "medium" if paths else "low",
        })

    languages.sort(key=lambda x: (x["files"], len(x["manifests"])), reverse=True)
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
        if not item["runtime_available"]:
            warnings.append(f"{item['name']} is used by the project but its runtime is not on PATH")

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
        f"description: Route tasks in {root_name} to its detected languages, frameworks, tools, and commands. Generated from repository evidence; use when choosing between Python/Java, Blender/Godot, build systems, test runners, or package managers.",
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
            "available" if item["runtime_available"] else "NOT ON PATH",
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

    lines += ["", "### Language and engine tie-breakers", ""]
    languages = {item["name"] for item in profile["languages"]}
    tools = {tool["name"] for tool in profile["tools"]}
    if {"python", "java"} <= languages:
        lines.append("- **Python vs Java:** route by the file path and manifest, not by the repo root. Python lives under Python packages/tests; Java under the Maven/Gradle module. If both are plausible, ask for the module.")
    if {"blender", "godot"} <= tools:
        lines.append("- **Blender vs Godot:** use Blender for offline 3D, blockouts, render passes, and asset generation; use Godot for runtime/playable scenes, 2D parallax, and in-engine cameras. If the task is ambiguous, ask which output is required.")
    elif "blender" in tools:
        lines.append("- **Blender:** use it for offline 3D, blockouts, render passes, and asset generation. It is not a runtime engine.")
    elif "godot" in tools:
        lines.append("- **Godot:** use it for runtime, playable scenes, 2D parallax, and in-engine cameras. It is not an offline renderer.")
    if profile["ambiguous"]:
        lines.append("- **Polyglot repository:** this project has more than one substantial language. Choose by the task's paths, not by the repository root.")
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
        "- If the runtime column says `NOT ON PATH`, do not claim the tool is available; report the gap.",
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
        f"python3 <path-to-project-classifier>/scripts/build_project_classifier.py --root . --out {out_dir.as_posix()} --force",
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
        lines.append(f"- Runtime on PATH: {item['runtime_available']} ({item['runtime_path'] or '—'})")
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


def write_skill(name: str, profile: dict[str, Any], out_dir: Path, force: bool) -> None:
    if out_dir.exists():
        if not force:
            print(f"output exists: {out_dir} (use --force to replace)", file=sys.stderr)
            raise SystemExit(2)
        shutil.rmtree(out_dir)
    (out_dir / "data").mkdir(parents=True)
    (out_dir / "references").mkdir(parents=True)
    (out_dir / "SKILL.md").write_text(render_skill(name, profile, out_dir), encoding="utf-8")
    (out_dir / "data" / "project-profile.json").write_text(json.dumps(profile, indent=2), encoding="utf-8")
    (out_dir / "references" / "evidence.md").write_text(render_evidence(profile), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    ap.add_argument("--out", help="output skill directory; default <root>/.opencode/skills/<name>")
    ap.add_argument("--name", help="skill name; default <project-slug>-tool-router")
    ap.add_argument("--force", action="store_true", help="replace an existing generated skill")
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
    out_dir = Path(args.out).resolve() if args.out else root / ".opencode" / "skills" / name
    if out_dir == root or str(root).startswith(str(out_dir) + os.sep):
        print(f"refusing to write a generated skill over the project root: {out_dir}", file=sys.stderr)
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
