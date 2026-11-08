<!-- subagent-orchestrator:bootstrap v1 -->
## Delegation check

Before starting a task, load the `subagent-orchestrator` skill if any of these is true:

- The user asks you to delegate, hand work to other agents, run agents in parallel, or not write the code yourself.
- The user names a provider, model, or reasoning setting for the work.
- The task splits into two or more independent pieces with separate outputs, such as separate directories, a test matrix, or parallel research questions.
- The work needs a separate checkout or PR, a long background run, or a review from a different model.
- A delegated task has reported back and its result needs checking before you accept it.
- The user asks which model or target should handle a task.

Otherwise do the work yourself. Do not load the skill for one small edit, one question, or a tightly coupled change to a few files.
<!-- /subagent-orchestrator:bootstrap -->
