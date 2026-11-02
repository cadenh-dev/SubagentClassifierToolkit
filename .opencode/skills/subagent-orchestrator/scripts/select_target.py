#!/usr/bin/env python3
"""Select a T3 subagent target from a live `orchestrator_capabilities` snapshot.

This is the deterministic "route only" helper. It does not call T3 and it does
not deploy anything. The skill normally reads `orchestrator_capabilities`
directly; this script exists for repeatable selection, tests, and callers that
want one canonical target.

Usage:
  select_target.py --capabilities caps.json --role implementation --depth 3
  select_target.py --capabilities caps.json --role review --depth 5 --ignore-cost --json

Output:
  providerInstanceId:model[#option=value,...]
  UNAVAILABLE

Exit codes:
  0  selected or unavailable
  2  bad input / unreadable file
"""

from __future__ import annotations

import argparse
import json
import sys
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
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"file not found: {path}", file=sys.stderr)
        raise SystemExit(2)
    except json.JSONDecodeError as e:
        print(f"invalid JSON in {path}: {e}", file=sys.stderr)
        raise SystemExit(2)


def value_rank(value: Any, index: int = 0) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        if value in VALUE_RANK:
            return float(VALUE_RANK[value])
        try:
            return float(value)
        except ValueError:
            pass
    # Unknown value: preserve the provider's declared order.
    return float(index)


def choose_effort(values: list[Any], has_default: bool, default: Any, depth: int) -> Any:
    """Choose the lowest effort that still fits the task depth."""
    if depth <= 2:
        if has_default and default is not None:
            return default
        return min(values, key=lambda v: value_rank(v, values.index(v)))
    if depth == 3:
        if "high" in values:
            return "high"
        if has_default and default is not None:
            return default
        return values[len(values) // 2]
    if depth == 4:
        for candidate in ("xhigh", "high"):
            if candidate in values:
                return candidate
        if has_default and default is not None:
            return default
        return values[len(values) // 2]
    return max(values, key=lambda v: value_rank(v, values.index(v)))


def choose_option(option: dict[str, Any], *, maximize: bool, depth: int) -> tuple[str, Any] | None:
    oid = option.get("id")
    values = option.get("values")
    if not oid or not isinstance(values, list) or not values:
        return None

    default = option.get("default", None)
    has_default = "default" in option

    if maximize:
        if oid == "fastMode":
            chosen = False if False in values else True
        elif oid == "thinking":
            chosen = True if True in values else default
        elif oid == "serviceTier":
            chosen = "default" if "default" in values else values[0]
        elif oid == "contextWindow":
            chosen = max(values, key=lambda v: value_rank(v, values.index(v)))
        else:
            chosen = max(values, key=lambda v: value_rank(v, values.index(v)))
    else:
        if oid == "fastMode":
            chosen = True if True in values else default
        elif oid == "thinking":
            chosen = default if has_default else (False if False in values else values[0])
        elif oid == "serviceTier":
            chosen = "default" if "default" in values else values[0]
        elif oid == "contextWindow":
            chosen = default if has_default else min(values, key=lambda v: value_rank(v, values.index(v)))
        elif oid in EFFORT_OPTIONS:
            chosen = choose_effort(values, has_default, default, depth)
        else:
            chosen = default if has_default else min(values, key=lambda v: value_rank(v, values.index(v)))

    if chosen is None:
        return None
    return oid, chosen


def score_for(policy: dict[str, Any], key: str, domain: str) -> tuple[float, float, bool, Any]:
    defaults = policy.get("defaults") or {}
    default_cost = float(defaults.get("cost_tier", 3))
    default_reasoning = float(defaults.get("reasoning", 2))
    rec = (policy.get("models") or {}).get(key)
    if not isinstance(rec, dict):
        return default_reasoning, default_cost, False, None

    reasoning = rec.get("reasoning", default_reasoning)
    if isinstance(reasoning, dict):
        score = reasoning.get(domain, reasoning.get("general", default_reasoning))
    else:
        score = reasoning
    try:
        score = float(score)
    except (TypeError, ValueError):
        score = default_reasoning

    try:
        cost = float(rec.get("cost_tier", default_cost))
    except (TypeError, ValueError):
        cost = default_cost

    context = rec.get("context", rec.get("context_window"))
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


def build_candidates(caps: dict[str, Any], policy: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    domain = args.domain or ROLE_DOMAIN[args.role]
    excludes = set(args.exclude or [])
    parent = None
    if args.parent_provider and args.parent_model:
        parent = f"{args.parent_provider}:{args.parent_model}"

    candidates: list[dict[str, Any]] = []
    for provider in caps.get("providers", []) or []:
        pid = provider.get("providerInstanceId")
        if not pid:
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
            key = f"{pid}:{mid}"
            if key in excludes or mid in excludes:
                continue

            score, cost, known, context = score_for(policy, key, domain)
            if score < args.depth:
                continue
            if args.max_cost_tier is not None and cost > args.max_cost_tier:
                continue
            if args.min_context is not None:
                if context is None:
                    continue
                try:
                    if float(context) < args.min_context:
                        continue
                except (TypeError, ValueError):
                    continue

            selected: list[dict[str, Any]] = []
            if not args.no_options:
                for option in model.get("options", []) or []:
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
    if ignore_cost:
        key = lambda c: (
            -c["score"],
            0 if c["known"] else 1,
            c["cost"],
            -(c["context"] or 0),
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
    ap.add_argument("--ignore-cost", action="store_true", help="maximize reasoning; ignore price and soft quota")
    ap.add_argument("--parent-provider")
    ap.add_argument("--parent-model")
    ap.add_argument("--min-context", type=int)
    ap.add_argument("--max-cost-tier", type=float)
    ap.add_argument("--exclude", action="append", default=[], help="canonical key or model id")
    ap.add_argument("--no-options", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--explain", action="store_true", help="write scoring details to stderr")
    args = ap.parse_args()

    caps = load_json(Path(args.capabilities))
    policy = load_json(Path(args.policy))
    if not isinstance(caps, dict):
        print("capabilities must be a JSON object", file=sys.stderr)
        return 2
    if not isinstance(policy, dict):
        print("policy must be a JSON object", file=sys.stderr)
        return 2

    domain = args.domain or ROLE_DOMAIN[args.role]
    candidates = build_candidates(caps, policy, args)
    chosen = select(candidates, ignore_cost=args.ignore_cost)

    if args.explain:
        print(
            f"route: role={args.role} domain={domain} depth={args.depth} "
            f"ignore_cost={args.ignore_cost} candidates={len(candidates)}",
            file=sys.stderr,
        )
        for c in sorted(candidates, key=lambda x: (-x["score"], x["cost"], x["key"])):
            print(
                f"  {c['key']:<48} score={c['score']:.1f} cost={c['cost']:.1f} "
                f"known={c['known']} parent={c['parent']}",
                file=sys.stderr,
            )

    if chosen is None:
        result = {
            "status": "unavailable",
            "reason": "no eligible target meets the requested depth",
            "role": args.role,
            "domain": domain,
            "depth": args.depth,
            "ignore_cost": args.ignore_cost,
        }
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print("UNAVAILABLE")
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
    }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(result["canonical"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
