#!/usr/bin/env python3
"""Select a T3 subagent target from a live `orchestrator_capabilities` snapshot.

This is the deterministic "route only" helper. It does not call T3 and it does
not deploy anything. The skill normally reads `orchestrator_capabilities`
directly; this script exists for repeatable selection, tests, and callers that
want one canonical target.

Usage:
  select_target.py --capabilities caps.json --role implementation --depth 3
  select_target.py --capabilities caps.json --role review --depth 5 --ignore-cost --json
  select_target.py --capabilities caps.json --role review --depth 4 --provider codex --model gpt-6.1-sol --option reasoningEffort=high

Output:
  providerInstanceId:model[#option=value,...]
  UNAVAILABLE
  BUDGET_EXCEEDED

Exit codes:
  0  selected or unavailable
  2  bad input / unreadable file
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

ROLE_DOMAIN = {
    "implementation": "code",
    "research": "research",
    "review": "review",
    "design": "architecture",
    "test": "test",
    "general": "general",
}
DOMAINS = ["general", "code", "architecture", "research", "review", "test", "docs", "security", "data"]
EFFORT_OPTIONS = {"variant", "reasoningEffort", "reasoning_effort", "effort", "thinking_level"}
ROLES = list(ROLE_DOMAIN)
DEFAULT_POLICY = Path(__file__).resolve().parent.parent / "data" / "routing-policy.json"


class InputError(ValueError):
    """Raised for structurally invalid capabilities, policy, or usage input."""


def finite_number(value: Any) -> float | None:
    """Return a finite float, or None when the value is not a real number."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def nonnegative_number(value: Any) -> float | None:
    """Return a finite, non-negative float, or None for invalid input."""
    number = finite_number(value)
    if number is None or number < 0:
        return None
    return number


def context_units(value: Any) -> float | None:
    """Parse a context size, accepting plain numbers and k/m suffixes."""
    number = nonnegative_number(value)
    if number is not None:
        return number
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    multiplier = 1
    if text.endswith("k"):
        multiplier = 1_000
        text = text[:-1]
    elif text.endswith("m"):
        multiplier = 1_000_000
        text = text[:-1]
    try:
        parsed = float(text) * multiplier
    except (ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


# Higher is stronger / more expensive. Unknown values use their position.
VALUE_RANK = {
    "low": 50,
    "medium": 60,
    "high": 70,
    "xhigh": 80,
    "max": 90,
    "ultracode": 94,
    "ultrathink": 95,
    "ultra": 100,
    "auto": 40,
    "default": 30,
    "256k": 256000,
    "300k": 300000,
    "500k": 500000,
    "1m": 1000000,
}


def load_json(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        print(f"file not found: {path}", file=sys.stderr)
        raise SystemExit(2)
    except (OSError, UnicodeDecodeError) as e:
        print(f"could not read {path}: {e}", file=sys.stderr)
        raise SystemExit(2)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        print(f"invalid JSON in {path}: {e}", file=sys.stderr)
        raise SystemExit(2)
    except (ValueError, RecursionError) as e:
        print(f"invalid JSON in {path}: {e}", file=sys.stderr)
        raise SystemExit(2)


def parse_option_pin(raw: str) -> tuple[str, Any]:
    """Parse an exact option pin such as variant=max or fastMode=false."""
    if "=" not in raw:
        raise InputError(f"--option must be id=value, got {raw!r}")
    option_id, value = raw.split("=", 1)
    option_id = option_id.strip()
    value = value.strip()
    if not option_id:
        raise InputError("--option id must be a non-empty string")
    if not value:
        raise InputError(f"--option {option_id!r} must have a value")
    lowered = value.lower()
    if lowered == "true":
        return option_id, True
    if lowered == "false":
        return option_id, False
    return option_id, value


def declared(value: Any, values: list[Any]) -> bool:
    """Type-aware membership: True must not match the integer 1."""
    return any(type(value) is type(item) and value == item for item in values)


def declared_index(value: Any, values: list[Any]) -> int:
    """Return the declared position of a value, or 0 when it is not declared."""
    for index, item in enumerate(values):
        if type(value) is type(item) and value == item:
            return index
    return 0


def value_rank(value: Any, index: int = 0) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        number = finite_number(value)
        return number if number is not None else float(index)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in VALUE_RANK:
            return float(VALUE_RANK[text])
        number = finite_number(text)
        if number is not None:
            return number
    # Unknown value: preserve the provider's declared order.
    return float(index)


def choose_effort(values: list[Any], has_default: bool, default: Any, depth: int) -> Any:
    """Choose the lowest effort that still fits the task depth."""
    if depth <= 2:
        if has_default and default is not None:
            return default
        return min(values, key=lambda v: value_rank(v, declared_index(v, values)))
    if depth == 3:
        if declared("high", values):
            return "high"
        if has_default and default is not None:
            return default
        return values[len(values) // 2]
    if depth == 4:
        for candidate in ("xhigh", "high"):
            if declared(candidate, values):
                return candidate
        if has_default and default is not None:
            return default
        return values[len(values) // 2]
    return max(values, key=lambda v: value_rank(v, declared_index(v, values)))


def choose_option(option: dict[str, Any], *, maximize: bool, depth: int) -> tuple[str, Any] | None:
    if not isinstance(option, dict):
        return None
    oid = option.get("id")
    values = option.get("values")
    if not isinstance(oid, str) or not oid.strip() or not isinstance(values, list) or not values:
        return None

    default = option.get("default", None)
    has_default = "default" in option

    if maximize:
        if oid == "fastMode":
            chosen = False if declared(False, values) else True
        elif oid == "thinking":
            chosen = True if declared(True, values) else default
        elif oid == "serviceTier":
            chosen = "default" if declared("default", values) else values[0]
        elif oid == "contextWindow":
            chosen = max(values, key=lambda v: value_rank(v, declared_index(v, values)))
        else:
            chosen = max(values, key=lambda v: value_rank(v, declared_index(v, values)))
    else:
        if oid == "fastMode":
            chosen = True if declared(True, values) else default
        elif oid == "thinking":
            chosen = default if has_default else (False if declared(False, values) else values[0])
        elif oid == "serviceTier":
            chosen = "default" if declared("default", values) else values[0]
        elif oid == "contextWindow":
            chosen = default if has_default else min(values, key=lambda v: value_rank(v, declared_index(v, values)))
        elif oid in EFFORT_OPTIONS:
            chosen = choose_effort(values, has_default, default, depth)
        else:
            chosen = default if has_default else min(values, key=lambda v: value_rank(v, declared_index(v, values)))

    # Never emit an option value that the live capability did not declare.
    if chosen is None:
        return None
    if not declared(chosen, values):
        if oid in EFFORT_OPTIONS and not maximize:
            chosen = choose_effort(values, False, None, depth)
        elif maximize:
            chosen = max(values, key=lambda v: value_rank(v, declared_index(v, values)))
        else:
            chosen = min(values, key=lambda v: value_rank(v, declared_index(v, values)))
    if chosen is None or not declared(chosen, values):
        return None
    return oid, chosen


def score_for(policy: dict[str, Any], key: str, domain: str) -> tuple[float, float, bool, Any]:
    defaults = policy.get("defaults")
    if not isinstance(defaults, dict):
        defaults = {}
    default_cost = nonnegative_number(defaults.get("cost_tier", 3))
    if default_cost is None:
        default_cost = 3.0
    default_reasoning = finite_number(defaults.get("reasoning", 2))
    if default_reasoning is None:
        default_reasoning = 2.0

    models = policy.get("models")
    if not isinstance(models, dict):
        models = {}
    rec = models.get(key)
    if not isinstance(rec, dict):
        return default_reasoning, default_cost, False, None

    reasoning = rec.get("reasoning", default_reasoning)
    if isinstance(reasoning, dict):
        score = reasoning.get(domain, reasoning.get("general", default_reasoning))
    else:
        score = reasoning
    score = finite_number(score)
    if score is None:
        score = default_reasoning

    cost = nonnegative_number(rec.get("cost_tier", default_cost))
    if cost is None:
        cost = default_cost

    context = context_units(rec.get("context", rec.get("context_window")))
    return score, cost, True, context


def canonical(provider: str, model: str, options: list[dict[str, Any]]) -> str:
    base = f"{provider}:{model}"
    if not options:
        return base
    parts = []
    for option in options:
        value = option.get("value")
        if isinstance(value, bool):
            value = "true" if value else "false"
        parts.append(f"{option['id']}={value}")
    return base + "#" + ",".join(parts)


def budget_estimate(policy: dict[str, Any], depth: int, override: float | None) -> float | None:
    if override is not None:
        number = nonnegative_number(override)
        if number is None:
            raise InputError("--estimate-units must be a finite non-negative number")
        return number

    budget = policy.get("budget")
    if budget is None:
        budget = {}
    if not isinstance(budget, dict):
        raise InputError("policy.budget must be an object")
    mapping = budget.get("estimated_units_by_depth")
    if mapping is None:
        mapping = {}
    if not isinstance(mapping, dict):
        raise InputError("policy.budget.estimated_units_by_depth must be an object")
    value = mapping.get(str(depth), mapping.get(depth))
    if value is None:
        fallback = {1: 2500, 2: 12000, 3: 50000, 4: 150000, 5: 400000}
        value = fallback.get(depth)
    if value is None:
        return None
    number = nonnegative_number(value)
    if number is None:
        raise InputError(
            f"policy budget estimate for depth {depth} must be a finite non-negative number"
        )
    return number


def usage_remaining(usage: dict[str, Any], provider: str, model: str) -> tuple[float | None, str | None]:
    """Return remaining budget for a target, preferring a model override."""
    models = usage.get("models")
    if not isinstance(models, dict):
        models = {}
    record = models.get(f"{provider}:{model}")
    if isinstance(record, dict):
        record = record.get("remaining")
    if record is not None:
        number = nonnegative_number(record)
        return (number, "model") if number is not None else (None, "model")

    providers = usage.get("providers")
    if not isinstance(providers, dict):
        providers = {}
    record = providers.get(provider)
    if isinstance(record, dict):
        record = record.get("remaining")
    if record is not None:
        number = nonnegative_number(record)
        return (number, "provider") if number is not None else (None, "provider")
    return None, None


def role_allowed(policy: dict[str, Any], role: str, key: str, model_id: str) -> tuple[bool, str | None]:
    """Enforce policy.role_restrictions (for example, Claude read-only)."""
    rules = policy.get("role_restrictions")
    if not isinstance(rules, list):
        rules = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        pattern = str(rule.get("match") or "").lower()
        if not pattern:
            continue
        if not (fnmatch(model_id.lower(), pattern) or fnmatch(key.lower(), pattern)):
            continue
        raw_allow = rule.get("allow_roles")
        if not isinstance(raw_allow, list):
            raw_allow = []
        allow = {str(r).lower() for r in raw_allow if isinstance(r, str)}
        if allow and role.lower() not in allow:
            return False, rule.get("reason") or f"{model_id} is not allowed for role {role}"
    return True, None


def web_patterns(policy: dict[str, Any], field: str) -> list[str]:
    section = policy.get("web_research")
    if not isinstance(section, dict):
        return []
    patterns = section.get(field)
    if not isinstance(patterns, list):
        return []
    return [str(p) for p in patterns]


def web_allowed(policy: dict[str, Any], key: str, model_id: str) -> bool:
    """Return whether this target may do delegated web research."""
    patterns = web_patterns(policy, "models")
    if not patterns:
        return True
    return any(
        fnmatch(model_id.lower(), p.lower()) or fnmatch(key.lower(), p.lower())
        for p in patterns
    )


def web_only(policy: dict[str, Any], key: str, model_id: str) -> bool:
    """Return whether this model is reserved for web-research units."""
    patterns = web_patterns(policy, "web_only")
    return any(
        fnmatch(model_id.lower(), p.lower()) or fnmatch(key.lower(), p.lower())
        for p in patterns
    )


def validate_capabilities(caps: dict[str, Any]) -> None:
    """Reject malformed capability snapshots instead of routing on partial data."""
    providers = caps.get("providers", [])
    if providers is None:
        providers = []
    if not isinstance(providers, list):
        raise InputError("capabilities.providers must be an array")
    for i, provider in enumerate(providers):
        here = f"capabilities.providers[{i}]"
        if not isinstance(provider, dict):
            raise InputError(f"{here} must be an object")
        pid = provider.get("providerInstanceId")
        if pid is not None and (not isinstance(pid, str) or not pid.strip()):
            raise InputError(f"{here}.providerInstanceId must be a non-empty string")
        for field in ("canRunChildTask", "canRunCrossProviderChildTask"):
            if field in provider and not isinstance(provider.get(field), bool):
                raise InputError(f"{here}.{field} must be a boolean")
        models = provider.get("models", [])
        if models is None:
            models = []
        if not isinstance(models, list):
            raise InputError(f"{here}.models must be an array")
        for j, model in enumerate(models):
            mhere = f"{here}.models[{j}]"
            if not isinstance(model, dict):
                raise InputError(f"{mhere} must be an object")
            mid = model.get("id")
            if mid is not None and (not isinstance(mid, str) or not mid.strip()):
                raise InputError(f"{mhere}.id must be a non-empty string")
            if "canRunChildTask" in model and not isinstance(model.get("canRunChildTask"), bool):
                raise InputError(f"{mhere}.canRunChildTask must be a boolean")
            options = model.get("options", [])
            if options is None:
                options = []
            if not isinstance(options, list):
                raise InputError(f"{mhere}.options must be an array")
            option_ids: set[str] = set()
            for k, option in enumerate(options):
                ohere = f"{mhere}.options[{k}]"
                if not isinstance(option, dict):
                    raise InputError(f"{ohere} must be an object")
                oid = option.get("id")
                if oid is not None:
                    if not isinstance(oid, str) or not oid.strip():
                        raise InputError(f"{ohere}.id must be a non-empty string")
                    if oid in option_ids:
                        raise InputError(f"{mhere}.options contains duplicate id {oid!r}")
                    option_ids.add(oid)
                values = option.get("values")
                if values is not None:
                    if not isinstance(values, list):
                        raise InputError(f"{ohere}.values must be an array")
                    for value_index, value in enumerate(values):
                        if not isinstance(value, (str, bool)):
                            raise InputError(
                                f"{ohere}.values[{value_index}] must be a string or boolean"
                            )
                if "default" in option:
                    default = option.get("default")
                    if not isinstance(default, (str, bool)):
                        raise InputError(f"{ohere}.default must be a string or boolean")


def validate_policy(policy: dict[str, Any]) -> None:
    models = policy.get("models")
    if models is None:
        models = {}
    if not isinstance(models, dict):
        raise InputError("policy.models must be an object")
    for key, record in models.items():
        here = f"policy.models[{key!r}]"
        if not isinstance(key, str) or not key.strip():
            raise InputError("policy.models keys must be non-empty strings")
        if not isinstance(record, dict):
            raise InputError(f"{here} must be an object")
        if "cost_tier" in record and nonnegative_number(record.get("cost_tier")) is None:
            raise InputError(f"{here}.cost_tier must be a finite non-negative number")
        if "reasoning" in record:
            reasoning = record.get("reasoning")
            if isinstance(reasoning, dict):
                for domain, score in reasoning.items():
                    if not isinstance(domain, str) or finite_number(score) is None:
                        raise InputError(
                            f"{here}.reasoning[{domain!r}] must be a finite number"
                        )
            elif finite_number(reasoning) is None:
                raise InputError(f"{here}.reasoning must be a number or an object")
        for field in ("context", "context_window"):
            if field in record and context_units(record.get(field)) is None:
                raise InputError(f"{here}.{field} must be a finite non-negative size")

    defaults = policy.get("defaults")
    if defaults is not None:
        if not isinstance(defaults, dict):
            raise InputError("policy.defaults must be an object")
        if "cost_tier" in defaults and nonnegative_number(defaults.get("cost_tier")) is None:
            raise InputError("policy.defaults.cost_tier must be a finite non-negative number")
        if "reasoning" in defaults and finite_number(defaults.get("reasoning")) is None:
            raise InputError("policy.defaults.reasoning must be a finite number")

    rules = policy.get("role_restrictions")
    if rules is not None:
        if not isinstance(rules, list):
            raise InputError("policy.role_restrictions must be an array")
        for index, rule in enumerate(rules):
            here = f"policy.role_restrictions[{index}]"
            if not isinstance(rule, dict):
                raise InputError(f"{here} must be an object")
            match = rule.get("match")
            if not isinstance(match, str) or not match.strip():
                raise InputError(f"{here}.match must be a non-empty string")
            allow_roles = rule.get("allow_roles")
            if not isinstance(allow_roles, list) or not allow_roles:
                raise InputError(f"{here}.allow_roles must be a non-empty array")
            for role_index, role in enumerate(allow_roles):
                if not isinstance(role, str) or not role.strip():
                    raise InputError(
                        f"{here}.allow_roles[{role_index}] must be a non-empty string"
                    )
            if "reason" in rule and not isinstance(rule.get("reason"), str):
                raise InputError(f"{here}.reason must be a string")

    web = policy.get("web_research")
    if web is not None:
        if not isinstance(web, dict):
            raise InputError("policy.web_research must be an object")
        for field in ("models", "web_only"):
            if field in web:
                patterns = web.get(field)
                if not isinstance(patterns, list):
                    raise InputError(f"policy.web_research.{field} must be an array")
                for index, pattern in enumerate(patterns):
                    if not isinstance(pattern, str) or not pattern.strip():
                        raise InputError(
                            f"policy.web_research.{field}[{index}] must be a non-empty string"
                        )

    budget = policy.get("budget")
    if budget is not None:
        if not isinstance(budget, dict):
            raise InputError("policy.budget must be an object")
        if "unit" in budget and (
            not isinstance(budget.get("unit"), str) or not budget.get("unit", "").strip()
        ):
            raise InputError("policy.budget.unit must be a non-empty string")
        mapping = budget.get("estimated_units_by_depth")
        if mapping is not None:
            if not isinstance(mapping, dict):
                raise InputError("policy.budget.estimated_units_by_depth must be an object")
            for depth, value in mapping.items():
                if nonnegative_number(value) is None:
                    raise InputError(
                        f"policy.budget.estimated_units_by_depth[{depth!r}] "
                        "must be a finite non-negative number"
                    )


def validate_usage(usage: dict[str, Any]) -> None:
    for section in ("providers", "models"):
        data = usage.get(section)
        if data is None:
            continue
        if not isinstance(data, dict):
            raise InputError(f"usage.{section} must be an object")
        for name, record in data.items():
            if isinstance(record, dict):
                if "remaining" in record and record.get("remaining") is not None:
                    if nonnegative_number(record.get("remaining")) is None:
                        raise InputError(
                            f"usage.{section}[{name!r}].remaining must be a finite non-negative number"
                        )
            elif record is not None:
                if nonnegative_number(record) is None:
                    raise InputError(
                        f"usage.{section}[{name!r}] must be a finite non-negative number or an object"
                    )


def build_candidates(caps: dict[str, Any], policy: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    domain = args.domain or ROLE_DOMAIN[args.role]
    excludes = set(args.exclude or [])
    pin_provider = getattr(args, "provider", None)
    pin_model = getattr(args, "model", None)
    parent = None
    if args.parent_provider and args.parent_model:
        parent = f"{args.parent_provider}:{args.parent_model}"

    candidates: list[dict[str, Any]] = []
    for provider in caps.get("providers", []) or []:
        pid = provider.get("providerInstanceId")
        if not pid:
            continue
        if pid in excludes:
            continue
        if pin_provider is not None and pid != pin_provider:
            continue
        if not provider.get("canRunChildTask"):
            continue
        if provider.get("constraints"):
            continue
        if args.parent_provider and pid != args.parent_provider:
            if not provider.get("canRunCrossProviderChildTask"):
                continue

        for model in provider.get("models", []) or []:
            mid = model.get("id")
            if not mid:
                continue
            if pin_model is not None and mid != pin_model:
                continue
            if "canRunChildTask" in model and model.get("canRunChildTask") is False:
                continue
            if model.get("constraints"):
                continue
            key = f"{pid}:{mid}"
            if key in excludes or mid in excludes:
                continue
            allowed, _reason = role_allowed(policy, args.role, key, mid)
            if not allowed:
                continue
            if getattr(args, "web", False) and not web_allowed(policy, key, mid):
                continue
            if not getattr(args, "web", False) and web_only(policy, key, mid):
                continue

            score, cost, known, context = score_for(policy, key, domain)
            if score < args.depth:
                continue
            if args.max_cost_tier is not None and cost > args.max_cost_tier:
                continue
            if args.min_context is not None:
                if context is None or context < args.min_context:
                    continue

            model_options = [
                option for option in (model.get("options", []) or []) if isinstance(option, dict)
            ]
            option_pins: dict[str, Any] = getattr(args, "option_pins", {})
            selected: list[dict[str, Any]] = []
            pin_ok = True
            for option_id, pinned_value in option_pins.items():
                option = next(
                    (item for item in model_options if item.get("id") == option_id),
                    None,
                )
                values = option.get("values") if option is not None else None
                if not isinstance(values, list) or not declared(pinned_value, values):
                    pin_ok = False
                    break
                selected.append({"id": option_id, "value": pinned_value})
            if not pin_ok:
                continue
            if not args.no_options:
                for option in model_options:
                    if option.get("id") in option_pins:
                        continue
                    chosen = choose_option(option, maximize=args.ignore_cost, depth=args.depth)
                    if chosen is not None:
                        selected.append({"id": chosen[0], "value": chosen[1]})

            candidates.append(
                {
                    "provider": pid,
                    "model": mid,
                    "key": key,
                    "score": score,
                    "cost": cost,
                    "known": known,
                    "context": context,
                    "options": selected,
                    "parent": key == parent,
                }
            )
    return candidates


def select(candidates: list[dict[str, Any]], *, ignore_cost: bool) -> dict[str, Any] | None:
    if not candidates:
        return None

    def remaining_rank(candidate: dict[str, Any]) -> float:
        remaining = candidate.get("remaining")
        return -remaining if isinstance(remaining, (int, float)) else 1.0

    if ignore_cost:
        key = lambda c: (
            -c["score"],
            0 if c["known"] else 1,
            c["cost"],
            -(c["context"] or 0),
            remaining_rank(c),
            c["provider"],
            c["model"],
        )
    else:
        key = lambda c: (
            0 if c["parent"] else 1,
            c["cost"],
            -c["score"],
            0 if c["known"] else 1,
            -(c["context"] or 0),
            remaining_rank(c),
            c["provider"],
            c["model"],
        )
    return sorted(candidates, key=key)[0]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--capabilities", required=True, help="JSON from orchestrator_capabilities")
    ap.add_argument("--policy", default=str(DEFAULT_POLICY), help="routing policy JSON")
    ap.add_argument("--role", choices=ROLES, required=True)
    ap.add_argument("--depth", type=int, choices=range(1, 6), required=True)
    ap.add_argument("--domain", choices=DOMAINS)
    ap.add_argument("--ignore-cost", action="store_true", help="maximize reasoning; ignore price tiers, not remaining usage")
    ap.add_argument("--provider", help="pin to an exact providerInstanceId; no fallback when unavailable")
    ap.add_argument("--model", help="pin to an exact model id; no fallback when unavailable")
    ap.add_argument(
        "--option",
        action="append",
        default=[],
        metavar="ID=VALUE",
        help="pin an exact option value; candidate must declare it",
    )
    ap.add_argument("--parent-provider")
    ap.add_argument("--parent-model")
    ap.add_argument("--min-context", type=int)
    ap.add_argument("--max-cost-tier", type=float)
    ap.add_argument("--usage", help="JSON remaining-usage snapshot; enables budget filtering")
    ap.add_argument("--estimate-units", type=float, help="estimated units for this unit; defaults to the policy by depth")
    ap.add_argument("--allow-unknown-budget", action="store_true", help="allow targets with no entry in the usage snapshot")
    ap.add_argument("--exclude", action="append", default=[], help="provider id, canonical key, or model id")
    ap.add_argument("--web", action="store_true", help="web research: restrict to policy web_research.models (Gemini Flash)")
    ap.add_argument("--no-options", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--explain", action="store_true", help="write scoring details to stderr")
    args = ap.parse_args()

    if bool(args.parent_provider) != bool(args.parent_model):
        print("--parent-provider and --parent-model must be supplied together", file=sys.stderr)
        return 2
    for flag, value in (("--provider", args.provider), ("--model", args.model)):
        if value is not None and not value.strip():
            print(f"{flag} must be a non-empty string", file=sys.stderr)
            return 2
    if args.option and args.no_options:
        print("--option cannot be combined with --no-options", file=sys.stderr)
        return 2
    option_pins: dict[str, Any] = {}
    try:
        for raw in args.option:
            option_id, value = parse_option_pin(raw)
            if option_id in option_pins:
                raise InputError(f"duplicate --option id {option_id!r}")
            option_pins[option_id] = value
    except InputError as e:
        print(str(e), file=sys.stderr)
        return 2
    args.option_pins = option_pins
    if args.estimate_units is not None and nonnegative_number(args.estimate_units) is None:
        print("--estimate-units must be a finite non-negative number", file=sys.stderr)
        return 2
    if args.min_context is not None and args.min_context < 0:
        print("--min-context must be a non-negative integer", file=sys.stderr)
        return 2
    if args.max_cost_tier is not None and nonnegative_number(args.max_cost_tier) is None:
        print("--max-cost-tier must be a finite non-negative number", file=sys.stderr)
        return 2

    caps = load_json(Path(args.capabilities))
    policy = load_json(Path(args.policy))
    if not isinstance(caps, dict):
        print("capabilities must be a JSON object", file=sys.stderr)
        return 2
    if not isinstance(policy, dict):
        print("policy must be a JSON object", file=sys.stderr)
        return 2
    try:
        validate_capabilities(caps)
        validate_policy(policy)
    except InputError as e:
        print(str(e), file=sys.stderr)
        return 2

    domain = args.domain or ROLE_DOMAIN[args.role]
    usage: dict[str, Any] | None = None
    budget_checked = False
    estimated_units: float | None = None
    budget_rejected = 0
    budget_policy = policy.get("budget")
    if not isinstance(budget_policy, dict):
        budget_policy = {}

    if args.usage:
        usage = load_json(Path(args.usage))
        if not isinstance(usage, dict):
            print("usage must be a JSON object", file=sys.stderr)
            return 2
        try:
            validate_usage(usage)
        except InputError as e:
            print(str(e), file=sys.stderr)
            return 2
        policy_unit = budget_policy.get("unit")
        usage_unit = usage.get("unit") or policy_unit
        if policy_unit and usage_unit and policy_unit != usage_unit:
            print(
                f"usage unit {usage_unit!r} does not match policy unit {policy_unit!r}",
                file=sys.stderr,
            )
            return 2
        try:
            estimated_units = budget_estimate(policy, args.depth, args.estimate_units)
        except InputError as e:
            print(str(e), file=sys.stderr)
            return 2
        if estimated_units is None:
            print("no budget estimate; pass --estimate-units", file=sys.stderr)
            return 2
        budget_checked = True

    candidates = build_candidates(caps, policy, args)
    if usage is not None:
        kept: list[dict[str, Any]] = []
        for candidate in candidates:
            remaining, source = usage_remaining(usage, candidate["provider"], candidate["model"])
            if remaining is None and not args.allow_unknown_budget:
                budget_rejected += 1
                continue
            if remaining is not None and estimated_units is not None and remaining < estimated_units:
                budget_rejected += 1
                continue
            candidate["remaining"] = remaining
            candidate["remaining_source"] = source
            kept.append(candidate)
        candidates = kept

    chosen = select(candidates, ignore_cost=args.ignore_cost)

    if args.explain:
        print(
            f"route: role={args.role} domain={domain} depth={args.depth} "
            f"ignore_cost={args.ignore_cost} candidates={len(candidates)} "
            f"budget_checked={budget_checked} budget_rejected={budget_rejected}",
            file=sys.stderr,
        )
        if budget_checked:
            print(f"  estimated_units={estimated_units}", file=sys.stderr)
        for c in sorted(candidates, key=lambda x: (-x["score"], x["cost"], x["key"])):
            remaining = c.get("remaining")
            print(
                f"  {c['key']:<48} score={c['score']:.1f} cost={c['cost']:.1f} "
                f"known={c['known']} parent={c['parent']} remaining={remaining}",
                file=sys.stderr,
            )

    budget = {
        "checked": budget_checked,
        "unit": (usage or {}).get("unit") or budget_policy.get("unit"),
        "estimated_units": estimated_units,
        "rejected": budget_rejected,
    }

    if chosen is None:
        budget_exceeded = budget_checked and budget_rejected > 0
        result = {
            "status": "budget_exceeded" if budget_exceeded else "unavailable",
            "reason": (
                "estimated usage exceeds remaining budget for every eligible target"
                if budget_exceeded
                else "no eligible target meets the requested depth"
            ),
            "role": args.role,
            "domain": domain,
            "depth": args.depth,
            "ignore_cost": args.ignore_cost,
            "budget": budget,
        }
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print("BUDGET_EXCEEDED" if budget_exceeded else "UNAVAILABLE")
        return 0

    result = {
        "status": "ok",
        "target": {
            "providerInstanceId": chosen["provider"],
            "model": chosen["model"],
            "options": chosen["options"],
        },
        "canonical": canonical(chosen["provider"], chosen["model"], chosen["options"]),
        "role": args.role,
        "domain": domain,
        "depth": args.depth,
        "ignore_cost": args.ignore_cost,
        "reasoning": chosen["score"],
        "cost_tier": chosen["cost"],
        "known": chosen["known"],
        "parent_preferred": chosen["parent"],
        "budget": budget,
        "remaining_units": chosen.get("remaining"),
        "remaining_source": chosen.get("remaining_source"),
        "rejected_candidates": [
            {
                "target": {
                    "providerInstanceId": candidate["provider"],
                    "model": candidate["model"],
                    "options": candidate["options"],
                },
                "rejected_because": "ranked below the selected target by routing policy",
            }
            for candidate in sorted(
                (item for item in candidates if item is not chosen),
                key=lambda item: (-item["score"], item["cost"], item["key"]),
            )
        ],
    }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(result["canonical"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
