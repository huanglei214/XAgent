import asyncio
import json
import os
import shutil
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Dict, List, Optional

from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text
from prompt_toolkit.application import Application
from prompt_toolkit import PromptSession
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.application.current import get_app_or_none
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, VSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.processors import BeforeInput
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style

from xagent.agent.session import SessionStore, SessionSummary
from xagent.agent.tool_result_runtime import summarize_tool_result_for_ui
from xagent.agent.core import AgentAborted
from xagent.cli.runtime.runtime import (
    build_runtime_agent,
    format_runtime_error,
    get_runtime_status,
    make_external_path_approval_handler,
    run_agent_turn_stream,
)
from xagent.cli.tui.commands import BUILTIN_COMMANDS
from xagent.coding.middleware import ApprovalMiddleware
from xagent.coding.tools.ask_user_question import AskUserQuestionAnswer, AskUserQuestionInput, AskUserQuestionResultData
from xagent.foundation.messages import Message, ToolResultPart, ToolUsePart, message_text


console = Console(highlight=False)

DOG_ICON = (
    "    ╭──╮  ╭──╮\n"
    "    │▓▓╰──╯▓▓│\n"
    "    │ ◕    ◕ │\n"
    "    ╰─┬────┬─╯\n"
    "      │ ▽  │\n"
    "      ╰────╯"
)


def build_header_text(agent) -> str:
    """构建启动时的 header 显示文本。"""
    cwd = Path(getattr(agent, "cwd", ".")).resolve().as_posix()
    model = getattr(agent, "model", "unknown")
    dog_lines = DOG_ICON.splitlines()
    info_lines = [
        "XAgent",
        f"{model}",
        f"{cwd}",
    ]
    combined: List[str] = []
    max_dog_width = max(len(line) for line in dog_lines) if dog_lines else 0
    for i in range(max(len(dog_lines), len(info_lines))):
        dog_part = dog_lines[i] if i < len(dog_lines) else ""
        info_part = info_lines[i] if i < len(info_lines) else ""
        combined.append(f"{dog_part:<{max_dog_width}}  {info_part}")
    return "\n".join(combined)


def build_status_text(agent, active_tools: Optional[List[str]] = None) -> str:
    """构建底部状态栏文本。"""
    model = getattr(agent, "model", "unknown")
    messages = len(getattr(agent, "messages", []))
    if active_tools:
        return f"{model}    {messages} messages    running {', '.join(active_tools)}"
    return f"{model}    {messages} messages"


def build_sidebar_text(agent, active_tools: Optional[List[str]] = None) -> str:
    """构建运行时状态信息（保留兼容性）。"""
    lines = get_runtime_status(agent).splitlines()

    todo_store = getattr(agent, "todo_store", None)
    if todo_store and getattr(todo_store, "items", None):
        lines.append("")
        lines.append("Todos:")
        for item in todo_store.items:
            lines.append(f"  - [{item.status}] {item.content}")

    approval_store = getattr(agent, "approval_store", None)
    if approval_store is not None:
        allowed = sorted(getattr(approval_store, "allowed_tools", set()))
        if allowed:
            lines.append("")
            lines.append("Approvals:")
            for tool in allowed:
                lines.append(f"  - {tool}")

    if active_tools:
        lines.append("")
        lines.append("In Progress:")
        for tool in active_tools:
            lines.append(f"  - {tool}")

    last_trace = getattr(agent, "last_trace_recorder", None)
    if last_trace is not None:
        lines.append("")
        lines.append(f"Last trace: {last_trace.trace_id} ({last_trace.status})")

    return "\n".join(lines)


def build_transcript_text(
    messages: List[Message],
    notices: List[str],
    live_assistant: Optional[Message] = None,
    active_tools: Optional[List[str]] = None,
) -> str:
    """构建对话记录文本。"""
    blocks: List[str] = []

    for notice in notices:
        blocks.append(notice)

    for message in messages:
        blocks.extend(_render_message_blocks(message))

    if live_assistant is not None and message_text(live_assistant).strip():
        blocks.append(f"● {message_text(live_assistant)}")

    if active_tools:
        for tool in active_tools:
            blocks.append(f"… running {tool}")

    return "\n\n".join(blocks) if blocks else "Start chatting."


def build_command_palette_text(
    query: str, commands: List[Dict[str, str]], selected_index: int
) -> str:
    """构建命令面板文本。"""
    if not query.startswith("/"):
        return ""
    if not commands:
        return "No commands found"

    lines = []
    for index, command in enumerate(commands[:6]):
        prefix = "❯" if index == selected_index else " "
        lines.append(f"{prefix} /{command['name']} — {command['description']}")
    return "\n".join(lines)


def build_todo_text(agent) -> str:
    """构建展示在输入框上方的 Todo 文本。"""
    todo_store = getattr(agent, "todo_store", None)
    items = getattr(todo_store, "items", None) or []
    if not items:
        return ""

    status_markers = {
        "pending": "[ ]",
        "in_progress": "[>]",
        "completed": "[x]",
        "cancelled": "[-]",
    }
    lines = ["Todos"]
    for item in items:
        marker = status_markers.get(getattr(item, "status", ""), "[ ]")
        lines.append(f"  {marker} {item.content}")
    return "\n".join(lines)


def _todo_line_style(line: str) -> str:
    """返回 Todo 行对应的渲染样式。"""
    if "[>]" in line:
        return "class:todo_active"
    if "[x]" in line or "[-]" in line:
        return "class:todo_done"
    return "class:todo_item"


def _render_message_blocks(message: Message) -> List[str]:
    """渲染单条消息为文本块列表。"""
    blocks: List[str] = []
    if message.role == "user":
        blocks.append(f"❯ {message_text(message)}")
        return blocks

    if message.role == "assistant":
        text = message_text(message)
        if text.strip():
            blocks.append(f"● {text}")
        for part in message.content:
            if isinstance(part, ToolUsePart):
                blocks.append(f"○ {part.name} {summarize_payload(part.input)}")
        return blocks

    if message.role == "tool":
        for part in message.content:
            if isinstance(part, ToolResultPart):
                prefix = "✖" if part.is_error else "✓"
                blocks.append(f"{prefix} {summarize_tool_result_for_ui('', part.content, part.is_error)}")
        return blocks

    return blocks


def summarize_payload(payload) -> str:
    """将工具调用参数截断摘要。"""
    try:
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    except Exception:
        text = str(payload)
    if len(text) > 100:
        return text[:97] + "..."
    return text


def _format_token_count(count: int) -> str:
    """格式化 token 数量显示。"""
    if count >= 1000:
        return f"{count / 1000:.1f}k"
    return str(count)


def build_resume_hint_text(session_id: str) -> str:
    return f"To continue this session, run xagent resume {session_id}"


def _format_session_age(saved_at: float) -> str:
    if saved_at <= 0:
        return "unknown time"
    delta = datetime.now(timezone.utc).timestamp() - saved_at
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    if delta < 86400 * 7:
        return f"{int(delta // 86400)}d ago"
    return datetime.fromtimestamp(saved_at, tz=timezone.utc).astimezone().strftime("%Y-%m-%d")


def _format_session_option(index: int, summary: SessionSummary, current_session_id: str) -> str:
    markers = []
    if summary.is_latest:
        markers.append("latest")
    if summary.session_id == current_session_id:
        markers.append("current")
    marker_text = f" [{' | '.join(markers)}]" if markers else ""
    short_id = summary.session_id[:8]
    return (
        f"{index}. {summary.preview} "
        f"({summary.message_count} msgs, {_format_session_age(summary.saved_at)}, {short_id}){marker_text}"
    )


def _build_session_picker_values(
    sessions: List[SessionSummary],
    current_session_id: str,
) -> List[tuple[str, str]]:
    return [
        (summary.session_id, _format_session_option(index, summary, current_session_id))
        for index, summary in enumerate(sessions, start=1)
    ]


def _default_session_picker_selection(
    sessions: List[SessionSummary],
    current_session_id: str,
) -> Optional[str]:
    for summary in sessions:
        if summary.session_id != current_session_id:
            return summary.session_id
    return sessions[0].session_id if sessions else None


def _filter_session_summaries(
    sessions: List[SessionSummary],
    query: str,
) -> List[SessionSummary]:
    normalized = query.strip().lower()
    if not normalized:
        return list(sessions)

    terms = [term for term in normalized.split() if term]
    filtered: List[SessionSummary] = []
    for summary in sessions:
        haystack = " ".join(
            [
                summary.session_id.lower(),
                summary.branch.lower(),
                summary.preview.lower(),
                _format_session_age(summary.created_at).lower(),
                _format_session_age(summary.saved_at).lower(),
            ]
        )
        if all(term in haystack for term in terms):
            filtered.append(summary)
    return filtered


def _fit_session_picker_cell(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text.ljust(width)
    if width <= 3:
        return text[:width]
    return (text[: width - 3] + "...").ljust(width)


def _session_picker_column_widths(total_width: int) -> dict[str, int]:
    usable = max(72, total_width)
    created = 18
    updated = 18
    branch = min(14, max(10, usable // 8))
    conversation = max(20, usable - 2 - created - updated - branch - 6)
    return {
        "marker": 2,
        "created": created,
        "updated": updated,
        "branch": branch,
        "conversation": conversation,
    }


def _build_session_picker_row_text(
    summary: SessionSummary,
    total_width: int,
) -> tuple[str, str, str, str]:
    widths = _session_picker_column_widths(total_width)
    return (
        _fit_session_picker_cell(_format_session_age(summary.created_at), widths["created"]),
        _fit_session_picker_cell(_format_session_age(summary.saved_at), widths["updated"]),
        _fit_session_picker_cell(summary.branch, widths["branch"]),
        _fit_session_picker_cell(summary.preview or "(empty session)", widths["conversation"]),
    )


def _print_header(agent) -> None:
    """打印欢迎 header（西施犬图标 + 项目信息）到终端。"""
    dog_lines = DOG_ICON.splitlines()
    model = getattr(agent, "model", "unknown")
    cwd_path = Path(getattr(agent, "cwd", ".")).resolve().as_posix()
    info_lines = ["XAgent", model, cwd_path]
    max_dog_width = max(len(line) for line in dog_lines) if dog_lines else 0

    for i in range(max(len(dog_lines), len(info_lines))):
        dog_part = dog_lines[i] if i < len(dog_lines) else ""
        info_part = info_lines[i] if i < len(info_lines) else ""
        line = Text()
        line.append(f"{dog_part:<{max_dog_width}}", style="bold yellow")
        line.append("  ")
        if i == 0:
            line.append(info_part, style="bold green")
        else:
            line.append(info_part, style="dim")
        console.print(line)

    console.print()


def _print_user_message(text: str) -> None:
    """打印用户消息。"""
    line = Text()
    line.append("❯ ", style="bold")
    line.append(text)
    console.print(line)
    console.print()


def _print_assistant_text(text: str) -> None:
    """打印助手最终文本。"""
    console.print(Text("● ", style="bold blue"), end="")
    console.print(Markdown(text))
    console.print()


def _print_tool_use(tool_use: ToolUsePart) -> None:
    """打印工具调用。"""
    line = Text()
    line.append(f"○ {tool_use.name} ", style="dim")
    line.append(summarize_payload(tool_use.input), style="dim")
    console.print(line)


def _print_tool_result(result: ToolResultPart) -> None:
    """打印工具结果。"""
    marker = "✖" if result.is_error else "✓"
    style = "red" if result.is_error else "dim"
    summary = summarize_tool_result_for_ui("", result.content, result.is_error)
    truncated = summary if len(summary) <= 200 else summary[:197] + "..."
    line = Text()
    line.append(f"  └─ {marker} ", style=style)
    line.append(truncated, style=style)
    console.print(line)


def _format_runtime_block(title: str, lines: list[str]) -> str:
    normalized = [line.strip() for line in lines if line and line.strip()]
    if not normalized:
        normalized = ["-"]
    body = "\n".join(f"  {line}" for line in normalized)
    return f"{title}\n{body}"


def _print_runtime_block(title: str, lines: list[str]) -> None:
    block = _format_runtime_block(title, lines)
    rendered = Text()
    first = True
    for line in block.splitlines():
        if first:
            rendered.append(line, style="bold cyan")
            first = False
        else:
            rendered.append("\n")
            rendered.append(line, style="dim")
    console.print(rendered)
    console.print()


async def _ask_user_questions_via_prompt(
    prompt_fn,
    params: AskUserQuestionInput,
) -> AskUserQuestionResultData:
    answers = []
    for index, question in enumerate(params.questions):
        lines = [question.question]
        for option_index, option in enumerate(question.options, start=1):
            lines.append(f"{option_index}. {option.label} — {option.description}")
        lines.append(
            "Reply with one number."
            if not question.multi_select
            else "Reply with one or more numbers separated by commas."
        )

        while True:
            raw = await prompt_fn(_format_runtime_block(question.header, lines))
            try:
                selected = _parse_question_selection(raw, question.multi_select, len(question.options))
            except ValueError:
                continue
            answers.append(
                AskUserQuestionAnswer(
                    question_index=index,
                    selected_labels=[question.options[item - 1].label for item in selected],
                )
            )
            break
    return AskUserQuestionResultData(answers=answers)


def _parse_question_selection(raw: str, multi_select: bool, option_count: int) -> list[int]:
    tokens = [part.strip() for part in raw.split(",") if part.strip()]
    if not tokens:
        raise ValueError("empty selection")
    values = []
    for token in tokens:
        if not token.isdigit():
            raise ValueError("non numeric selection")
        value = int(token)
        if value < 1 or value > option_count:
            raise ValueError("out of range")
        values.append(value)
    if not multi_select and len(values) != 1:
        raise ValueError("single select requires exactly one answer")
    return list(dict.fromkeys(values))


def _print_status_bar(agent, token_count: int) -> None:
    """打印底部状态栏。"""
    model = getattr(agent, "model", "unknown")
    token_display = _format_token_count(token_count) + " tokens"
    try:
        width = os.get_terminal_size().columns
    except OSError:
        width = 80
    left = f"{model}"
    right = f"{token_display}"
    padding = max(0, width - len(left) - len(right))
    line = Text()
    line.append(left, style="dim")
    line.append(" " * padding)
    line.append(right, style="dim")
    console.print(line)


def _get_terminal_width() -> int:
    """获取当前终端宽度（用于绘制输入 panel）。"""
    return max(40, shutil.get_terminal_size(fallback=(80, 24)).columns)


def _build_input_panel_prompt(width: int, streaming: bool = False) -> FormattedText:
    """构建输入行的 prompt（带顶部边框）。"""
    top = "─" * width
    if streaming:
        return FormattedText([("class:border", f"{top}\n"), ("class:prompt", "❯ Input anything to continue. Launch a new command or skill by typing `/`")])
    return FormattedText([("class:border", f"{top}\n"), ("class:prompt", "❯ ")])


def _build_input_panel_rprompt() -> FormattedText:
    """右侧不需要内容。"""
    return FormattedText([])


def _build_input_panel_bottom_toolbar(
    width: int, model: str, cwd: str, token_count: int
) -> FormattedText:
    """构建 bottom toolbar（分隔线 + 两端对齐状态栏）。"""
    token_display = f"{_format_token_count(token_count)} tokens"
    
    parts = []
    
    bottom_line = "─" * width
    parts.append(("class:border", f"{bottom_line}\n"))
    
    left_text = f"{model}"
    right_text = f"{token_display}"
    
    padding_len = width - len(left_text) - len(right_text)
    if padding_len < 0:
        padding_len = 0
        
    status_line = left_text + (" " * padding_len) + right_text
    parts.append(("class:status", status_line))
    
    return FormattedText(parts)


def _read_input() -> str:
    """显示带边框的输入框并读取用户输入。"""
    try:
        text = console.input("[bold]❯[/bold] ")
    except EOFError:
        return "/exit"
    return text.strip()


async def run_tui(cwd: str, *, resume: bool = False, resume_session_id: Optional[str] = None) -> None:
    """TUI 主入口 — 透明背景 + 固定底部输入 panel（非全屏）。"""
    ui_state = {
        "streaming": False,
        "streaming_hint": "",
        "thinking_active": False,
        "thinking_frame": 0,
        "thinking_label": "Thinking",
        "streaming_text": "",
        "runtime_event_keys": set(),
    }
    token_count = 0

    style = Style.from_dict(
        {
            "": "",
            "border": "fg:#6c6c6c",
            "prompt": "fg:#ffffff bold",
            "status": "fg:#a0a0a0",
            "todo_header": "fg:#67d4ff bold",
            "todo_item": "fg:#b8c0cc",
            "todo_active": "fg:#ffffff bold",
            "todo_done": "fg:#6f7782",
            "bottom-toolbar": "noreverse bg:default fg:default",
            "bottom-toolbar.text": "noreverse bg:default fg:default",
            "spinner": "fg:#00afff bold",
            "glow0": "fg:#ffffff bold",
            "glow1": "fg:#5fd7ff bold",
            "glow2": "fg:#005f87",
            "assistant_dot": "fg:#005f87 bold",
            "streaming_text": "fg:default",
            "picker.title": "fg:#00d7d7 bold",
            "picker.sort_label": "fg:#6f7782",
            "picker.sort_value": "fg:#d700ff bold",
            "picker.search_prompt": "fg:#6f7782",
            "picker.search_input": "fg:#d8dee9",
            "picker.search_empty": "fg:#6f7782",
            "picker.header": "fg:#e5e9f0 bold",
            "picker.text": "fg:#b8c0cc",
            "picker.branch": "fg:#00d7d7",
            "picker.selected": "fg:#ffffff bold",
            "picker.selected_branch": "fg:#5fd7ff bold",
            "picker.marker": "fg:#4c566a",
            "picker.selected_marker": "fg:#ffffff bold",
            "picker.footer": "fg:#6f7782",
            "picker.empty": "fg:#6f7782 italic",
        }
    )

    kb = KeyBindings()

    @kb.add("enter")
    def _on_enter(event) -> None:
        if ui_state["streaming"]:
            return
        event.current_buffer.validate_and_handle()

    @kb.add("c-c")
    def _on_ctrl_c(event) -> None:
        if ui_state["streaming"]:
            agent.abort()
            _print_runtime_block("Cancelling run", ["User requested abort via Ctrl+C"])
            return
        event.current_buffer.text = "/exit"
        event.current_buffer.cursor_position = len(event.current_buffer.text)
        event.current_buffer.validate_and_handle()

    session = None
    prompt_task: Optional[asyncio.Task[str]] = None

    def _invalidate_prompt() -> None:
        app = get_app_or_none()
        if app is not None:
            app.invalidate()

    def _get_prompt_message():
        width = _get_terminal_width()
        top = "─" * width
        parts = []

        todo_text = build_todo_text(agent)
        if todo_text:
            todo_lines = todo_text.splitlines()
            parts.append(("class:todo_header", todo_lines[0]))
            parts.append(("", "\n"))
            for line in todo_lines[1:]:
                parts.append((_todo_line_style(line), line))
                parts.append(("", "\n"))

        if ui_state["thinking_active"]:
            frame = ui_state["thinking_frame"]
            label = ui_state["thinking_label"]

            spinner_frames = ["·", "✢", "✳", "✶", "✻", "✽"]
            spinner = spinner_frames[(frame // 2) % len(spinner_frames)]
            text_len = len(label)
            glow_pos = (frame // 2) % (text_len + 5)

            parts.append(("class:spinner", f"{spinner} "))
            for i, char in enumerate(label):
                dist = abs(i - glow_pos)
                if dist == 0:
                    parts.append(("class:glow0", char))
                elif dist == 1:
                    parts.append(("class:glow1", char))
                else:
                    parts.append(("class:glow2", char))
            parts.append(("", "\n"))

        if ui_state["streaming_text"]:
            parts.append(("class:assistant_dot", "● "))
            parts.append(("class:streaming_text", ui_state["streaming_text"]))
            parts.append(("", "\n"))

        parts.append(("class:border", f"{top}\n"))
        if ui_state["streaming"]:
            parts.append(("class:prompt", "❯ Input anything to continue. Launch a new command or skill by typing `/`"))
        else:
            parts.append(("class:prompt", "❯ "))

        return FormattedText(parts)

    async def _prompt_once() -> str:
        width = _get_terminal_width()
        bottom_toolbar = partial(
            _build_input_panel_bottom_toolbar,
            width=width,
            model=model,
            cwd=cwd_path,
            token_count=token_count,
        )
        return (
            await session.prompt_async(
                message=_get_prompt_message,
                rprompt=_build_input_panel_rprompt,
                bottom_toolbar=bottom_toolbar,
            )
        ).strip()

    async def _cancel_live_prompt() -> None:
        nonlocal prompt_task
        if prompt_task is None or prompt_task.done():
            return
        prompt_task.cancel()
        try:
            await prompt_task
        except asyncio.CancelledError:
            pass
        prompt_task = None

    async def _prompt_modal(label: str) -> str:
        nonlocal prompt_task
        was_streaming = ui_state["streaming"]
        await _cancel_live_prompt()

        if ui_state["streaming_text"]:
            _print_assistant_text(ui_state["streaming_text"])
            ui_state["streaming_text"] = ""
        ui_state["thinking_active"] = False
        ui_state["streaming"] = False
        _invalidate_prompt()

        try:
            return (
                await session.prompt_async(
                    message=lambda: FormattedText(
                        [
                            ("class:border", f"{'─' * _get_terminal_width()}\n"),
                            ("class:prompt", f"? {label} "),
                        ]
                    ),
                )
            ).strip()
        finally:
            if was_streaming:
                ui_state["streaming"] = True
                prompt_task = asyncio.create_task(_prompt_once())
            _invalidate_prompt()

    async def _prompt_session_picker(entries: List[SessionSummary]) -> Optional[str]:
        nonlocal prompt_task
        was_streaming = ui_state["streaming"]
        await _cancel_live_prompt()

        if ui_state["streaming_text"]:
            _print_assistant_text(ui_state["streaming_text"])
            ui_state["streaming_text"] = ""
        ui_state["thinking_active"] = False
        ui_state["streaming"] = False
        _invalidate_prompt()

        search_buffer = Buffer()
        filtered_entries = list(entries)
        default_session_id = _default_session_picker_selection(entries, agent.trace_session_id)
        initial_index = next(
            (index for index, item in enumerate(filtered_entries) if item.session_id == default_session_id),
            0,
        )
        picker_state = {"selected_index": initial_index}

        def _selected_summary() -> Optional[SessionSummary]:
            if not filtered_entries:
                return None
            index = max(0, min(picker_state["selected_index"], len(filtered_entries) - 1))
            picker_state["selected_index"] = index
            return filtered_entries[index]

        def _refresh_filtered() -> None:
            nonlocal filtered_entries
            current_selected = _selected_summary()
            filtered_entries = _filter_session_summaries(entries, search_buffer.text)
            target_session_id = current_selected.session_id if current_selected is not None else default_session_id
            picker_state["selected_index"] = next(
                (index for index, item in enumerate(filtered_entries) if item.session_id == target_session_id),
                0,
            )
            app = get_app_or_none()
            if app is not None:
                app.invalidate()

        search_buffer.on_text_changed += lambda _: _refresh_filtered()

        picker_bindings = KeyBindings()

        @picker_bindings.add("down")
        @picker_bindings.add("c-n")
        def _picker_down(event) -> None:
            if filtered_entries:
                picker_state["selected_index"] = min(len(filtered_entries) - 1, picker_state["selected_index"] + 1)
                event.app.invalidate()

        @picker_bindings.add("up")
        @picker_bindings.add("c-p")
        def _picker_up(event) -> None:
            if filtered_entries:
                picker_state["selected_index"] = max(0, picker_state["selected_index"] - 1)
                event.app.invalidate()

        @picker_bindings.add("pageup")
        def _picker_page_up(event) -> None:
            if filtered_entries:
                picker_state["selected_index"] = max(0, picker_state["selected_index"] - 8)
                event.app.invalidate()

        @picker_bindings.add("pagedown")
        def _picker_page_down(event) -> None:
            if filtered_entries:
                picker_state["selected_index"] = min(len(filtered_entries) - 1, picker_state["selected_index"] + 8)
                event.app.invalidate()

        @picker_bindings.add("enter")
        def _picker_accept(event) -> None:
            selected = _selected_summary()
            event.app.exit(result=selected.session_id if selected is not None else None)

        @picker_bindings.add("escape")
        @picker_bindings.add("c-c")
        def _picker_cancel(event) -> None:
            event.app.exit(result=None)

        def _title_fragments():
            return [
                ("class:picker.title", "Resume a previous session"),
                ("", "  "),
                ("class:picker.sort_label", "Sort: "),
                ("class:picker.sort_value", "Updated"),
            ]

        def _search_label_fragments():
            if search_buffer.text:
                return [("class:picker.search_prompt", "Search")]
            return [("class:picker.search_empty", "Type to search")]

        def _header_fragments():
            widths = _session_picker_column_widths(max(72, _get_terminal_width() - 4))
            return [
                ("class:picker.header", "  "),
                ("class:picker.header", _fit_session_picker_cell("Created", widths["created"])),
                ("", "  "),
                ("class:picker.header", _fit_session_picker_cell("Updated", widths["updated"])),
                ("", "  "),
                ("class:picker.header", _fit_session_picker_cell("Branch", widths["branch"])),
                ("", "  "),
                ("class:picker.header", _fit_session_picker_cell("Conversation", widths["conversation"])),
            ]

        def _rows_fragments():
            if not filtered_entries:
                return [("class:picker.empty", "  No sessions match your search.")]

            total_width = max(72, _get_terminal_width() - 4)
            fragments = []
            for index, summary in enumerate(filtered_entries):
                created, updated, branch, conversation = _build_session_picker_row_text(summary, total_width)
                is_selected = index == picker_state["selected_index"]
                text_style = "class:picker.selected" if is_selected else "class:picker.text"
                branch_style = "class:picker.selected_branch" if is_selected else "class:picker.branch"
                marker_style = "class:picker.selected_marker" if is_selected else "class:picker.marker"
                marker = "> " if is_selected else "  "
                fragments.extend(
                    [
                        (marker_style, marker),
                        (text_style, created),
                        ("", "  "),
                        (text_style, updated),
                        ("", "  "),
                        (branch_style, branch),
                        ("", "  "),
                        (text_style, conversation),
                        ("", "\n"),
                    ]
                )
            if fragments:
                fragments.pop()
            return fragments

        def _footer_fragments():
            return [("class:picker.footer", "Enter resume  Esc cancel  Up/Down navigate")]

        search_window = Window(
            content=BufferControl(
                buffer=search_buffer,
                input_processors=[BeforeInput("  ")],
            ),
            height=1,
            style="class:picker.search_input",
            always_hide_cursor=False,
        )
        root = VSplit(
            [
                Window(width=1, char=" "),
                HSplit(
                    [
                        Window(height=1, content=FormattedTextControl(_title_fragments)),
                        Window(height=1, content=FormattedTextControl(_search_label_fragments)),
                        search_window,
                        Window(height=1, char=" "),
                        Window(height=1, content=FormattedTextControl(_header_fragments)),
                        Window(content=FormattedTextControl(_rows_fragments), always_hide_cursor=True),
                        Window(height=1, char=" "),
                        Window(height=1, content=FormattedTextControl(_footer_fragments)),
                    ]
                ),
                Window(width=1, char=" "),
            ]
        )

        try:
            app = Application(
                layout=Layout(root, focused_element=search_window),
                key_bindings=picker_bindings,
                full_screen=True,
                style=style,
            )
            return await app.run_async()
        finally:
            if was_streaming:
                ui_state["streaming"] = True
                prompt_task = asyncio.create_task(_prompt_once())
            _invalidate_prompt()

    async def _prompt_path_access(prompt: str) -> bool:
        decision = await _prompt_modal(prompt)
        return decision.strip().lower() in {"y", "yes"}

    async def _ask_user_questions(params: AskUserQuestionInput) -> AskUserQuestionResultData:
        return await _ask_user_questions_via_prompt(_prompt_modal, params)

    agent = build_runtime_agent(cwd, ask_user_question=_ask_user_questions)
    agent.runtime_mode = "chat"

    session_store = SessionStore(cwd)
    agent.trace_session_id = session_store.new_session_id()

    def _print_session_restored_notice(
        session_id: str,
        restore_metadata,
        *,
        source: str = "previous session",
    ) -> None:
        if restore_metadata.restored_message_count == 0:
            console.print(f"[italic dim]Resumed empty {source} {session_id}.[/italic dim]")
        elif restore_metadata.has_checkpoint:
            console.print(
                "[italic dim]"
                f"Restored checkpoint ({restore_metadata.checkpointed_message_count} compacted messages) "
                f"+ {restore_metadata.recent_message_count} recent messages from {source} {session_id}."
                "[/italic dim]"
            )
        else:
            console.print(
                f"[italic dim]Restored {restore_metadata.restored_message_count} messages from {source} {session_id}.[/italic dim]"
            )
        console.print()

    def _load_session_into_agent(session_id: str) -> bool:
        nonlocal token_count
        loaded_session_id, restored_messages, restore_metadata = session_store.load_state_with_metadata(session_id=session_id)
        if not restored_messages and not session_store.session_exists(session_id):
            return False
        agent.clear_messages()
        agent.set_messages(restored_messages)
        agent.trace_session_id = loaded_session_id
        token_count = sum(len(message_text(m).split()) for m in agent.messages)
        console.clear()
        _print_header(agent)
        _print_session_restored_notice(loaded_session_id, restore_metadata, source="session")
        return True

    async def _prompt_resume_session_choice() -> Optional[str]:
        entries = session_store.list_sessions(limit=12)
        if not entries:
            console.print("[dim]No saved sessions available to resume.[/dim]")
            console.print()
            return None
        return await _prompt_session_picker(entries)

    async def _resume_session(selected_session_id: Optional[str] = None) -> None:
        nonlocal token_count
        if agent.messages:
            session_store.save_messages(agent.messages, session_id=agent.trace_session_id)

        target_session_id = selected_session_id or await _prompt_resume_session_choice()
        if not target_session_id:
            return

        if not _load_session_into_agent(target_session_id):
            console.print(f"[dim]Session {target_session_id} was not found.[/dim]")
            console.print()

    def _start_new_session() -> None:
        nonlocal token_count
        if agent.messages:
            session_store.save_messages(agent.messages, session_id=agent.trace_session_id)

        agent.clear_messages()
        agent.trace_session_id = session_store.new_session_id()
        token_count = 0
        console.clear()
        _print_header(agent)
        console.print(f"[italic dim]Started a new session {agent.trace_session_id}.[/italic dim]")
        console.print()

    def _exit_tui() -> None:
        session_store.save_messages(agent.messages, session_id=agent.trace_session_id)
        console.print(f"[dim]{build_resume_hint_text(agent.trace_session_id)}[/dim]")
        console.print("[dim]Bye![/dim]")

    if resume:
        target_session_id = resume_session_id or next(
            (entry.session_id for entry in session_store.list_sessions(limit=1)),
            None,
        )
        if target_session_id and _load_session_into_agent(target_session_id):
            pass
        elif resume_session_id:
            console.print(f"[italic dim]Session {resume_session_id} was not found. Started a new session.[/italic dim]")
            console.print()
            agent.trace_session_id = session_store.new_session_id()
        else:
            console.print("[italic dim]No previous session found. Started a new session.[/italic dim]")
            console.print()
            agent.trace_session_id = session_store.new_session_id()
    command_sources = [*BUILTIN_COMMANDS, *[skill.__dict__ for skill in getattr(agent, "skills", [])]]

    if not resume or not agent.messages:
        _print_header(agent)
    token_count = sum(len(message_text(m).split()) for m in agent.messages)
    model = getattr(agent, "model", "unknown")
    cwd_path = Path(getattr(agent, "cwd", cwd)).resolve().as_posix()
    session = PromptSession(
        key_bindings=kb,
        style=style,
        erase_when_done=True,
        completer=SlashCommandCompleter(command_sources),
        complete_while_typing=True,
    )

    def _runtime_event_key(event_type: str, payload: dict) -> tuple:
        if event_type in {"project_rules_loaded", "project_rules_context_injected"}:
            return event_type, payload.get("scope_count"), payload.get("context_message_count")
        if event_type == "skill_requested_detected":
            return event_type, payload.get("requested_skill_name"), payload.get("source")
        if event_type in {"skill_bundle_resolved", "skill_prompt_injected"}:
            return event_type, tuple(payload.get("loaded_skill_names", []))
        if event_type == "agent_decision":
            return event_type, payload.get("summary")
        return event_type, tuple(sorted(payload.items()))

    def _handle_runtime_event(event_type: str, payload: dict) -> None:
        key = _runtime_event_key(event_type, payload)
        if key in ui_state["runtime_event_keys"]:
            return
        ui_state["runtime_event_keys"].add(key)

        if event_type == "skill_requested_detected":
            _print_runtime_block(
                "Launching skill",
                [f"{payload.get('requested_skill_name')} ({payload.get('source')})"],
            )
            return
        if event_type == "project_rules_loaded":
            _print_runtime_block(
                "Loaded project rules",
                [f"{payload.get('scope_count', 0)} scope(s), {payload.get('char_count', 0)} chars"],
            )
            return
        if event_type == "project_rules_context_injected":
            _print_runtime_block(
                "Injected AGENTS context",
                [f"{payload.get('context_message_count', 0)} context message(s)"],
            )
            return
        if event_type == "skill_bundle_resolved":
            loaded = payload.get("loaded_skill_names", [])
            _print_runtime_block("Loaded skills", loaded or ["-"])
            return
        if event_type == "skill_prompt_injected":
            loaded = payload.get("loaded_skill_names", [])
            _print_runtime_block("Injected prompt", loaded or ["-"])
            return
        if event_type == "agent_decision":
            summary = str(payload.get("summary", "")).strip()
            if summary:
                _print_runtime_block("Agent note", [summary])

    agent.request_path_access = make_external_path_approval_handler(
        prompt_fn=_prompt_path_access,
        recorder_getter=lambda: getattr(agent, "trace_recorder", None),
    )
    agent.runtime_event_sink = _handle_runtime_event
    for middleware in getattr(agent, "middlewares", []):
        if isinstance(middleware, ApprovalMiddleware):
            middleware.prompt_fn = _prompt_modal

    with patch_stdout(raw=True):
        prompt_task = asyncio.create_task(_prompt_once())

        while True:
            text = await prompt_task

            if not text:
                prompt_task = asyncio.create_task(_prompt_once())
                continue

            if text in {"/exit", "/quit"}:
                _exit_tui()
                break

            if text == "/help":
                _print_user_message(text)
                console.print("[dim]Commands: /help, /new, /resume, /status, /abort, /cancel, /clear, /quit[/dim]")
                skills = getattr(agent, "skills", [])
                if skills:
                    console.print("[dim]Skills:[/dim]")
                    for skill in skills:
                        console.print(f"[dim]  /{skill.name} — {skill.description}[/dim]")
                console.print()
                prompt_task = asyncio.create_task(_prompt_once())
                continue

            if text == "/new":
                _print_user_message(text)
                _start_new_session()
                prompt_task = asyncio.create_task(_prompt_once())
                continue

            if text.startswith("/resume"):
                _print_user_message(text)
                requested_session_id = text.split(" ", 1)[1].strip() if " " in text else None
                await _resume_session(requested_session_id or None)
                prompt_task = asyncio.create_task(_prompt_once())
                continue

            if text == "/clear":
                agent.clear_messages()
                session_store.clear(session_id=agent.trace_session_id)
                token_count = 0
                console.clear()
                _print_header(agent)
                console.print("[dim]Cleared conversation history.[/dim]")
                console.print()
                prompt_task = asyncio.create_task(_prompt_once())
                continue

            if text == "/status":
                _print_user_message(text)
                console.print(f"[dim]{get_runtime_status(agent)}[/dim]")
                console.print()
                prompt_task = asyncio.create_task(_prompt_once())
                continue

            if text in {"/abort", "/cancel"}:
                _print_user_message(text)
                if ui_state["streaming"]:
                    agent.abort()
                    _print_runtime_block("Cancelling run", ["User requested abort"])
                else:
                    console.print("[dim]No active run to abort.[/dim]")
                    console.print()
                prompt_task = asyncio.create_task(_prompt_once())
                continue

            requested_skill_name = None
            if text.startswith("/"):
                token = text[1:].split(" ", 1)[0]
                skills = getattr(agent, "skills", [])
                if any(skill.name.lower() == token.lower() for skill in skills):
                    requested_skill_name = token
                    text = text.split(" ", 1)[1].strip() if " " in text else ""
                    if not text:
                        console.print(f"[dim]Selected skill /{token}. Continue typing your request after the command.[/dim]")
                        console.print()
                        prompt_task = asyncio.create_task(_prompt_once())
                        continue

            _print_user_message(text)
            ui_state["runtime_event_keys"] = set()

            ui_state["streaming"] = True
            
            # 重新启动带 "Input anything..." 提示的 prompt
            prompt_task = asyncio.create_task(_prompt_once())

            try:
                agent.set_requested_skill_name(requested_skill_name)

                # 初始显示炫酷的扫光动画
                ui_state["thinking_active"] = True
                ui_state["thinking_frame"] = 0
                ui_state["thinking_label"] = "Almost there..."
                ui_state["streaming_text"] = ""
                
                # 独立任务用于更新动画
                async def animate_thinking():
                    while ui_state["thinking_active"]:
                        ui_state["thinking_frame"] += 1
                        _invalidate_prompt()
                        await asyncio.sleep(0.08)

                asyncio.create_task(animate_thinking())

                def _clear_thinking() -> None:
                    if ui_state["thinking_active"]:
                        ui_state["thinking_active"] = False
                        _invalidate_prompt()

                def _on_delta(snapshot: Message) -> None:
                    new_text = message_text(snapshot)
                    if not new_text.strip():
                        return
                    ui_state["streaming_text"] = new_text
                    _clear_thinking()
                    _invalidate_prompt()

                def _on_tool_use(tool_use: ToolUsePart) -> None:
                    _clear_thinking()
                    # If we had streaming text, print it to stdout to keep it before tool output
                    if ui_state["streaming_text"]:
                        _print_assistant_text(ui_state["streaming_text"])
                        ui_state["streaming_text"] = ""
                        _invalidate_prompt()
                    
                    # 打印工具调用
                    line = Text()
                    line.append(f"○ {tool_use.name} ", style="dim")
                    line.append(summarize_payload(tool_use.input), style="dim")
                    console.print(line)

                def _on_tool_result(tool_use: ToolUsePart, result: ToolResultPart) -> None:
                    _clear_thinking()
                    marker = "✖" if result.is_error else "✓"
                    style = "red" if result.is_error else "dim"
                    truncated = result.content if len(result.content) <= 200 else result.content[:197] + "..."
                    line = Text()
                    line.append(f"  └─ {marker} ", style=style)
                    line.append(truncated, style=style)
                    console.print(line)

                final_message, duration = await run_agent_turn_stream(
                    agent,
                    text,
                    on_assistant_delta=_on_delta,
                    on_tool_use=_on_tool_use,
                    on_tool_result=_on_tool_result,
                )

                _clear_thinking()

                if ui_state["streaming_text"]:
                    # Print the final streaming text with rich so markdown is parsed
                    _print_assistant_text(ui_state["streaming_text"])
                    ui_state["streaming_text"] = ""
                    _invalidate_prompt()
                elif not message_text(final_message).strip():
                    console.print("[italic dim](no output; inspect `xagent trace latest` for the full event trail)[/italic dim]")
                    console.print()

                token_count = sum(len(message_text(m).split()) for m in agent.messages)
                session_store.save_messages(agent.messages, session_id=agent.trace_session_id)
                console.print(f"[italic dim]Completed in {duration:.2f}s[/italic dim]")
                console.print()

            except AgentAborted:
                console.print("[yellow]Aborted current run.[/yellow]")
                console.print()
            except Exception as exc:
                console.print(f"[bold red]Error: {exc}[/bold red]")
                console.print()
                format_runtime_error(exc)

            finally:
                agent.set_requested_skill_name(None)
                ui_state["streaming"] = False
                _invalidate_prompt()


class SlashCommandCompleter(Completer):
    def __init__(self, commands: List[Dict[str, str]]) -> None:
        self.commands = commands

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        if " " in text:
            return

        query = text[1:].lower()
        for command in self.commands:
            name = command["name"]
            description = command.get("description", "")
            if query and query not in f"{name} {description}".lower():
                continue
            yield Completion(
                f"/{name}",
                start_position=-len(text),
                display=f"/{name}",
                display_meta=description,
            )
