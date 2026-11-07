#!/usr/bin/env python3
"""Persist project or global agent roles and print thread-start instructions."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PRESETS_FILE = ROOT / "data" / "presets.json"
ROLE_BEGIN, ROLE_END = "<!-- toolkit:role:begin -->", "<!-- toolkit:role:end -->"
RULE_BEGIN, RULE_END = "<!-- toolkit:session-kickoff:begin -->", "<!-- toolkit:session-kickoff:end -->"


class BadInput(Exception):
    """Invalid arguments or data (exit 2)."""


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BadInput(f"cannot read JSON {path}: {exc}") from exc


def presets() -> dict[str, Any]:
    data = read_json(PRESETS_FILE)
    if data.get("schema_version") != 1 or not isinstance(data.get("presets"), list):
        raise BadInput("presets must be a schema_version 1 object")
    result = {p.get("id"): p for p in data["presets"]}
    for required in ("id", "summary", "main_agent", "delegation", "policies"):
        if any(not isinstance(p, dict) or required not in p for p in data["presets"]):
            raise BadInput(f"every preset requires {required}")
    return data


def global_path(override: str | None) -> Path:
    if override:
        return Path(override).expanduser()
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "agent-role" / "default.json"


def role_file(path: Path) -> dict[str, Any]:
    value = read_json(path)
    if not isinstance(value, dict) or value.get("schema_version") != 1 or not isinstance(value.get("role"), dict):
        raise BadInput(f"invalid role file {path}: expected schema_version 1 and role object")
    validate_role(value["role"])
    return value["role"]


def validate_shape(value: Any, template: Any, path: str = "role") -> None:
    if isinstance(template, dict):
        if not isinstance(value, dict) or set(value) != set(template):
            raise BadInput(f"{path} must have the preset fields")
        for key, item in value.items():
            validate_shape(item, template[key], f"{path}.{key}")
    elif isinstance(template, list):
        if not isinstance(value, list):
            raise BadInput(f"{path} must be a list")
    elif type(value) is not type(template):
        raise BadInput(f"{path} must be {type(template).__name__}")


def validate_role(role: dict[str, Any]) -> None:
    available = {p["id"]: p for p in presets()["presets"]}
    preset_id = role.get("id")
    if preset_id not in available:
        raise BadInput(f"role id must match a built-in preset: {preset_id}")
    validate_shape(role, available[preset_id])


def resolve(repo: Path, global_file: str | None) -> dict[str, Any]:
    data = presets()
    project = repo / ".agent-role.json"
    if project.is_file():
        role, source = role_file(project), "project"
    elif global_path(global_file).is_file():
        role, source = role_file(global_path(global_file)), "global"
    else:
        role, source = data["presets"][0], "builtin"
    return {"source": source, "role": role}


def parse_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def apply_override(role: dict[str, Any], raw: str) -> None:
    if "=" not in raw:
        raise BadInput(f"--set must be key.path=value: {raw}")
    path, raw_value = raw.split("=", 1)
    keys = path.split(".")
    node: Any = role
    for key in keys[:-1]:
        if not isinstance(node, dict) or key not in node or not isinstance(node[key], dict):
            raise BadInput(f"unknown override path: {path}")
        node = node[key]
    if not isinstance(node, dict) or keys[-1] not in node:
        raise BadInput(f"unknown override path: {path}")
    parsed = parse_value(raw_value)
    expected = node[keys[-1]]
    if isinstance(expected, dict) and not isinstance(parsed, dict):
        raise BadInput(f"override {path} must be a JSON object")
    if isinstance(expected, list) and not isinstance(parsed, list):
        raise BadInput(f"override {path} must be a JSON list")
    if not isinstance(expected, (dict, list)) and type(parsed) is not type(expected):
        raise BadInput(f"override {path} must be {type(expected).__name__}")
    node[keys[-1]] = parsed


def update_marked(existing: bytes, begin: str, end: str, block: bytes, append: bool) -> bytes:
    bmark, emark = begin.encode(), end.encode()
    b, e = existing.count(bmark), existing.count(emark)
    if b != e or b > 1:
        raise ValueError("malformed markers: expected zero or one balanced pair")
    if b:
        start, finish = existing.index(bmark), existing.index(emark)
        if start > finish:
            raise ValueError("malformed markers: end marker precedes begin marker")
        return existing[:start] + block + existing[finish + len(emark):]
    if existing and not append:
        raise ValueError("file has no managed markers; use --append")
    separator = b"\n\n" if existing and not existing.endswith(b"\n\n") else (b"\n" if existing and existing.endswith(b"\n") else b"")
    return existing + separator + block + b"\n"


def role_block(role: dict[str, Any]) -> bytes:
    data = json.dumps(role, ensure_ascii=False, sort_keys=True, indent=2)
    return f"{ROLE_BEGIN}\n## Agent role\n\n```json\n{data}\n```\n{ROLE_END}".encode()


def save_role(role: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 1, "role": role}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def brief_text(resolved: dict[str, Any]) -> str:
    role, source = resolved["role"], resolved["source"]
    name = role["id"]
    does = ", ".join(role["main_agent"].get("does", []))
    nots = ", ".join(role["main_agent"].get("does_not", [])) or "nothing specified"
    rows = [f"Role: {name} ({source})", f"Main agent does: {does}.", f"Main agent does not: {nots}.", "Delegation:"]
    if role.get("delegation"):
        rows.extend(f"- {unit} → {target.get('providerInstanceId', '')}:{target.get('model', '')} {json.dumps(target.get('options', {}), sort_keys=True)} — {target.get('notes', '')}" for unit, target in role["delegation"].items())
    else:
        rows.append("- No delegation.")
    policies = role.get("policies", {})
    rows += ["Rules:", f"- parent_codes={policies.get('parent_codes')}; review_required={policies.get('review_required')}; ignore_cost={policies.get('ignore_cost')}; offpeak_deepseek={policies.get('offpeak_deepseek')}."]
    if policies.get("acceptance_checklist"):
        rows.extend(f"- {item}" for item in policies["acceptance_checklist"])
    delegation_summary = "; ".join(
        f"{unit} uses {target.get('providerInstanceId')} {target.get('model')}"
        for unit, target in role.get("delegation", {}).items()
        if target.get("providerInstanceId") and target.get("providerInstanceId") != "main"
    )
    announcement = f"Role: {name} ({source}) — {does.rstrip('.')}"
    if delegation_summary:
        announcement += f"; {delegation_summary}"
    rows.append(f"Announcement: {announcement}.")
    return "\n".join(rows)


def onboard(repo: Path) -> list[dict[str, Any]]:
    agents = repo / "AGENTS.md"
    text = agents.read_text(encoding="utf-8") if agents.is_file() else ""
    secret = repo / ".agent-secrets.json"
    checks = [
        ("toolkit-doctor", "python3 .opencode/skills/toolkit-doctor/scripts/doctor.py --repo .", False),
        ("project-classifier (dry-run first)", "python3 .opencode/skills/project-classifier/scripts/build_project_classifier.py --root . --dry-run", (repo / ".opencode/skills").is_dir() and any((repo / ".opencode/skills").glob("*-tool-router/SKILL.md"))),
        ("repo-guardrails", "python3 .opencode/skills/repo-guardrails/scripts/generate_agents_md.py --profile <project-profile.json> --repo .", "<!-- toolkit:guardrails:begin -->" in text and "<!-- toolkit:guardrails:end -->" in text),
        ("secret-preflight (if manifest exists)", "python3 .opencode/skills/secret-preflight/scripts/preflight.py check --manifest .agent-secrets.json", not secret.exists()),
        ("session-kickoff set", "python3 .opencode/skills/session-kickoff/scripts/kickoff.py set --preset planner --scope project --repo .", (repo / ".agent-role.json").is_file()),
    ]
    return [{"step": label, "command": command, "done": bool(done)} for label, command, done in checks]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("presets"); p.add_argument("--json", action="store_true")
    for command in ("show", "brief", "onboard"):
        p = sub.add_parser(command); p.add_argument("--repo", default="."); p.add_argument("--global-file"); p.add_argument("--json", action="store_true")
    p = sub.add_parser("set"); p.add_argument("--preset", required=True); p.add_argument("--set", action="append", default=[]); p.add_argument("--scope", choices=["project", "global"], default="project"); p.add_argument("--repo", default="."); p.add_argument("--global-file"); p.add_argument("--agents-md", action="store_true"); p.add_argument("--append", action="store_true")
    p = sub.add_parser("check"); p.add_argument("--capabilities", required=True); p.add_argument("--repo", default="."); p.add_argument("--strict", action="store_true")
    p = sub.add_parser("install-rules"); p.add_argument("--file", required=True); p.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        data = presets()
        if args.command == "presets":
            out = data if args.json else "\n".join(f"{p['id']}: {p['summary']}" for p in data["presets"])
        elif args.command == "set":
            choices = {p["id"]: p for p in data["presets"]}
            if args.preset not in choices: raise BadInput(f"unknown preset: {args.preset}")
            role = json.loads(json.dumps(choices[args.preset]))
            for override in args.set: apply_override(role, override)
            validate_role(role)
            destination = Path(args.repo).resolve() / ".agent-role.json" if args.scope == "project" else global_path(args.global_file)
            if args.agents_md:
                target = Path(args.repo).resolve() / "AGENTS.md"
                old = target.read_bytes() if target.exists() else b""
                new = update_marked(old, ROLE_BEGIN, ROLE_END, role_block(role), args.append)
            save_role(role, destination)
            if args.agents_md:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(new)
            out = f"saved {role['id']} role to {destination}"
        elif args.command in ("show", "brief"):
            active = resolve(Path(args.repo).resolve(), args.global_file)
            out = active if args.command == "show" or args.json else brief_text(active)
            if args.command == "brief" and args.json: out = {**active, "brief": brief_text(active)}
        elif args.command == "onboard":
            items = onboard(Path(args.repo).resolve())
            out = {"checklist": items} if args.json else "\n".join(f"{'[x]' if x['done'] else '[ ]'} {x['step']}: {x['command']}" for x in items)
        elif args.command == "check":
            active = resolve(Path(args.repo).resolve(), None)["role"]
            caps = read_json(Path(args.capabilities)); providers = caps.get("providers", [])
            issues = []
            for role, target in active.get("delegation", {}).items():
                if not target.get("providerInstanceId") or target.get("providerInstanceId") == "main":
                    continue
                provider = next((p for p in providers if p.get("providerInstanceId") == target.get("providerInstanceId")), None)
                if not provider: issues.append(f"{role}: provider {target.get('providerInstanceId')} missing")
                elif not provider.get("canRunChildTask") or provider.get("constraints"): issues.append(f"{role}: provider {target.get('providerInstanceId')} disabled or constrained")
                elif not any(m.get("id") == target.get("model") for m in provider.get("models", [])): issues.append(f"{role}: model {target.get('model')} missing")
            out = "\n".join(issues) if issues else "all delegation targets found and enabled"
            if issues:
                for issue in issues: print(f"warning: {issue}", file=sys.stderr)
                return 1 if args.strict else 0
        else:
            target = Path(args.file).expanduser(); target.parent.mkdir(parents=True, exist_ok=True)
            existing = target.read_bytes() if target.exists() else b""
            block = (ROOT / "assets" / "global-rules.md").read_bytes().rstrip(b"\n")
            updated = update_marked(existing, RULE_BEGIN, RULE_END, block, True)
            if args.check: return 0 if existing == updated else 1
            target.write_bytes(updated)
            out = f"installed rules in {target}"
        if isinstance(out, str): print(out)
        else: print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    except BadInput as exc:
        print(str(exc), file=sys.stderr); return 2
    except (OSError, UnicodeError, ValueError, KeyError) as exc:
        print(str(exc), file=sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main())
