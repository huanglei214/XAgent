import asyncio
import json
import os
import sys
import shutil
from pathlib import Path
from typing import Dict, List, Optional

from rich.console import Console
from rich.text import Text
from prompt_toolkit import PromptSession
from prompt_toolkit.application.current import get_app_or_none
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style

from xagent.agent.session import SessionStore
from xagent.cli.runtime import (
    build_runtime_agent,
    format_runtime_error,
    get_runtime_status,
    run_agent_turn_stream,
)
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
                blocks.append(f"{prefix} {part.content}")
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
    line = Text()
    line.append("● ", style="bold blue")
    line.append(text)
    console.print(line)
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
    truncated = result.content if len(result.content) <= 200 else result.content[:197] + "..."
    line = Text()
    line.append(f"  └─ {marker} ", style=style)
    line.append(truncated, style=style)
    console.print(line)


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


def _build_input_panel_prompt(width: int) -> FormattedText:
    """构建输入行的 prompt（带顶部边框）。"""
    top = "─" * width
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


async def run_tui(cwd: str) -> None:
    """TUI 主入口 — 透明背景 + 固定底部输入 panel（非全屏）。"""
    agent = build_runtime_agent(cwd)
    agent.runtime_mode = "chat"

    session_store = SessionStore(cwd)
    session_id, restored_messages = session_store.load_state()
    agent.trace_session_id = session_id
    if restored_messages:
        agent.set_messages(restored_messages)
        console.print(
            f"[italic dim]Restored {len(restored_messages)} messages from the previous session.[/italic dim]"
        )
        console.print()

    _print_header(agent)

    token_count = 0

    model = getattr(agent, "model", "unknown")
    cwd_path = Path(getattr(agent, "cwd", cwd)).resolve().as_posix()

    ui_state = {
        "streaming": False,
        "streaming_hint": "",
    }

    style = Style.from_dict(
        {
            # 终端历史输出保持透明（继承终端主题），输入区也透明
            "": "",
            "border": "fg:#6c6c6c",
            "prompt": "fg:#ffffff bold",
            "status": "fg:#a0a0a0",
            # 覆盖 prompt_toolkit 的默认底栏样式，防止自带反色(reverse)或灰色背景
            "bottom-toolbar": "noreverse bg:default fg:default",
            "bottom-toolbar.text": "noreverse bg:default fg:default",
            "thinking": "fg:#a0a0a0 bg:#2b2b2b",  # Thinking 条有灰色背景
        }
    )

    kb = KeyBindings()

    @kb.add("enter")
    def _on_enter(event) -> None:
        # 流式输出期间不允许提交（但允许继续编辑）。
        if ui_state["streaming"]:
            return
        event.current_buffer.validate_and_handle()

    session = PromptSession(key_bindings=kb, style=style, erase_when_done=True)

    def _invalidate_prompt() -> None:
        app = get_app_or_none()
        if app is not None:
            app.invalidate()

    async def _prompt_once() -> str:
        width = _get_terminal_width()
        prompt = lambda: _build_input_panel_prompt(width)  # noqa: E731
        bottom_toolbar = lambda: _build_input_panel_bottom_toolbar(  # noqa: E731
            width=width,
            model=model,
            cwd=cwd_path,
            token_count=token_count,
        )
        return (
            await session.prompt_async(
                message=prompt,
                rprompt=_build_input_panel_rprompt,
                bottom_toolbar=bottom_toolbar,
            )
        ).strip()

    with patch_stdout(raw=True):
        prompt_task = asyncio.create_task(_prompt_once())

        while True:
            text = await prompt_task

            if not text:
                prompt_task = asyncio.create_task(_prompt_once())
                continue

            if text in {"/exit", "/quit"}:
                session_store.save_messages(agent.messages, session_id=agent.trace_session_id)
                console.print("[dim]Bye![/dim]")
                break

            if text == "/help":
                _print_user_message(text)
                console.print("[dim]Commands: /help, /clear, /status, /exit[/dim]")
                console.print()
                prompt_task = asyncio.create_task(_prompt_once())
                continue

            if text == "/clear":
                agent.clear_messages()
                session_store.clear()
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

            _print_user_message(text)

            streaming_text = ""
            assistant_line_open = False

            try:
                # 在流式输出期间，我们直接打印内容，不再使用任何框架劫持 stdout
                # 这样可以保证打字效果 100% 原生和自然，不会吞字或截断。
                
                # 初始显示 Thinking 提示
                sys.stdout.write("\n\033[90m* Thinking...\033[0m\n")
                sys.stdout.flush()
                
                thinking_shown = True

                def _clear_thinking() -> None:
                    nonlocal thinking_shown
                    if thinking_shown:
                        # 上移两行并清除（清除 \n 和 Thinking... 及其后的内容）
                        sys.stdout.write("\033[2A\033[J")
                        sys.stdout.flush()
                        thinking_shown = False

                def _on_delta(snapshot: Message) -> None:
                    nonlocal streaming_text, assistant_line_open
                    new_text = message_text(snapshot)
                    if not new_text.strip():
                        return
                    delta = (
                        new_text[len(streaming_text) :]
                        if new_text.startswith(streaming_text)
                        else new_text
                    )
                    streaming_text = new_text
                    if not delta:
                        return
                    
                    _clear_thinking()
                    
                    if not assistant_line_open:
                        # 蓝色的圆点
                        sys.stdout.write("\033[1;34m● \033[0m")
                        assistant_line_open = True
                    sys.stdout.write(delta)
                    sys.stdout.flush()

                def _on_tool_use(tool_use: ToolUsePart) -> None:
                    nonlocal streaming_text, assistant_line_open
                    _clear_thinking()
                    
                    if assistant_line_open:
                        sys.stdout.write("\n\n")
                        assistant_line_open = False
                    
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

                _, duration = await run_agent_turn_stream(
                    agent,
                    text,
                    on_assistant_delta=_on_delta,
                    on_tool_use=_on_tool_use,
                    on_tool_result=_on_tool_result,
                )

                _clear_thinking()

                if assistant_line_open:
                    console.print()
                    console.print()

                token_count += sum(len(message_text(m).split()) for m in agent.messages)
                session_store.save_messages(agent.messages, session_id=agent.trace_session_id)
                console.print(f"[italic dim]Completed in {duration:.2f}s[/italic dim]")
                console.print()

            except Exception as exc:
                console.print(f"[bold red]Error: {exc}[/bold red]")
                console.print()
                format_runtime_error(exc)

            finally:
                prompt_task = asyncio.create_task(_prompt_once())
