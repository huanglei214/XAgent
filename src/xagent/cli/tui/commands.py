from typing import Dict, List, Optional


BUILTIN_COMMANDS: List[Dict[str, str]] = [
    {"name": "help", "description": "Show available slash commands", "type": "builtin"},
    {"name": "status", "description": "Show current session status", "type": "builtin"},
    {"name": "clear", "description": "Clear the current conversation history", "type": "builtin"},
    {"name": "exit", "description": "Exit the TUI", "type": "builtin"},
    {"name": "quit", "description": "Exit the TUI", "type": "builtin"},
]


def get_slash_query(text: str) -> Optional[str]:
    if not text.startswith("/"):
        return None
    stripped = text[1:]
    if " " in stripped:
        return None
    return stripped.lower()


def filter_commands(query: str, commands: Optional[List[Dict[str, str]]] = None) -> List[Dict[str, str]]:
    commands = commands or BUILTIN_COMMANDS
    normalized = query.strip().lower()
    if not normalized:
        return commands

    ranked = []
    for command in commands:
        haystack = f"{command['name']} {command['description']}".lower()
        if normalized in haystack:
            score = 2 if command["name"].startswith(normalized) else 1
            ranked.append((score, command))
    ranked.sort(key=lambda item: (-item[0], item[1]["name"]))
    return [item[1] for item in ranked]


def insert_command(command_name: str) -> str:
    return f"/{command_name} "
