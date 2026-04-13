import json
from pathlib import Path
from typing import Optional, Set, Union

from xagent.foundation.messages import ToolUsePart
from xagent.foundation.runtime.paths import ensure_config_dir, get_approvals_file


MUTATING_TOOLS = {"write_file", "apply_patch", "bash", "mkdir", "move_path", "str_replace"}


def requires_approval(tool_name: str) -> bool:
    return tool_name in MUTATING_TOOLS


def describe_tool_use(tool_use: ToolUsePart) -> str:
    return f"{tool_use.name} {tool_use.input}"


class ApprovalStore:
    def __init__(self, cwd: Union[str, Path]) -> None:
        self.cwd = Path(cwd)
        self.path = get_approvals_file(self.cwd)
        self._allowed_tools = self._load()

    @property
    def allowed_tools(self) -> Set[str]:
        return set(self._allowed_tools)

    def is_allowed(self, tool_name: str) -> bool:
        return tool_name in self._allowed_tools

    def allow_tool(self, tool_name: str) -> None:
        self._allowed_tools.add(tool_name)
        self._save()

    def _load(self) -> Set[str]:
        if not self.path.exists():
            return set()

        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return set()

        tools = data.get("allowed_tools", [])
        if not isinstance(tools, list):
            return set()
        return {tool for tool in tools if isinstance(tool, str)}

    def _save(self) -> None:
        ensure_config_dir(self.cwd)
        payload = {"allowed_tools": sorted(self._allowed_tools)}
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
