# XAgent Working Agreement

This repository builds `XAgent`, a Python coding assistant CLI.

## Architecture Target

The project should converge toward this layered structure:

```text
src/xagent/
  foundation/
  agent/
  coding/
  community/
  cli/
```

Interpretation of each layer:

- `foundation`: base protocols and shared primitives only.
- `agent`: generic agent runtime capabilities such as loop, middleware, todos, session, and traces.
- `coding`: coding-domain behavior, workspace rules, approval policy, and coding tools.
- `community`: external model/provider adapters such as OpenAI, Anthropic, and Ark.
- `cli`: product entrypoints, command handlers, runtime wiring, UI helpers, and local config helpers.

Do not introduce new top-level technical buckets such as `providers/`, `memory/`, `traces/`, or `config/` under `src/xagent/` long-term. Prefer placing code into one of the five target layers above.

## Structural Rules

- Prefer one more directory level inside each major layer when it improves grouping clarity.
- Avoid a flat directory full of unrelated `*.py` files once a layer starts holding multiple concepts.
- Keep imports directional when possible:
  - `foundation` should not depend on `agent`, `coding`, `community`, or `cli`.
  - `agent` may depend on `foundation`, but not on `cli`.
  - `coding` may depend on `agent` and `foundation`.
  - `community` may depend on `foundation`.
  - `cli` may depend on every lower layer.

## Middleware Rules

- Cross-cutting behavior should prefer middleware over direct runtime branching.
- Current lifecycle hooks:
  - `before_agent_run`
  - `after_agent_run`
  - `before_agent_step`
  - `after_agent_step`
  - `before_model`
  - `after_model`
  - `before_tool`
  - `after_tool`
- Do not reintroduce a generic `on_error` middleware hook. Keep middleware focused on normal lifecycle interception.

## Error Handling Rules

- Recoverable tool failures should usually become `tool result` errors and remain in the agent message flow.
- System-level failures such as provider failures, loop failures, or invalid runtime state may raise exceptions.
- Trace failure capture should happen at the runtime boundary, not through a dedicated middleware error hook.

## Coding Agent Rules

- Read relevant files before editing them.
- Prefer `str_replace` or `apply_patch` for targeted edits.
- Use `write_file` only for full rewrites or brand-new files.
- Mutating tools must stay behind approval handling.
- Keep workspace operations rooted inside the project directory.

## Session and Trace Rules

- Project-local state belongs under `.xagent/`.
- Session persistence uses `.xagent/session.json`.
- Approval persistence uses `.xagent/approvals.json`.
- Trace events use `.xagent/traces/*.ndjson` plus `.xagent/traces/index.json`.
- Failed runs must preserve replayable evidence, not just a final error string.

## Config Rules

- Project-local config lives in `.xagent/config.yaml`.
- Project-local environment variables live in `.env`.
- Repository-level template config lives in `config.example.yaml`.
- CLI configuration helpers should live under the `cli` layer, not as a top-level architectural layer.

## Refactor Rules

- Prefer small, staged moves when restructuring directories.
- After each structural move:
  - fix imports
  - run tests
  - confirm CLI commands still work
- Avoid mixing broad directory reshuffles with unrelated feature work in the same step when possible.

## Verification

Before considering work complete, run:

```bash
PYTHONPATH=src PYTHONPYCACHEPREFIX=.pycache python3 -m unittest discover -s tests -v
```

If verification cannot run, say so explicitly.
