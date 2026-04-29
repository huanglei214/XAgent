# XAgent v2

XAgent v2 is a greenfield local AI agent. It can work in a default managed
workspace, edit files, run tools, execute commands, and keep session-local traces
under a user-level `~/.xagent` directory.

This branch intentionally does not reuse the `main-v1` implementation. The old
codebase remains preserved on the `main-v1` branch.

## User Data Layout

```text
~/.xagent/
  config.yaml
  workspace/
    files/
    sessions/
      <source-external-id>/
        messages.jsonl
        trace.jsonl
        artifacts/
```

`messages.jsonl` stores OpenAI-compatible model-visible conversation records.
`trace.jsonl` stores raw provider stream events, tool inputs/outputs, errors,
usage, and timings for debugging.

## CLI

```bash
agent
agent -m "explain this workspace"
agent -r terminal-20260429-abcdef
agent -w /path/to/project
agent gateway
```

`agent` starts a new terminal chat session. `agent -m/--message` runs one message directly
against the Agent while still writing a session package and trace. `agent
gateway` is reserved for future external chat channels. `-r/--resume` resumes a
session by directory name, and `-w/--workspace` chooses the workspace path.

## Provider Config

XAgent currently supports one provider backend: `openai_compat`.

```yaml
agents:
  defaults:
    model: "gpt-4o-mini"
    provider: "openai_compat"
    temperature: null
    max_tokens: null

providers:
  openai_compat:
    api_key: null
    api_key_env: "OPENAI_API_KEY"
    api_base: null
    extra_headers: {}
    extra_body: {}
    timeout_seconds: 120
```

`providers.openai_compat.api_key` can be set directly, or XAgent can read the
environment variable named by `api_key_env`. If neither is present, XAgent passes
`no-key` to the OpenAI-compatible SDK client so local endpoints can run without
authentication.
