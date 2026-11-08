# Skill-text evals

The unit tests in `tests/` prove the helper scripts work. These evals check something else: whether an agent that reads `SKILL.md` makes the right call when a situation pushes it toward a shortcut.

Two suites live in [`subagent-orchestrator/`](subagent-orchestrator/):

- **`scenarios.json`**: pressure scenarios. Each one gives the agent a user request and the current state, then asks for its next move as one JSON object: a decision, a target, the tool calls it would make, and the message to the user. A deterministic grader checks those fields. No model judges another model.
- **`triggers.json`**: would the agent load the skill at all? The agent sees a list of skill descriptions (the real ones plus distractors) and, optionally, the AGENTS.md bootstrap snippet.

## Run

Any CLI that reads a prompt on stdin and prints a reply works. Use `{prompt_file}` in the command if it needs a file instead.

```bash
# With the skill, then without it (the baseline the skill has to beat)
python3 evals/run_skill_evals.py run --cmd "claude -p --model haiku --tools '' --strict-mcp-config" --trials 3 --out out/skill.json
python3 evals/run_skill_evals.py run --cmd "claude -p --model haiku --tools '' --strict-mcp-config" --trials 3 --baseline --out out/base.json

# One scenario against another agent
python3 evals/run_skill_evals.py run --cmd "codex exec --skip-git-repo-check -" --only pinned-over-budget

# Trigger suite, with and without the bootstrap snippet
python3 evals/run_skill_evals.py triggers --cmd "claude -p --model haiku --tools ''" --trials 3
python3 evals/run_skill_evals.py triggers --cmd "claude -p --model haiku --tools ''" --trials 3 \
  --bootstrap .opencode/skills/subagent-orchestrator/assets/bootstrap.md

# Re-grade saved replies after changing a scenario's expectations
python3 evals/run_skill_evals.py grade --responses out/skill.json
```

The agent runs in a temporary directory so it cannot read this repository. Live runs cost tokens and are not part of CI.

CI runs `tests/test_skill_evals.py` instead, which calls no model. It checks that:

- every scenario's own `pass` example passes and its `fail` example fails;
- every `rule_anchors` string still appears in `SKILL.md`, so an edit that deletes a rule a scenario depends on fails the build;
- every skill description starts with "Use when" and does not summarize the workflow.

## Writing a scenario

Follow the RED → GREEN order:

1. Write the scenario around one shortcut an agent might take. Put the temptation in `pressure`.
2. Run it with `--baseline` and watch it fail. If the baseline already passes, the scenario is not testing the skill.
3. Run it with the skill. If it still fails, read the raw replies (`--out`), find the excuse the agent used, and answer it in the skill, usually as a row in an "Excuses that break Phase N" table.
4. Add the rule text you relied on to `rule_anchors`, plus one `pass` and one `fail` example reply.

Grader fields in `expect`: `decision` (allowed list), `target` (exact, `re:` regex, or `null`), `require_calls` / `forbid_calls` (`{"tool", "match": {arg: value or "re:..."}}`), `max_calls`, `must_mention` / `must_not_mention` (regexes over `user_message`), and `user_message_fullmatch`.

## Results, 2026-11-08

Final scenario set. Columns are passed / total across trials.

| Model | Without skill | With skill |
|---|---|---|
| Sonnet (2 trials × 15) | 22/30 | 30/30 |
| Haiku (3 trials × 15) | 36/45 | 38/45 |

Without the skill, both models fail in the same three places:

- they do small work in the parent when the user required delegation;
- they wait out or relaunch a rate-limited pinned target instead of telling the user;
- they wrap route-only answers in prose.

Sonnet also accepts a fix without a fresh review.

With the skill, Sonnet passes all of these and Haiku fails only one of its three rate-limit trials. Haiku's other failures with the skill: it sometimes launches a pinned target over budget, retries a cooling-down coder when no other is eligible, or moves a fix round to a stronger model. The "Excuses" tables were written from those replies. They helped, but did not remove the failures.

Trigger suite, Haiku, 3 trials × 13 cases, with distractor skills:

| Descriptions | Bootstrap | Passed |
|---|---|---|
| Original (workflow summary) | no | 39/39 |
| Rewritten, first draft | no | 38/39 |
| Rewritten, first draft | yes | 39/39 |

The first draft of the rewritten description dropped the word "parallel" and missed a "run these at the same time" request once. The shipped description restores it; that case and the three weakest others then passed 16/16. In short, the descriptions already triggered well in isolation. The bootstrap adds margin, and it matters most in long sessions with many skills, which this suite does not simulate.
