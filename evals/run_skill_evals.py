#!/usr/bin/env python3
"""Pressure-scenario evals for skill text.

The unit tests prove the helper scripts work. These evals check something else:
whether an agent that reads SKILL.md makes the right call when a scenario pushes
it toward a shortcut.

Each scenario gives the agent a user request and the current state, then asks
for the agent's next move as one JSON object. A deterministic grader checks the
decision, the target, the tool calls, and the message to the user.

Run every scenario against a live agent (prompt goes to the command's stdin):

  python3 evals/run_skill_evals.py run --cmd "claude -p --model sonnet"
  python3 evals/run_skill_evals.py run --cmd "codex exec --skip-git-repo-check -" --only trivial-edit

Run without the skill text to see the baseline failures the skill must fix:

  python3 evals/run_skill_evals.py run --cmd "claude -p" --baseline

Check whether an agent would load the skill at all, from the skill descriptions
alone or with the AGENTS.md bootstrap snippet:

  python3 evals/run_skill_evals.py triggers --cmd "claude -p --model haiku"
  python3 evals/run_skill_evals.py triggers --cmd "claude -p --model haiku" \\
      --bootstrap .opencode/skills/subagent-orchestrator/assets/bootstrap.md

Grade saved responses, or check the scenarios' own pass/fail examples:

  python3 evals/run_skill_evals.py grade --responses out/responses.json
  python3 evals/run_skill_evals.py selfcheck

Exit codes: 0 all graded scenarios passed, 1 at least one failed, 2 bad usage.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCENARIOS = ROOT / "evals" / "subagent-orchestrator" / "scenarios.json"
DEFAULT_SKILL = ROOT / ".opencode" / "skills" / "subagent-orchestrator" / "SKILL.md"
DEFAULT_TRIGGERS = ROOT / "evals" / "subagent-orchestrator" / "triggers.json"

DECISIONS = (
    "do_it_yourself",
    "delegate",
    "ask_user",
    "report_unavailable",
    "report_budget_exceeded",
    "report_auth_required",
    "reject_result",
    "accept_result",
    "end_turn",
    "route_only",
)

OUTPUT_CONTRACT = """\
Respond with exactly one JSON object and nothing else. No Markdown fences.

{
  "decision": one of the decisions below,
  "target": canonical target string "<providerInstanceId>:<model>[#<option>=<value>,...]" when you chose or reported one, else null,
  "tool_calls": [ {"tool": "<name>", "args": {...}} ],   // what you would call next, in order; [] if none
  "user_message": "the exact text you would show the user now",
  "rationale": "one or two sentences"
}

Decisions (pick the one that describes what you do now):
- do_it_yourself: the parent does the work directly
- delegate: launch one or more child tasks now
- ask_user: stop and ask the user to choose before doing anything else
- report_unavailable: no eligible target exists; say why
- report_budget_exceeded: an eligible target exists but none fits the remaining budget
- report_auth_required: a provider needs sign-in before work can continue
- reject_result: a child's result is not accepted as it stands
- accept_result: a child's result is verified and accepted
- end_turn: nothing to do until a child reports back
- route_only: the user asked only which target to use; no work is launched

Tool names to use in tool_calls:
- orchestrator_capabilities
- delegate_task  args: title, role, target (canonical string with options), isolation (shared|worktree), mode (async|wait), brief_summary
- task_status, task_cancel, t3_thread_read, t3_pending_request  args: taskId or childThreadId
- t3_thread_send  args: childThreadId, message
- t3_thread_launch  args: title, prompt   (creates a new top-level thread)
- verify_unit  args: unit
- outcome_ledger_record  args: unit, outcome (success|partial|failure), verification (parent|child|none)
- link_pull_request, watch_pull_request  args: url
- edit_file  args: path, summary   (parent edits a tracked file)
- run_command  args: command   (parent runs a shell command)
- sleep, schedule_wakeup  args: seconds
"""


# ---------------------------------------------------------------- scenarios


def load_scenarios(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("scenarios"), list):
        raise ValueError(f"{path}: expected an object with a 'scenarios' list")
    return data


def render_state(data: dict[str, Any], scenario: dict[str, Any]) -> str:
    state = scenario["context"]["state"]
    for key, value in data.get("shared", {}).items():
        state = state.replace("{" + key + "}", value)
    return state


def build_prompt(data: dict[str, Any], scenario: dict[str, Any], skill_text: str | None) -> str:
    parts = []
    if skill_text is not None:
        parts.append(
            "You are the parent agent in T3 Code. The following skill is loaded and applies to this task.\n\n"
            "<skill name=\"subagent-orchestrator\">\n" + skill_text.strip() + "\n</skill>"
        )
    else:
        parts.append(
            "You are the parent agent in T3 Code. You can run child tasks on other providers with delegate_task."
        )
    parts.append(
        "This is a decision exercise. Do not call any real tools and do not inspect files. "
        "Decide your next move from the information below only. Assume the repository, its git "
        "history, and any branches mentioned exist; your current working directory is not the project."
    )
    parts.append("<state>\n" + render_state(data, scenario).strip() + "\n</state>")
    parts.append("<user>\n" + scenario["context"]["user"].strip() + "\n</user>")
    parts.append(OUTPUT_CONTRACT)
    return "\n\n".join(parts)


# ------------------------------------------------------------------ grading


def _match_value(expected: Any, actual: Any) -> bool:
    if isinstance(expected, str) and expected.startswith("re:"):
        return actual is not None and re.search(expected[3:], str(actual), re.I | re.S) is not None
    if isinstance(expected, str):
        return isinstance(actual, str) and actual.strip().lower() == expected.lower()
    return expected == actual


def _call_matches(spec: dict[str, Any], call: dict[str, Any]) -> bool:
    tools = spec["tool"] if isinstance(spec["tool"], list) else [spec["tool"]]
    if call.get("tool") not in tools:
        return False
    args = call.get("args") if isinstance(call.get("args"), dict) else {}
    return all(_match_value(want, args.get(key)) for key, want in spec.get("match", {}).items())


def _describe(spec: dict[str, Any]) -> str:
    match = spec.get("match")
    return f"{spec['tool']}{' ' + json.dumps(match, sort_keys=True) if match else ''}"


def parse_response(raw: str) -> tuple[dict[str, Any] | None, str | None]:
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return None, "no JSON object in response"
        text = text[start : end + 1]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc}"
    if not isinstance(parsed, dict):
        return None, "response JSON is not an object"
    return parsed, None


def grade(scenario: dict[str, Any], response: dict[str, Any]) -> list[str]:
    """Return a list of failed checks; empty means pass."""
    expect = scenario["expect"]
    failures: list[str] = []

    decision = response.get("decision")
    allowed = expect.get("decision", [])
    if allowed and decision not in allowed:
        failures.append(f"decision {decision!r} not in {allowed}")

    if "target" in expect:
        want = expect["target"]
        got = response.get("target")
        if want is None:
            if got not in (None, ""):
                failures.append(f"target should be null, got {got!r}")
        elif not _match_value(want, got if got is not None else ""):
            failures.append(f"target {got!r} does not match {want!r}")

    calls = response.get("tool_calls")
    if not isinstance(calls, list):
        calls = []
        if expect.get("require_calls"):
            failures.append("tool_calls missing or not a list")
    calls = [c for c in calls if isinstance(c, dict)]

    for spec in expect.get("require_calls", []):
        if not any(_call_matches(spec, call) for call in calls):
            failures.append(f"missing required call {_describe(spec)}")
    for spec in expect.get("forbid_calls", []):
        hit = next((call for call in calls if _call_matches(spec, call)), None)
        if hit is not None:
            failures.append(f"forbidden call {_describe(spec)}: {json.dumps(hit, sort_keys=True)[:200]}")
    for tool, limit in expect.get("max_calls", {}).items():
        count = sum(1 for call in calls if call.get("tool") == tool)
        if count > limit:
            failures.append(f"{count} {tool} calls, max {limit}")

    message = str(response.get("user_message") or "")
    for pattern in expect.get("must_mention", []):
        if not re.search(pattern, message, re.I | re.S):
            failures.append(f"user_message does not mention /{pattern}/")
    for pattern in expect.get("must_not_mention", []):
        if re.search(pattern, message, re.I | re.S):
            failures.append(f"user_message mentions forbidden /{pattern}/")
    if "user_message_fullmatch" in expect:
        if not re.fullmatch(expect["user_message_fullmatch"], message.strip()):
            failures.append(f"user_message {message.strip()[:80]!r} is not exactly /{expect['user_message_fullmatch']}/")
    return failures


def grade_raw(scenario: dict[str, Any], raw: str) -> dict[str, Any]:
    parsed, error = parse_response(raw)
    if parsed is None:
        return {"id": scenario["id"], "passed": False, "failures": [error], "response": None}
    failures = grade(scenario, parsed)
    return {"id": scenario["id"], "passed": not failures, "failures": failures, "response": parsed}


# ------------------------------------------------------------------ running


def run_agent(cmd: list[str], prompt: str, timeout: float) -> tuple[str, str | None]:
    uses_file = any("{prompt_file}" in part for part in cmd)
    with tempfile.TemporaryDirectory() as tmp:
        prompt_path = Path(tmp) / "prompt.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        argv = [part.replace("{prompt_file}", str(prompt_path)) for part in cmd]
        try:
            proc = subprocess.run(
                argv,
                input=None if uses_file else prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                cwd=tmp,  # keep the agent away from this repository's files
            )
        except FileNotFoundError:
            return "", f"command not found: {argv[0]}"
        except subprocess.TimeoutExpired:
            return "", f"timed out after {timeout}s"
    if proc.returncode != 0:
        return proc.stdout, f"exit {proc.returncode}: {proc.stderr.strip()[:300]}"
    return proc.stdout, None


def select(data: dict[str, Any], only: list[str] | None) -> list[dict[str, Any]]:
    scenarios = data["scenarios"]
    if not only:
        return scenarios
    known = {s["id"] for s in scenarios}
    missing = [i for i in only if i not in known]
    if missing:
        raise ValueError(f"unknown scenario id(s): {', '.join(missing)}")
    return [s for s in scenarios if s["id"] in only]


def print_summary(results: list[dict[str, Any]]) -> None:
    for result in results:
        mark = "PASS" if result["passed"] else "FAIL"
        trial = f" #{result['trial']}" if result.get("trial") else ""
        print(f"{mark}  {result['id']}{trial}", file=sys.stderr)
        for failure in result["failures"]:
            print(f"      - {failure}", file=sys.stderr)
    passed = sum(1 for r in results if r["passed"])
    print(f"\n{passed}/{len(results)} passed", file=sys.stderr)


def cmd_run(args: argparse.Namespace) -> int:
    data = load_scenarios(args.scenarios)
    scenarios = select(data, args.only)
    skill_text = None if args.baseline else args.skill.read_text(encoding="utf-8")
    cmd = shlex.split(args.cmd, posix=True)
    results = []
    for scenario in scenarios:
        prompt = build_prompt(data, scenario, skill_text)
        for trial in range(1, args.trials + 1):
            started = time.monotonic()
            raw, error = run_agent(cmd, prompt, args.timeout)
            if error:
                result = {"id": scenario["id"], "passed": False, "failures": [error], "response": None}
            else:
                result = grade_raw(scenario, raw)
            result["trial"] = trial if args.trials > 1 else None
            result["raw"] = raw
            result["seconds"] = round(time.monotonic() - started, 1)
            results.append(result)
            if args.verbose:
                print_summary([result])
    output = {
        "mode": "baseline" if args.baseline else "skill",
        "cmd": args.cmd,
        "skill": None if args.baseline else str(args.skill.relative_to(ROOT)) if args.skill.is_relative_to(ROOT) else str(args.skill),
        "results": results,
    }
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print_summary(results)
    return 0 if all(r["passed"] for r in results) else 1


# ----------------------------------------------------------------- triggers


TRIGGER_CONTRACT = """\
Before doing any work, decide which of the available skills you would load for this request.
Respond with exactly one JSON object and nothing else:
{"load_skills": ["<skill name>", ...], "reason": "one sentence"}
Use [] when no skill applies."""


def skill_catalog(skills_root: Path) -> list[tuple[str, str]]:
    """(name, description) for every SKILL.md under skills_root, from its frontmatter."""
    catalog = []
    for path in sorted(skills_root.glob("*/SKILL.md")):
        text = path.read_text(encoding="utf-8")
        match = re.match(r"^---\r?\n(.*?)\r?\n---", text, re.S)
        if not match:
            continue
        fields = dict(re.findall(r"^(name|description):\s*(.+?)\s*$", match.group(1), re.M))
        if "name" in fields and "description" in fields:
            catalog.append((fields["name"], fields["description"]))
    return catalog


def build_trigger_prompt(case: dict[str, Any], catalog: list[tuple[str, str]], bootstrap: str | None) -> str:
    parts = ["You are a coding agent in T3 Code. You can run child tasks on other providers with delegate_task."]
    parts.append("Available skills:\n" + "\n".join(f"- {name}: {desc}" for name, desc in catalog))
    if bootstrap is not None:
        parts.append("<project-instructions source=\"AGENTS.md\">\n" + bootstrap.strip() + "\n</project-instructions>")
    if case.get("state"):
        parts.append("<state>\n" + case["state"].strip() + "\n</state>")
    parts.append("<user>\n" + case["user"].strip() + "\n</user>")
    parts.append(TRIGGER_CONTRACT)
    return "\n\n".join(parts)


def grade_trigger(case: dict[str, Any], skill: str, raw: str) -> dict[str, Any]:
    parsed, error = parse_response(raw)
    if parsed is None:
        return {"id": case["id"], "passed": False, "failures": [error], "response": None}
    loaded = parsed.get("load_skills")
    if not isinstance(loaded, list):
        return {"id": case["id"], "passed": False, "failures": ["load_skills missing or not a list"], "response": parsed}
    did_load = skill in loaded
    failures = [] if did_load == case["load"] else [f"expected load={case['load']}, got {loaded}"]
    return {"id": case["id"], "passed": not failures, "failures": failures, "response": parsed}


def load_triggers(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("cases"), list) or not data.get("skill"):
        raise ValueError(f"{path}: expected an object with 'skill' and a 'cases' list")
    return data


def cmd_triggers(args: argparse.Namespace) -> int:
    data = load_triggers(args.triggers)
    cases = data["cases"]
    if args.only:
        cases = [c for c in cases if c["id"] in args.only]
    catalog = skill_catalog(args.skills_root)
    if not args.no_distractors:
        # Competing skills make the choice realistic; sorted so the order is stable.
        catalog = sorted(catalog + [(d["name"], d["description"]) for d in data.get("distractors", [])])
    bootstrap = args.bootstrap.read_text(encoding="utf-8") if args.bootstrap else None
    cmd = shlex.split(args.cmd, posix=True)
    results = []
    for case in cases:
        prompt = build_trigger_prompt(case, catalog, bootstrap)
        for trial in range(1, args.trials + 1):
            raw, error = run_agent(cmd, prompt, args.timeout)
            if error:
                result = {"id": case["id"], "passed": False, "failures": [error], "response": None}
            else:
                result = grade_trigger(case, data["skill"], raw)
            result["trial"] = trial if args.trials > 1 else None
            result["raw"] = raw
            results.append(result)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps({"mode": "bootstrap" if bootstrap else "catalog", "cmd": args.cmd, "results": results}, indent=2),
            encoding="utf-8",
        )
    print_summary(results)
    return 0 if all(r["passed"] for r in results) else 1


def cmd_grade(args: argparse.Namespace) -> int:
    data = load_scenarios(args.scenarios)
    by_id = {s["id"]: s for s in data["scenarios"]}
    saved = json.loads(args.responses.read_text(encoding="utf-8"))
    entries = saved["results"] if isinstance(saved, dict) else saved
    results = []
    for entry in entries:
        scenario = by_id.get(entry["id"])
        if scenario is None:
            results.append({"id": entry["id"], "passed": False, "failures": ["unknown scenario"]})
            continue
        raw = entry.get("raw")
        if raw is None:
            raw = json.dumps(entry.get("response"))
        results.append(grade_raw(scenario, raw))
    print_summary(results)
    return 0 if all(r["passed"] for r in results) else 1


def cmd_selfcheck(args: argparse.Namespace) -> int:
    """Each scenario's pass example must pass and its fail example must fail."""
    data = load_scenarios(args.scenarios)
    problems = []
    for scenario in data["scenarios"]:
        examples = scenario.get("examples", {})
        if grade(scenario, examples.get("pass", {})):
            problems.append(f"{scenario['id']}: pass example fails: {grade(scenario, examples.get('pass', {}))}")
        if not grade(scenario, examples.get("fail", {})):
            problems.append(f"{scenario['id']}: fail example passes")
    for problem in problems:
        print(problem, file=sys.stderr)
    print(f"{len(data['scenarios'])} scenarios, {len(problems)} problems", file=sys.stderr)
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="send each scenario to an agent command and grade the reply")
    run.add_argument("--cmd", required=True, help="agent command; the prompt goes to stdin, or to {prompt_file} if present")
    run.add_argument("--skill", type=Path, default=DEFAULT_SKILL)
    run.add_argument("--baseline", action="store_true", help="omit the skill text")
    run.add_argument("--only", nargs="+", help="scenario ids to run")
    run.add_argument("--trials", type=int, default=1)
    run.add_argument("--timeout", type=float, default=300)
    run.add_argument("--out", type=Path, help="write raw replies and grades as JSON")
    run.add_argument("--verbose", action="store_true", help="print each result as it finishes")
    run.set_defaults(func=cmd_run)

    grade_p = sub.add_parser("grade", help="grade saved replies from a previous run")
    grade_p.add_argument("--responses", type=Path, required=True)
    grade_p.set_defaults(func=cmd_grade)

    check = sub.add_parser("selfcheck", help="check the scenarios' pass/fail examples against the grader")
    check.set_defaults(func=cmd_selfcheck)

    trig = sub.add_parser("triggers", help="check whether an agent would load the skill for each request")
    trig.add_argument("--cmd", required=True, help="agent command; the prompt goes to stdin, or to {prompt_file} if present")
    trig.add_argument("--triggers", type=Path, default=DEFAULT_TRIGGERS)
    trig.add_argument("--skills-root", type=Path, default=DEFAULT_SKILL.parents[1])
    trig.add_argument("--bootstrap", type=Path, help="include this file as project instructions (e.g. assets/bootstrap.md)")
    trig.add_argument("--no-distractors", action="store_true", help="list only the real skills")
    trig.add_argument("--only", nargs="+", help="case ids to run")
    trig.add_argument("--trials", type=int, default=1)
    trig.add_argument("--timeout", type=float, default=300)
    trig.add_argument("--out", type=Path)
    trig.set_defaults(func=cmd_triggers)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
