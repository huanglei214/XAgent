from __future__ import annotations

import inspect
import json
import shlex
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import typer

from xagent.agent.core.middleware import AgentMiddleware
from xagent.agent.core.runtime_events import emit_runtime_event
from xagent.bus.types import ToolResultPart, ToolUsePart
from xagent.foundation.runtime.paths import ensure_config_dir, find_project_root, get_approvals_file
from xagent.foundation.runtime.workspace_paths import resolve_workspace_path


MUTATING_TOOLS = {"write_file", "apply_patch", "bash", "mkdir", "move_path", "str_replace"}
SCOPED_APPROVAL_TTL_SECONDS = 7 * 24 * 60 * 60

FILE_INSPECTION_TOOLS = {"read_file", "file_info"}
PATH_DISCOVERY_TOOLS = {"list_files", "glob_search", "grep_search"}
GUARDED_WRITE_TOOLS = {"write_file", "str_replace", "apply_patch", "move_path"}


@dataclass
class ApprovalRule:
    tool_name: str
    scope_type: str
    command_prefix: list[str] | None = None
    path_prefixes: list[str] | None = None
    expires_at: str | None = None


def requires_approval(tool_name: str) -> bool:
    """判断指定工具是否需要审批。"""
    return tool_name in MUTATING_TOOLS


def describe_tool_use(tool_use: ToolUsePart) -> str:
    """生成工具调用的可读描述。"""
    return f"{tool_use.name} {tool_use.input}"


def describe_scoped_rule(tool_use: ToolUsePart, cwd: str | Path) -> str:
    """生成作用域规则的可读描述。"""
    command_prefix = _extract_command_prefix(tool_use)
    if command_prefix:
        return f"command prefix: {' '.join(command_prefix)}"
    path_prefixes = _extract_path_prefixes(tool_use, cwd)
    if path_prefixes:
        return "paths: " + ", ".join(path_prefixes)
    return f"tool: {tool_use.name}"


def load_project_rules(start: Path | None = None) -> str | None:
    """从项目根目录到当前目录逐层加载 AGENTS.md 规则文件。"""
    current = (start or Path.cwd()).resolve()
    if current.is_file():
        current = current.parent

    root = find_project_root(current)
    segments = []
    for directory in _iter_rule_directories(root, current):
        rules_path = directory / "AGENTS.md"
        if not rules_path.exists():
            continue
        body = rules_path.read_text(encoding="utf-8").strip()
        if not body:
            continue
        relative = directory.relative_to(root)
        scope = "." if str(relative) == "." else relative.as_posix()
        segments.append(
            "\n".join(
                [
                    f'<agents_scope path="{scope}/AGENTS.md">',
                    body,
                    "</agents_scope>",
                ]
            )
        )
    if not segments:
        return None
    return "\n\n".join(segments)


def _build_rule(tool_use: ToolUsePart, cwd: str | Path, ttl_seconds: int) -> ApprovalRule | None:
    """根据工具调用构建作用域审批规则。"""
    expires_at = _expires_at(ttl_seconds)
    command_prefix = _extract_command_prefix(tool_use)
    if command_prefix:
        return ApprovalRule(
            tool_name=tool_use.name,
            scope_type="command_prefix",
            command_prefix=command_prefix,
            expires_at=expires_at,
        )

    path_prefixes = _extract_path_prefixes(tool_use, cwd)
    if path_prefixes:
        return ApprovalRule(
            tool_name=tool_use.name,
            scope_type="path_prefixes",
            path_prefixes=path_prefixes,
            expires_at=expires_at,
        )
    return None


def _rule_matches(rule: ApprovalRule, tool_use: ToolUsePart, cwd: str | Path) -> bool:
    """判断规则是否匹配当前工具调用。"""
    if _is_expired(rule):
        return False
    if rule.scope_type == "command_prefix":
        prefix = rule.command_prefix or []
        current = _extract_command_prefix(tool_use)
        return bool(prefix) and current[: len(prefix)] == prefix
    if rule.scope_type == "path_prefixes":
        prefixes = [Path(item) for item in rule.path_prefixes or []]
        paths = [Path(item) for item in _extract_path_prefixes(tool_use, cwd)]
        if not prefixes or not paths:
            return False
        return all(any(_path_matches_prefix(path, prefix) for prefix in prefixes) for path in paths)
    return False


def _extract_command_prefix(tool_use: ToolUsePart) -> list[str]:
    """从 bash 工具调用中提取命令前缀。"""
    if tool_use.name != "bash":
        return []
    command = str(tool_use.input.get("command", "")).strip()
    if not command:
        return []
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    if not tokens:
        return []
    return tokens[: min(2, len(tokens))]


def _extract_path_prefixes(tool_use: ToolUsePart, cwd: str | Path) -> list[str]:
    """从工具调用输入中提取路径前缀列表。"""
    prefixes: list[str] = []
    for key in ("path", "source", "destination"):
        raw_value = tool_use.input.get(key)
        if not isinstance(raw_value, str) or not raw_value.strip():
            continue
        raw_path = Path(raw_value).expanduser()
        resolved = raw_path.resolve() if raw_path.is_absolute() else (Path(cwd).resolve() / raw_path).resolve()
        prefixes.append(str(resolved))
    return prefixes


def _path_matches_prefix(path: Path, prefix: Path) -> bool:
    """判断路径是否匹配给定前缀。"""
    if path == prefix:
        return True
    try:
        path.relative_to(prefix)
        return True
    except ValueError:
        return False


def _expires_at(ttl_seconds: int) -> str:
    """计算过期时间的 ISO 格式字符串。"""
    return (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()


def _is_expired(rule: ApprovalRule) -> bool:
    """判断审批规则是否已过期。"""
    if not rule.expires_at:
        return False
    try:
        expires_at = datetime.fromisoformat(rule.expires_at)
    except ValueError:
        return True
    return expires_at <= datetime.now(timezone.utc)


def _filter_unexpired(rules: list[ApprovalRule]) -> list[ApprovalRule]:
    """过滤掉已过期的审批规则。"""
    return [rule for rule in rules if not _is_expired(rule)]


def _iter_rule_directories(root: Path, current: Path) -> list[Path]:
    """从项目根目录到当前目录逐层迭代所有规则目录。"""
    if current == root:
        return [root]
    relative = current.relative_to(root)
    directories = [root]
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        directories.append(cursor)
    return directories


def _record_approval_feedback(recorder, tool_name: str, decision: str) -> None:
    """记录审批决策的反馈信息。"""
    if recorder is None:
        return
    recorder.emit(
        "approval_decided",
        payload={"tool_name": tool_name, "decision": decision},
        tags={"tool_name": tool_name, "feedback_type": "approval"},
    )
    recorder.emit(
        "user_feedback",
        payload={"kind": "approval", "decision": decision, "tool_name": tool_name},
        tags={"tool_name": tool_name},
    )


class ApprovalStore:
    """管理工具审批状态，包括全局允许和作用域规则的持久化。"""

    def __init__(self, cwd: str | Path) -> None:
        self.cwd = Path(cwd)
        self.path = get_approvals_file(self.cwd)
        self._allowed_tools, self._scoped_rules = self._load()

    @property
    def allowed_tools(self) -> set[str]:
        return set(self._allowed_tools)

    @property
    def scoped_rules(self) -> list[ApprovalRule]:
        self._prune_expired_rules()
        return list(self._scoped_rules)

    def is_allowed(self, tool_name: str) -> bool:
        return tool_name in self._allowed_tools

    def is_allowed_tool_use(self, tool_use: ToolUsePart, cwd: str | Path) -> bool:
        if self.is_allowed(tool_use.name):
            return True
        self._prune_expired_rules()
        for rule in self._scoped_rules:
            if rule.tool_name != tool_use.name:
                continue
            if _rule_matches(rule, tool_use, cwd):
                return True
        return False

    def allow_tool(self, tool_name: str) -> None:
        self._allowed_tools.add(tool_name)
        self._save()

    def allow_scoped_tool_use(
        self,
        tool_use: ToolUsePart,
        cwd: str | Path,
        ttl_seconds: int = SCOPED_APPROVAL_TTL_SECONDS,
    ) -> ApprovalRule | None:
        rule = _build_rule(tool_use, cwd, ttl_seconds)
        if rule is None:
            return None
        self._prune_expired_rules()
        self._scoped_rules = [existing for existing in self._scoped_rules if existing != rule]
        self._scoped_rules.append(rule)
        self._save()
        return rule

    def _load(self) -> tuple[set[str], list[ApprovalRule]]:
        if not self.path.exists():
            return set(), []

        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return set(), []

        tools = data.get("allowed_tools", [])
        allowed_tools = {tool for tool in tools if isinstance(tool, str)} if isinstance(tools, list) else set()

        raw_rules = data.get("scoped_rules", [])
        scoped_rules: list[ApprovalRule] = []
        if isinstance(raw_rules, list):
            for item in raw_rules:
                if not isinstance(item, dict):
                    continue
                try:
                    scoped_rules.append(ApprovalRule(**item))
                except TypeError:
                    continue
        return allowed_tools, _filter_unexpired(scoped_rules)

    def _save(self) -> None:
        self._scoped_rules = _filter_unexpired(self._scoped_rules)
        ensure_config_dir(self.cwd)
        payload = {
            "allowed_tools": sorted(self._allowed_tools),
            "scoped_rules": [asdict(rule) for rule in self._scoped_rules],
        }
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def _prune_expired_rules(self) -> None:
        fresh = _filter_unexpired(self._scoped_rules)
        if len(fresh) != len(self._scoped_rules):
            self._scoped_rules = fresh
            ensure_config_dir(self.cwd)
            payload = {
                "allowed_tools": sorted(self._allowed_tools),
                "scoped_rules": [asdict(rule) for rule in self._scoped_rules],
            }
            self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


class ApprovalMiddleware(AgentMiddleware):
    """在执行变更工具前拦截并请求用户审批。"""

    def __init__(self, approval_store: ApprovalStore, prompt_fn: Callable[[str], str] | None = None) -> None:
        self.approval_store = approval_store
        self.prompt_fn = prompt_fn or (lambda prompt: typer.prompt(prompt, default="n"))

    async def before_tool(self, *, agent, tool_use: ToolUsePart) -> ToolResultPart | None:
        if not requires_approval(tool_use.name):
            return None
        cwd = getattr(agent, "cwd", ".")
        if self.approval_store.is_allowed_tool_use(tool_use, cwd=cwd):
            return None

        recorder = getattr(agent, "trace_recorder", None)
        if recorder is not None:
            recorder.emit(
                "approval_requested",
                payload=tool_use.model_dump(mode="json"),
                tags={"tool_name": tool_use.name},
            )

        scope_description = describe_scoped_rule(tool_use, cwd=cwd)
        prompt = (
            f"Allow {tool_use.name} with input {json.dumps(tool_use.input, ensure_ascii=False)}? "
            f"[y]es once/[n]o/[s]cope 7d ({scope_description})/[a]llways tool"
        )

        while True:
            decision = self.prompt_fn(prompt)
            if inspect.isawaitable(decision):
                decision = await decision
            decision = str(decision).strip().lower()
            if decision in {"y", "yes"}:
                _record_approval_feedback(recorder, tool_use.name, "allow_once")
                return None
            if decision in {"s", "scope"}:
                rule = self.approval_store.allow_scoped_tool_use(tool_use, cwd=cwd)
                _record_approval_feedback(recorder, tool_use.name, "allow_scope")
                if rule is None:
                    return None
                return None
            if decision in {"a", "always"}:
                self.approval_store.allow_tool(tool_use.name)
                _record_approval_feedback(recorder, tool_use.name, "allow_always")
                return None
            if decision in {"n", "no"}:
                _record_approval_feedback(recorder, tool_use.name, "deny")
                return ToolResultPart(
                    tool_use_id=tool_use.id,
                    content=f"Execution denied for tool '{tool_use.name}'.",
                    is_error=True,
                )


class EditGuardrailsMiddleware(AgentMiddleware):
    """确保在修改文件前已通过检查工具查看过该文件。"""

    def __init__(self) -> None:
        self.inspected_paths: set[str] = set()
        self.discovered_paths: set[str] = set()

    async def before_tool(self, *, agent, tool_use: ToolUsePart) -> ToolResultPart | None:
        if tool_use.name not in GUARDED_WRITE_TOOLS:
            return None

        try:
            if tool_use.name == "move_path":
                source = self._resolve(agent.cwd, tool_use.input.get("source"))
                if source.exists() and not self._has_seen(source):
                    return self._deny(
                        tool_use,
                        f"Read or inspect {tool_use.input.get('source')} before moving it. "
                        "Use read_file or file_info first.",
                    )
                return None

            path = self._resolve(agent.cwd, tool_use.input.get("path"))
        except Exception as exc:
            return self._deny(tool_use, str(exc))

        if tool_use.name == "write_file" and not path.exists():
            return None

        if path.exists() and not self._has_seen(path):
            return self._deny(
                tool_use,
                f"Inspect {tool_use.input.get('path')} before modifying it. "
                "Use read_file or file_info first.",
            )
        return None

    async def after_tool(self, *, agent, tool_use: ToolUsePart, result: ToolResultPart) -> None:
        if result.is_error:
            return

        if tool_use.name in FILE_INSPECTION_TOOLS:
            try:
                path = self._resolve(agent.cwd, tool_use.input.get("path"))
            except Exception:
                return
            self.inspected_paths.add(str(path))
            return

        if tool_use.name in PATH_DISCOVERY_TOOLS:
            try:
                path = self._resolve(agent.cwd, tool_use.input.get("path", "."))
            except Exception:
                return
            self.discovered_paths.add(str(path))

    def _has_seen(self, path: Path) -> bool:
        """判断路径是否已被检查或发现过。"""
        key = str(path)
        if key in self.inspected_paths:
            return True
        return str(path.parent) in self.discovered_paths

    def _resolve(self, cwd: str, raw_path) -> Path:
        """解析工具输入中的路径为绝对路径。"""
        if not isinstance(raw_path, str) or not raw_path:
            raise ValueError("Tool input is missing a valid path.")
        return resolve_workspace_path(cwd, raw_path)

    def _deny(self, tool_use: ToolUsePart, message: str) -> ToolResultPart:
        """生成拒绝执行的工具结果。"""
        return ToolResultPart(
            tool_use_id=tool_use.id,
            content=message,
            is_error=True,
        )


class ProjectRulesMiddleware(AgentMiddleware):
    """在 Agent 运行前注入项目规则，并记录相关事件。"""

    def __init__(self, project_rules: str) -> None:
        self.project_rules = project_rules
        self.scope_count = project_rules.count("<agents_scope ")

    async def before_agent_run(self, *, agent, user_text: str) -> None:
        recorder = getattr(agent, "trace_recorder", None)
        payload = {
            "scope_count": self.scope_count,
            "char_count": len(self.project_rules),
        }
        if recorder is not None:
            recorder.emit("project_rules_loaded", payload=payload, tags={"scope_count": self.scope_count})
        await emit_runtime_event(agent, "project_rules_loaded", payload)

    async def before_model(self, *, agent, request):
        if not getattr(agent, "context_messages", None):
            return None
        payload = {
            "scope_count": self.scope_count,
            "context_message_count": len(agent.context_messages),
        }
        recorder = getattr(agent, "trace_recorder", None)
        if recorder is not None:
            recorder.emit(
                "project_rules_context_injected",
                payload=payload,
                tags={"scope_count": self.scope_count, "context_message_count": len(agent.context_messages)},
            )
        await emit_runtime_event(agent, "project_rules_context_injected", payload)
        return None
