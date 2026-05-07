# Workspace Memory Dream Prompt

<dream_goal>
Update the workspace `memory.md` from compact session summaries.
</dream_goal>

<must_keep>
- Long-lived workspace facts, project purpose, architecture decisions, conventions, completed work, pending work, and known risks.
- Existing memory that is still true.
- Human-maintained project instructions from `AGENTS.md` when they conflict with session summaries.
</must_keep>

<must_drop>
- Temporary conversation details, one-off web queries, failed model guesses, noisy debug logs, secrets, tokens, credentials, and stale time-sensitive facts.
- Raw trace details, large code blocks, and tool output that is not a durable project fact.
</must_drop>

<output_style>
Return the complete new Markdown content for `memory.md`.
Use this structure exactly:

# Workspace Memory

## 项目定位

## 架构决策

## 当前约定

## 已完成事项

## 待处理事项

## 注意事项
</output_style>
