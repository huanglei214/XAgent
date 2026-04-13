from pathlib import Path
from typing import Any

from xagent.config.paths import ensure_config_dir, get_chat_history_file


def create_prompt_session(cwd: str) -> Any:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
    from prompt_toolkit.completion import WordCompleter
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.key_binding import KeyBindings

    ensure_config_dir(Path(cwd))
    history_path = get_chat_history_file(Path(cwd))
    bindings = KeyBindings()

    @bindings.add("enter")
    def _(event) -> None:
        event.current_buffer.validate_and_handle()

    @bindings.add("escape", "enter")
    def _(event) -> None:
        event.current_buffer.insert_text("\n")

    completer = WordCompleter(
        ["/help", "/clear", "/exit", "/quit", "/status"],
        ignore_case=True,
        sentence=True,
    )

    return PromptSession(
        history=FileHistory(str(history_path)),
        multiline=True,
        key_bindings=bindings,
        completer=completer,
        complete_while_typing=True,
        reserve_space_for_menu=4,
        auto_suggest=AutoSuggestFromHistory(),
        bottom_toolbar="Enter submit | Esc+Enter newline | /help commands | /status session info",
        prompt_continuation=lambda width, line_number, wrap_count: "... ".rjust(width),
    )
