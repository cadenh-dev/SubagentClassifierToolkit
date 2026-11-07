#!/usr/bin/env python3
"""Validate a subagent delegation plan.

This is a small, dependency-free checker for `assets/plan.schema.json`. It
catches the mistakes that cost the most: duplicate units, missing dependencies,
cycles, overlapping shared writes, and integration orders that violate the DAG.

Usage:
  validate_plan.py --plan plan.json
  validate_plan.py --plan plan.json --json
  validate_plan.py --plan plan.json --strict

Exit codes:
  0  valid (warnings allowed)
  1  invalid, or warnings with --strict
  2  bad input
"""

from __future__ import annotations

import argparse
import json
import math
import posixpath
import re
import sys
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

ROLES = {"research", "design", "implementation", "test", "review", "general"}
ISOLATION = {"shared", "worktree"}
MODES = {"async", "wait"}
ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
TOP_LEVEL_KEYS = {"goal", "mode", "ignore_cost", "budget", "units", "integration", "fallback", "repos"}
UNIT_KEYS = {
    "id",
    "title",
    "role",
    "objective",
    "depends_on",
    "isolation",
    "mode",
    "target",
    "write_scope",
    "acceptance",
    "commands",
    "deliverable",
    "brief",
    "repo",
}
TARGET_KEYS = {"providerInstanceId", "model", "options"}
OPTION_KEYS = {"id", "value"}
BUDGET_KEYS = {"unit", "estimated_units", "remaining_units", "note"}
INTEGRATION_KEYS = {"order", "tests", "review", "notes", "stack"}


def finite_nonnegative(value: Any) -> float | None:
    """Return a finite non-negative number, or None when the value is invalid."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def unit_dependencies(unit: dict[str, Any]) -> list[str]:
    """Return only the valid string dependencies, even for malformed input."""
    depends = unit.get("depends_on")
    if not isinstance(depends, list):
        return []
    return [dep for dep in depends if isinstance(dep, str) and dep.strip()]


def normalize_scope(scope: str) -> str:
    text = scope.strip().replace("\\", "/")
    if not text:
        return ""
    normalized = posixpath.normpath(text)
    return "" if normalized == "." else normalized.strip("/")


def scope_contains(parent: str, child: str) -> bool:
    parent = parent.strip("/")
    child = child.strip("/")
    return bool(parent) and (child == parent or child.startswith(parent + "/"))


def scope_has_glob(scope: str) -> bool:
    return any(char in scope for char in "*?[")


def literal_scope_prefix(scope: str) -> str:
    cuts = [scope.find(char) for char in "*?[" if char in scope]
    cut = min(cuts) if cuts else len(scope)
    return scope[:cut].strip("/")


def literal_scope_suffix(scope: str) -> str:
    """Return the literal text after the last glob metacharacter."""
    last = max((scope.rfind(char) for char in "*?["), default=-1)
    return scope[last + 1 :] if last >= 0 else ""


def scopes_overlap(left: str, right: str) -> bool:
    """Conservatively detect two write scopes that can name the same path."""
    left = normalize_scope(left)
    right = normalize_scope(right)
    if not left or not right:
        return False
    if left == right:
        return True

    left_glob = scope_has_glob(left)
    right_glob = scope_has_glob(right)
    if not left_glob and not right_glob:
        return scope_contains(left, right) or scope_contains(right, left)
    if not left_glob or not right_glob:
        literal, pattern = (left, right) if not left_glob else (right, left)
        return (
            fnmatch(literal, pattern)
            or scope_contains(literal, pattern)
            or scope_contains(pattern, literal)
        )

    # Both are globs. Use the literal prefix and suffix as a conservative
    # intersection test. Disjoint directories or disjoint literal extensions
    # are the only cases we can prove non-overlapping without evaluating
    # concrete paths.
    left_prefix = literal_scope_prefix(left)
    right_prefix = literal_scope_prefix(right)
    if (
        left_prefix
        and right_prefix
        and not left_prefix.startswith(right_prefix)
        and not right_prefix.startswith(left_prefix)
    ):
        return False

    # Character classes make suffix reasoning unreliable; stay conservative.
    if "[" in left or "[" in right:
        return True
    left_suffix = literal_scope_suffix(left)
    right_suffix = literal_scope_suffix(right)
    if (
        left_suffix
        and right_suffix
        and not left_suffix.endswith(right_suffix)
        and not right_suffix.endswith(left_suffix)
    ):
        return False
    return True


def load_plan(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        print(f"file not found: {path}", file=sys.stderr)
        raise SystemExit(2)
    except (OSError, UnicodeDecodeError) as e:
        print(f"could not read {path}: {e}", file=sys.stderr)
        raise SystemExit(2)
    try:
        data = json.loads(text)
    except (ValueError, RecursionError) as e:
        detail = getattr(e, "msg", None) or str(e)
        print(f"invalid JSON in {path}: {detail}", file=sys.stderr)
        raise SystemExit(2)
    if not isinstance(data, dict):
        print("plan must be a JSON object", file=sys.stderr)
        raise SystemExit(2)
    return data


def validate(plan: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    multi_repo = "repos" in plan
    repos = plan.get("repos", {})
    if multi_repo and not isinstance(repos, dict):
        errors.append("repos must be an object")
        repos = {}
    if isinstance(repos, dict):
        for key, item in repos.items():
            if not isinstance(item, dict): errors.append(f"repos.{key} must be an object"); continue
            if not isinstance(item.get("remote"), str) or not item["remote"].strip(): errors.append(f"repos.{key}.remote must be a non-empty string")
            for field in ("base_branch", "integration_branch", "local_path"):
                if field in item and not isinstance(item[field], str): errors.append(f"repos.{key}.{field} must be a string")
            if set(item) - {"remote", "base_branch", "integration_branch", "local_path"}: errors.append(f"repos.{key}: unknown key")

    for key in sorted(set(plan) - TOP_LEVEL_KEYS):
        errors.append(f"unknown top-level key {key!r}")

    goal = plan.get("goal")
    if not isinstance(goal, str) or not goal.strip():
        errors.append("goal must be a non-empty string")

    mode = plan.get("mode")
    if not isinstance(mode, str) or mode not in {"single", "delegated"}:
        errors.append("mode must be 'single' or 'delegated'")

    ignore_cost = plan.get("ignore_cost")
    if ignore_cost is not None and not isinstance(ignore_cost, bool):
        errors.append("ignore_cost must be a boolean")

    fallback = plan.get("fallback")
    if fallback is not None and not isinstance(fallback, str):
        errors.append("fallback must be a string")

    budget = plan.get("budget")
    if budget is not None:
        if not isinstance(budget, dict):
            errors.append("budget must be an object")
        else:
            for key in sorted(set(budget) - BUDGET_KEYS):
                errors.append(f"budget: unknown key {key!r}")
            if "unit" in budget and not isinstance(budget.get("unit"), str):
                errors.append("budget.unit must be a string")
            for field in ("estimated_units", "remaining_units"):
                if field in budget and finite_nonnegative(budget.get(field)) is None:
                    errors.append(f"budget.{field} must be a finite non-negative number")
            if "note" in budget and not isinstance(budget.get("note"), str):
                errors.append("budget.note must be a string")

    units = plan.get("units")
    if not isinstance(units, list):
        errors.append("units must be an array")
        units = []

    if mode == "delegated" and not units:
        errors.append("delegated mode requires at least one unit")
    if mode == "single" and units:
        warnings.append("mode is 'single' but units are present")

    ids: list[str] = []
    by_id: dict[str, dict[str, Any]] = {}
    for i, unit in enumerate(units):
        here = f"units[{i}]"
        if not isinstance(unit, dict):
            errors.append(f"{here} must be an object")
            continue

        for key in sorted(set(unit) - UNIT_KEYS):
            errors.append(f"{here}: unknown key {key!r}")

        uid = unit.get("id")
        if not isinstance(uid, str) or not uid:
            errors.append(f"{here}.id must be a non-empty string")
            continue
        if not ID_PATTERN.fullmatch(uid):
            errors.append(f"{uid}: id must match ^[a-z][a-z0-9-]{{0,63}}$")
        if uid in by_id:
            errors.append(f"duplicate unit id: {uid}")
            continue
        ids.append(uid)
        by_id[uid] = unit

        title = unit.get("title")
        if not isinstance(title, str) or not title.strip():
            errors.append(f"{uid}: title must be a non-empty string")

        role = unit.get("role")
        repo_key = unit.get("repo")
        if repo_key is not None and (not isinstance(repo_key, str) or repo_key not in repos):
            errors.append(f"{uid}: unknown repo key {repo_key!r}")
        if not isinstance(role, str) or role not in ROLES:
            errors.append(f"{uid}: role must be one of {sorted(ROLES)}")

        objective = unit.get("objective")
        if not isinstance(objective, str) or not objective.strip():
            errors.append(f"{uid}: objective must be a non-empty string")

        depends = unit.get("depends_on")
        if not isinstance(depends, list):
            errors.append(f"{uid}: depends_on must be an array")
        else:
            seen_depends: set[str] = set()
            for dep in depends:
                if not isinstance(dep, str):
                    errors.append(f"{uid}: depends_on entries must be strings")
                    continue
                if not dep.strip():
                    errors.append(f"{uid}: depends_on entries must be non-empty strings")
                    continue
                if dep in seen_depends:
                    errors.append(f"{uid}: duplicate dependency {dep!r}")
                    continue
                seen_depends.add(dep)

        isolation = unit.get("isolation")
        if not isinstance(isolation, str) or isolation not in ISOLATION:
            errors.append(f"{uid}: isolation must be 'shared' or 'worktree'")

        unit_mode = unit.get("mode")
        if not isinstance(unit_mode, str) or unit_mode not in MODES:
            errors.append(f"{uid}: mode must be 'async' or 'wait'")

        target = unit.get("target")
        if target is None:
            if mode == "delegated":
                errors.append(f"{uid}: delegated units need a target object")
        elif not isinstance(target, dict):
            errors.append(f"{uid}: target must be an object")
        else:
            for key in sorted(set(target) - TARGET_KEYS):
                errors.append(f"{uid}: target has unknown key {key!r}")
            pid = target.get("providerInstanceId")
            mid = target.get("model")
            if not isinstance(pid, str) or not pid.strip():
                errors.append(f"{uid}: target.providerInstanceId is required")
            if not isinstance(mid, str) or not mid.strip():
                errors.append(f"{uid}: target.model is required")
            options = target.get("options")
            if options is not None and not isinstance(options, (dict, list)):
                errors.append(f"{uid}: target.options must be an object or array")
            if isinstance(options, dict):
                for option_id, value in options.items():
                    if not isinstance(value, (str, bool, int, float)):
                        errors.append(f"{uid}: target.options[{option_id!r}] must be a scalar")
            if isinstance(options, list):
                for j, option in enumerate(options):
                    if not isinstance(option, dict) or "id" not in option or "value" not in option:
                        errors.append(f"{uid}: target.options[{j}] needs id and value")
                        continue
                    for key in sorted(set(option) - OPTION_KEYS):
                        errors.append(
                            f"{uid}: target.options[{j}] has unknown key {key!r}"
                        )
                    if not isinstance(option.get("id"), str) or not option.get("id", "").strip():
                        errors.append(f"{uid}: target.options[{j}].id must be a non-empty string")
                    if not isinstance(option.get("value"), (str, bool, int, float)):
                        errors.append(f"{uid}: target.options[{j}].value must be a scalar")

        write_scope = unit.get("write_scope", [])
        if multi_repo and role == "implementation" and isinstance(write_scope, list) and write_scope and not repo_key:
            errors.append(f"{uid}: implementation unit with write_scope must name a repo")
        if multi_repo and isinstance(write_scope, list) and any(isinstance(s, str) and re.match(r"^[^/]+:", s) for s in write_scope):
            errors.append(f"{uid}: write_scope must not encode a repo prefix; use the repo field")
        if not isinstance(write_scope, list):
            errors.append(f"{uid}: write_scope must be an array")
        else:
            seen_scope: set[str] = set()
            for scope in write_scope:
                if not isinstance(scope, str) or not scope.strip():
                    errors.append(f"{uid}: write_scope entries must be non-empty strings")
                    continue
                if scope in seen_scope:
                    errors.append(f"{uid}: duplicate write_scope entry {scope!r}")
                    continue
                seen_scope.add(scope)
            if isinstance(role, str) and role in {"research", "review", "design"} and write_scope:
                warnings.append(f"{uid}: {role} unit declares write_scope; confirm it needs writes")

        commands = unit.get("commands")
        if commands is not None:
            if not isinstance(commands, list):
                errors.append(f"{uid}: commands must be an array")
            elif any(not isinstance(command, str) for command in commands):
                errors.append(f"{uid}: commands entries must be strings")

        acceptance = unit.get("acceptance")
        if not isinstance(acceptance, list) or not acceptance:
            errors.append(f"{uid}: acceptance must contain at least one criterion")
        elif any(not isinstance(a, str) or not a.strip() for a in acceptance):
            errors.append(f"{uid}: acceptance entries must be non-empty strings")

        brief = unit.get("brief")
        if not isinstance(brief, str) or not brief.strip():
            errors.append(f"{uid}: brief must be a non-empty string")

        deliverable = unit.get("deliverable")
        if not isinstance(deliverable, str) or not deliverable.strip():
            errors.append(f"{uid}: deliverable must be a non-empty string")
        if multi_repo and any(isinstance(x, str) and re.search(r"watch.*(?:PR|pull request)|(?:PR|pull request).*watch", x, re.I) for x in (brief, deliverable)):
            errors.append(f"{uid}: child must not watch or babysit a PR")
        elif not multi_repo and any(isinstance(x, str) and re.search(r"watch.*(?:PR|pull request)|(?:PR|pull request).*watch", x, re.I) for x in (brief, deliverable)):
            warnings.append(f"{uid}: child brief/deliverable mentions watching a PR")

        if role == "implementation" and not write_scope and isolation == "worktree":
            warnings.append(f"{uid}: implementation worktree has no write_scope")

        if role == "review" and isolation == "worktree":
            warnings.append(f"{uid}: review unit uses a worktree; shared is usually enough")

    # Dependencies.
    for uid, unit in by_id.items():
        for dep in unit_dependencies(unit):
            if dep == uid:
                errors.append(f"{uid}: depends on itself")
            elif dep not in by_id:
                errors.append(f"{uid}: unknown dependency {dep!r}")

    # Cycle detection / topological order. Unknown dependencies are reported
    # above and must not masquerade as a cycle.
    done: set[str] = set()
    remaining = set(by_id)
    while remaining:
        ready = {
            uid
            for uid in remaining
            if all(
                dep in done
                for dep in unit_dependencies(by_id[uid])
                if dep in by_id
            )
        }
        if not ready:
            errors.append(f"dependency cycle among: {', '.join(sorted(remaining))}")
            break
        done.update(ready)
        remaining -= ready

    # Integration.
    integration = plan.get("integration")
    if not isinstance(integration, dict):
        errors.append("integration must be an object")
    else:
        for key in sorted(set(integration) - INTEGRATION_KEYS):
            errors.append(f"integration: unknown key {key!r}")
        order = integration.get("order")
        if not isinstance(order, list):
            errors.append("integration.order must be an array")
            order = []
        string_order = [uid for uid in order if isinstance(uid, str)]
        if len(string_order) != len(order):
            errors.append("integration.order entries must be strings")
        if len(string_order) != len(set(string_order)):
            errors.append("integration.order contains duplicates")
        for uid in string_order:
            if uid not in by_id:
                errors.append(f"integration.order references unknown unit {uid!r}")
        missing = [uid for uid in ids if uid not in string_order]
        if missing:
            errors.append(f"integration.order is missing: {', '.join(missing)}")

        seen: set[str] = set()
        for uid in string_order:
            if uid not in by_id:
                continue
            for dep in unit_dependencies(by_id[uid]):
                if dep in by_id and dep not in seen:
                    errors.append(f"integration.order puts {uid} before its dependency {dep}")
            seen.add(uid)

        deps = integration.get("tests")
        if not isinstance(deps, list):
            errors.append("integration.tests must be an array")
        elif any(not isinstance(command, str) for command in deps):
            errors.append("integration.tests entries must be strings")
        notes = integration.get("notes")
        if notes is not None and not isinstance(notes, str):
            errors.append("integration.notes must be a string")
        review = integration.get("review")
        if not isinstance(review, bool):
            errors.append("integration.review must be a boolean")

        stack = integration.get("stack")
        if stack is not None:
            if not multi_repo: errors.append("integration.stack requires repos")
            if not isinstance(stack, list): errors.append("integration.stack must be an array"); stack = []
            seen_stack: set[str] = set(); produced: dict[str, set[str]] = {}; prior: set[str] = set()
            for index, entry in enumerate(stack):
                label = f"integration.stack[{index}]"
                if not isinstance(entry, dict): errors.append(f"{label} must be an object"); continue
                if set(entry) != {"repo", "units", "pr_base"}: errors.append(f"{label} needs repo, units, and pr_base")
                rk, members, base = entry.get("repo"), entry.get("units"), entry.get("pr_base")
                if rk not in repos: errors.append(f"{label}: unknown repo key {rk!r}")
                if not isinstance(members, list) or not members or any(not isinstance(x, str) for x in members): errors.append(f"{label}.units must be a non-empty string array"); members = []
                for member in members:
                    if member in seen_stack: errors.append(f"integration.stack contains unit {member!r} more than once")
                    seen_stack.add(member)
                    if member not in by_id: errors.append(f"{label} references unknown unit {member!r}"); continue
                    if by_id[member].get("repo") != rk: errors.append(f"{member}: stack repo does not match unit repo")
                    for dep in unit_dependencies(by_id[member]):
                        if dep in by_id and dep not in prior: errors.append(f"integration.stack puts {member} before dependency {dep}")
                valid_bases = {repos.get(rk, {}).get("base_branch"), repos.get(rk, {}).get("integration_branch")} | produced.get(rk, set()) if isinstance(repos.get(rk), dict) else set()
                if base not in valid_bases: errors.append(f"{label}.pr_base must be repo base/integration branch or an earlier unit branch")
                for member in members:
                    if member in by_id: produced.setdefault(rk, set()).add(member)
                prior.update(members)

        # Two children must not write the same path in a shared checkout.
        shared_writers = [
            (uid, unit)
            for uid, unit in by_id.items()
            if unit.get("isolation") == "shared"
            and isinstance(unit.get("write_scope"), list)
            and unit.get("write_scope")
        ]
        for left_index, (left_id, left_unit) in enumerate(shared_writers):
            left_scopes = [
                scope for scope in left_unit.get("write_scope", []) if isinstance(scope, str)
            ]
            for right_id, right_unit in shared_writers[left_index + 1:]:
                if multi_repo and left_unit.get("repo") != right_unit.get("repo"):
                    continue
                right_scopes = [
                    scope for scope in right_unit.get("write_scope", []) if isinstance(scope, str)
                ]
                overlap = next(
                    (
                        (left_scope, right_scope)
                        for left_scope in left_scopes
                        for right_scope in right_scopes
                        if scopes_overlap(left_scope, right_scope)
                    ),
                    None,
                )
                if overlap is not None:
                    errors.append(
                        f"{left_id} and {right_id} share a shared write scope "
                        f"({overlap[0]!r} overlaps {overlap[1]!r}); use worktrees or sequence them"
                    )

        implementations = [u for u in units if isinstance(u, dict) and u.get("role") == "implementation"]
        shared_impl = [u.get("id") for u in implementations if u.get("isolation") == "shared"]
        if len(shared_impl) > 1:
            warnings.append(
                "multiple implementation units use shared isolation: "
                + ", ".join(str(x) for x in shared_impl)
                + "; use worktrees or sequence them"
            )

    return errors, warnings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true", help="treat warnings as errors")
    args = ap.parse_args()

    plan = load_plan(Path(args.plan))
    errors, warnings = validate(plan)
    ok = not errors and not (args.strict and warnings)

    result = {"ok": ok, "errors": errors, "warnings": warnings}
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if ok:
            print(f"OK {len(plan.get('units', []))} units")
        else:
            print("INVALID")
        for error in errors:
            print(f"  error: {error}")
        for warning in warnings:
            print(f"  warning: {warning}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
