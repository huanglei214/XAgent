# Conversation Compaction Prompt

<summary_goal>
Summarize the current task state for future turns.
</summary_goal>

<must_include>
- The user's active goal and any explicit preferences.
- Key decisions and constraints.
- Important files, commands, tool results, and errors.
- File changes or artifacts that already exist.
- Remaining todo items and known risks.
</must_include>

<summary_style>
Keep the summary compact, factual, and useful as model-visible context.
</summary_style>
