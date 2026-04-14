from __future__ import annotations

import json
import shlex
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Set, Union

from xagent.foundation.messages import ToolUsePart
from xagent.foundation.runtime.paths import ensure_config_dir, get_approvals_file


MUTATING_TOOLS = {"write_file", "apply_patch", "bash", "mkdir", "move_path", "str_replace"}
SCOPED_APPROVAL_TTL_SECONDS = 7 * 24 * 60 * 60


@dataclass
class ApprovalRule:
    tool_name: str
    scope_type: str
    command_prefix: list[str] | None = None
    path_prefixes: list[str] | None = None
    expires_at: Optional[str] = None


def requires_approval(tool_name: str) -> bool:
    return tool_name in MUTATING_TOOLS


def describe_tool_use(tool_use: ToolUsePart) -> str:
    return f"{tool_use.name} {tool_use.input}"


def describe_scoped_rule(tool_use: ToolUsePart, cwd: Union[str, Path]) -> str:
    command_prefix = _extract_command_prefix(tool_use)
    if command_prefix:
        return f"command prefix: {' '.join(command_prefix)}"
    path_prefixes = _extract_path_prefixes(tool_use, cwd)
    if path_prefixes:
        return "paths: " + ", ".join(path_prefixes)
    return f"tool: {tool_use.name}"


class ApprovalStore:
    def __init__(self, cwd: Union[str, Path]) -> None:
        self.cwd = Path(cwd)
        self.path = get_approvals_file(self.cwd)
        self._allowed_tools, self._scoped_rules = self._load()

    @property
    def allowed_tools(self) -> Set[str]:
        return set(self._allowed_tools)

    @property
    def scoped_rules(self) -> list[ApprovalRule]:
        self._prune_expired_rules()
        return list(self._scoped_rules)

    def is_allowed(self, tool_name: str) -> bool:
        return tool_name in self._allowed_tools

    def is_allowed_tool_use(self, tool_use: ToolUsePart, cwd: Union[str, Path]) -> bool:
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
        cwd: Union[str, Path],
        ttl_seconds: int = SCOPED_APPROVAL_TTL_SECONDS,
    ) -> Optional[ApprovalRule]:
        rule = _build_rule(tool_use, cwd, ttl_seconds)
        if rule is None:
            return None
        self._prune_expired_rules()
        self._scoped_rules = [existing for existing in self._scoped_rules if existing != rule]
        self._scoped_rules.append(rule)
        self._save()
        return rule

    def _load(self) -> tuple[Set[str], list[ApprovalRule]]:
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


def _build_rule(tool_use: ToolUsePart, cwd: Union[str, Path], ttl_seconds: int) -> Optional[ApprovalRule]:
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


def _rule_matches(rule: ApprovalRule, tool_use: ToolUsePart, cwd: Union[str, Path]) -> bool:
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


def _extract_path_prefixes(tool_use: ToolUsePart, cwd: Union[str, Path]) -> list[str]:
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
    if path == prefix:
        return True
    try:
        path.relative_to(prefix)
        return True
    except ValueError:
        return False


def _expires_at(ttl_seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()


def _is_expired(rule: ApprovalRule) -> bool:
    if not rule.expires_at:
        return False
    try:
        expires_at = datetime.fromisoformat(rule.expires_at)
    except ValueError:
        return True
    return expires_at <= datetime.now(timezone.utc)


def _filter_unexpired(rules: list[ApprovalRule]) -> list[ApprovalRule]:
    return [rule for rule in rules if not _is_expired(rule)]
