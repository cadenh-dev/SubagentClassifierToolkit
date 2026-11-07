#!/usr/bin/env python3
"""Credential presence checks and tracked-repository secret scanning."""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

PATTERNS = (
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("OpenAI-style key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("JSON Web Token", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
)


class InputError(Exception):
    pass


def load_manifest(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InputError("cannot read valid manifest") from exc
    entries = data.get("secrets") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise InputError("manifest must contain a secrets array")
    names: set[str] = set()
    for entry in entries:
        if (not isinstance(entry, dict) or not isinstance(entry.get("name"), str)
                or not entry["name"] or entry["name"] in names
                or not isinstance(entry.get("purpose"), str)
                or not isinstance(entry.get("required"), bool)
                or not isinstance(entry.get("used_by"), list)
                or not isinstance(entry.get("sources"), list)):
            raise InputError("invalid secret entry")
        names.add(entry["name"])
        for source in entry["sources"]:
            if not isinstance(source, dict) or source.get("type") not in ("env", "file", "command"):
                raise InputError("invalid source")
            if source["type"] == "env" and not isinstance(source.get("var"), str):
                raise InputError("invalid env source")
            if source["type"] == "file" and not isinstance(source.get("path"), str):
                raise InputError("invalid file source")
            if source["type"] == "command" and (not isinstance(source.get("check"), list) or not source["check"] or not all(isinstance(x, str) for x in source["check"])):
                raise InputError("invalid command source")
    return entries


def check(entries: list[dict], manifest_dir: Path, timeout: float) -> tuple[list[dict], bool]:
    report, missing = [], False
    for item in entries:
        sources = []
        any_present = False
        for source in item["sources"]:
            kind = source["type"]
            result = {"type": kind}
            try:
                if kind == "env":
                    result["status"] = "present" if os.environ.get(source["var"], "") else "missing"
                elif kind == "file":
                    path = Path(source["path"])
                    if not path.is_absolute():
                        path = manifest_dir / path
                    try:
                        info = path.stat()
                    except FileNotFoundError:
                        result["status"] = "missing"
                    else:
                        result.update(status="present" if stat.S_ISREG(info.st_mode) else "unknown", size=info.st_size)
                        if stat.S_ISREG(info.st_mode) and info.st_mode & 0o077:
                            result["warning"] = "group/world readable"
                else:
                    try:
                        proc = subprocess.run(source["check"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout, check=False)
                        result.update(status="present" if proc.returncode == 0 else "missing", exit_status=proc.returncode)
                    except (OSError, subprocess.TimeoutExpired):
                        result["status"] = "unknown"
            except OSError:
                result["status"] = "unknown"
            any_present |= result["status"] == "present"
            sources.append(result)
        if item["required"] and not any_present:
            missing = True
        report.append({"name": item["name"], "required": item["required"], "status": "present" if any_present else "missing", "sources": sources,
                       "action": f"call request_secret for {item['name']}" if item["required"] and not any_present else None})
    return report, missing


def scan(entries: list[dict], root: Path) -> list[dict]:
    findings = []
    env_names = {s["var"] for e in entries for s in e["sources"] if s["type"] == "env"}
    files = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True).stdout.split(b"\0")
    ignored = subprocess.run(["git", "-C", str(root), "check-ignore", "-q", ".env"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    for raw in files:
        if not raw:
            continue
        rel = os.fsdecode(raw)
        if Path(rel).name.startswith(".env") and not ignored:
            findings.append({"file": rel, "line": 1, "pattern": "tracked env file not ignored"})
        path = root / rel
        try:
            if not path.is_file() or path.stat().st_size > 2_000_000:
                continue
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, 1):
            for label, pattern in PATTERNS:
                if pattern.search(line):
                    findings.append({"file": rel, "line": number, "pattern": label})
            for name in env_names:
                if re.search(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"\s*=\s*[^\s#]", line):
                    findings.append({"file": rel, "line": number, "pattern": "manifest env var assigned literal"})
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("check", "scan-repo"))
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        if not 0 < args.timeout <= 60:
            raise InputError("timeout must be between 0 and 60 seconds")
        entries = load_manifest(args.manifest)
        if args.mode == "check":
            records, missing = check(entries, args.manifest.resolve().parent, args.timeout)
            findings = []
        else:
            records, missing = [], False
            findings = scan(entries, args.root.resolve())
    except (InputError, OSError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    result = {"mode": args.mode, "secrets": records, "findings": findings}
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        for item in records:
            print(f"{item['name']}: {item['status']}")
            if item["action"]:
                print(item["action"])
        for finding in findings:
            print(f"{finding['file']}:{finding['line']}: {finding['pattern']}")
    return 1 if missing or findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
