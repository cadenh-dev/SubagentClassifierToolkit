#!/usr/bin/env python3
"""Join live orchestrator capabilities with a small compatibility reference.

Exit codes: 0 success, 1 validation failure or strict filter with no matches,
2 invalid input.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_CONSTRAINTS = Path(__file__).resolve().parent.parent / "data" / "constraints.json"
FIELDS = ("context_window", "max_output", "tool_calling", "image_input", "structured_output", "streaming")
BOOLEAN_FIELDS = ("tool_calling", "image_input", "structured_output", "streaming")


class InputError(ValueError):
    """Raised for malformed command input or data files."""


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InputError(f"could not read {path}: {error}") from None


def validate_constraints(data: Any) -> list[str]:
    """Return validation errors for a constraints document."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["document must be an object"]
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        errors.append("schema_version must be integer 1")
    models = data.get("models")
    if not isinstance(models, list):
        return errors + ["models must be an array"]
    seen: set[str] = set()
    for index, item in enumerate(models):
        where = f"models[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{where} must be an object")
            continue
        for key in ("provider_hint", "model", "notes"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                errors.append(f"{where}.{key} must be a non-empty string")
        model = item.get("model")
        if isinstance(model, str):
            if model in seen:
                errors.append(f"duplicate model id {model!r}")
            seen.add(model)
        for key in ("context_window", "max_output"):
            value = item.get(key)
            if value != "unknown" and (type(value) is not int or value < 0):
                errors.append(f"{where}.{key} must be a non-negative integer or 'unknown'")
        for key in BOOLEAN_FIELDS:
            if item.get(key) not in (True, False, "unknown") or type(item.get(key)) not in (bool, str):
                errors.append(f"{where}.{key} must be boolean or 'unknown'")
        sources = item.get("sources")
        if not isinstance(sources, list) or not sources or any(
            not isinstance(source, str) or not source.strip() for source in sources
        ):
            errors.append(f"{where}.sources must contain at least one non-empty URL")
        elif any(not re.match(r"^https?://\S+$", source) for source in sources):
            errors.append(f"{where}.sources entries must be URLs")
        retrieved = item.get("retrieved")
        try:
            if not isinstance(retrieved, str) or date.fromisoformat(retrieved).isoformat() != retrieved:
                raise ValueError
        except ValueError:
            errors.append(f"{where}.retrieved must be an ISO date (YYYY-MM-DD)")
        if type(item.get("verified")) is not bool:
            errors.append(f"{where}.verified must be boolean")
    return errors


def load_constraints(path: Path) -> list[dict[str, Any]]:
    data = read_json(path)
    errors = validate_constraints(data)
    if errors:
        raise InputError("invalid constraints: " + "; ".join(errors))
    return data["models"]


def local_match(model_id: str, models: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Match exact model ids first, then a namespaced live id's final segment."""
    for item in models:
        if item["model"] == model_id:
            return item
    stripped = model_id.split("/", 1)[1] if "/" in model_id else model_id
    return next((item for item in models if item["model"] == stripped), None)


def provider_disabled(provider: dict[str, Any]) -> bool:
    constraints = provider.get("constraints", [])
    explicitly_disabled = any(
        isinstance(item, str) and "disabled" in item.lower()
        for item in constraints
    )
    return bool(provider.get("disabled")) or explicitly_disabled


def join_rows(capabilities: Any, constraints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Produce live provider/model rows and local-only rows."""
    if not isinstance(capabilities, dict) or not isinstance(capabilities.get("providers"), list):
        raise InputError("capabilities must be an object with a providers array")
    rows: list[dict[str, Any]] = []
    found: set[str] = set()
    for provider in capabilities["providers"]:
        if not isinstance(provider, dict) or not isinstance(provider.get("providerInstanceId"), str):
            raise InputError("each provider needs providerInstanceId")
        models = provider.get("models", [])
        if not isinstance(models, list):
            raise InputError(f"provider {provider['providerInstanceId']} models must be an array")
        disabled = provider_disabled(provider)
        for live_model in models:
            if not isinstance(live_model, dict) or not isinstance(live_model.get("id"), str):
                raise InputError(f"provider {provider['providerInstanceId']} has malformed model")
            model_id = live_model["id"]
            local = local_match(model_id, constraints)
            if local:
                found.add(local["model"])
            model_disabled = bool(live_model.get("disabled"))
            row: dict[str, Any] = {
                "providerInstanceId": provider["providerInstanceId"],
                "model": model_id,
                "status": "joined" if local else "live-only",
                "availability": "disabled" if disabled or model_disabled else "available",
                "disabled": disabled or model_disabled,
                "canRunChildTask": provider.get("canRunChildTask", "unknown"),
                "constraints": provider.get("constraints", []),
                "options": live_model.get("options", []),
                "live": live_model,
            }
            for field in FIELDS:
                row[field] = (live_model[field] if field in live_model else local.get(field, "unknown") if local else "unknown")
            if local:
                row.update({"provider_hint": local["provider_hint"], "notes": local["notes"], "sources": local["sources"], "retrieved": local["retrieved"], "verified": local["verified"]})
            rows.append(row)
    for local in constraints:
        if local["model"] not in found:
            row = {"providerInstanceId": None, "model": local["model"], "status": "local-only", "availability": "not available", "disabled": False, "canRunChildTask": False, "constraints": [], "options": [], "live": None}
            row.update({field: local[field] for field in FIELDS})
            row.update({"provider_hint": local["provider_hint"], "notes": local["notes"], "sources": local["sources"], "retrieved": local["retrieved"], "verified": local["verified"]})
            rows.append(row)
    return rows


def filter_rows(rows: list[dict[str, Any]], requirements: list[str], min_context: int | None, min_output: int | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    passing: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    for row in rows:
        if row["availability"] != "available" or row["canRunChildTask"] is not True:
            continue
        missing = [name for name in requirements if row.get(name, "unknown") == "unknown"]
        if min_context is not None and row["context_window"] == "unknown":
            missing.append("context_window")
        if min_output is not None and row["max_output"] == "unknown":
            missing.append("max_output")
        if missing:
            unknown.append({"target": row, "unknown": sorted(set(missing)), "status": "unknown — verify"})
            continue
        if any(row.get(name) is not True for name in requirements):
            continue
        if min_context is not None and row["context_window"] < min_context:
            continue
        if min_output is not None and row["max_output"] < min_output:
            continue
        passing.append(row)
    return passing, unknown


def emit(value: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(value, indent=2, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    show = sub.add_parser("show", help="join live capabilities and local constraints")
    show.add_argument("--capabilities", required=True, type=Path)
    show.add_argument("--constraints", type=Path, default=DEFAULT_CONSTRAINTS)
    show.add_argument("--json", action="store_true")
    filt = sub.add_parser("filter", help="filter available targets by requirements")
    filt.add_argument("--capabilities", required=True, type=Path)
    filt.add_argument("--constraints", type=Path, default=DEFAULT_CONSTRAINTS)
    filt.add_argument("--require", default="", help="comma-separated capability fields")
    filt.add_argument("--min-context", type=int)
    filt.add_argument("--min-output", type=int)
    filt.add_argument("--strict", action="store_true")
    filt.add_argument("--json", action="store_true")
    stale = sub.add_parser("stale", help="list old or unverified local entries")
    stale.add_argument("--constraints", type=Path, default=DEFAULT_CONSTRAINTS)
    stale.add_argument("--max-age-days", type=int, default=60)
    stale.add_argument("--json", action="store_true")
    validate = sub.add_parser("validate", help="validate local constraints data")
    validate.add_argument("--constraints", type=Path, default=DEFAULT_CONSTRAINTS)
    validate.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            errors = validate_constraints(read_json(args.constraints))
            emit({"valid": not errors, "errors": errors}, args.json)
            return 0 if not errors else 1
        constraints = load_constraints(args.constraints)
        if args.command == "stale":
            if args.max_age_days < 0:
                raise InputError("--max-age-days must be non-negative")
            today = datetime.now(timezone.utc).date()
            entries = [item for item in constraints if not item["verified"] or (today - date.fromisoformat(item["retrieved"])).days > args.max_age_days]
            emit({"max_age_days": args.max_age_days, "entries": entries}, args.json)
            return 0
        capabilities = read_json(args.capabilities)
        rows = join_rows(capabilities, constraints)
        if args.command == "show":
            emit({"models": rows}, args.json)
            return 0
        requirements = [part.strip() for part in args.require.split(",") if part.strip()]
        invalid = sorted(set(requirements) - set(BOOLEAN_FIELDS))
        if invalid:
            raise InputError("--require supports: " + ", ".join(BOOLEAN_FIELDS))
        if args.min_context is not None and args.min_context < 0 or args.min_output is not None and args.min_output < 0:
            raise InputError("minimum values must be non-negative")
        targets, unknown = filter_rows(rows, requirements, args.min_context, args.min_output)
        emit({"targets": targets, "unknown": unknown}, args.json)
        return 1 if args.strict and not targets else 0
    except InputError as error:
        print(f"compat-matrix: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
