from __future__ import annotations

import asyncio
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast

from xagent.agent.permissions import Approver
from xagent.agent.tools.base import Tool, ToolResult, tool
from xagent.config import DEFAULT_SHELL_BLACKLIST, ShellPermissionConfig

ShellDefault = Literal["allow", "ask", "deny"]

_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*")
_CONTROL_CHARS = set(";&|()")
_REDIRECTION_RULES = {">", ">>", ">|", "&>", "2>", "2>>"}


@dataclass(frozen=True)
class ShellPolicyDecision:
    allowed: bool
    needs_approval: bool = False
    reason: str | None = None
    matched_rule: str | None = None


@dataclass(frozen=True)
class ShellPolicy:
    default: ShellDefault = "allow"
    blacklist: tuple[str, ...] = field(default_factory=lambda: DEFAULT_SHELL_BLACKLIST)

    def __post_init__(self) -> None:
        if self.default not in {"allow", "ask", "deny"}:
            raise ValueError("shell policy default must be 'allow', 'ask', or 'deny'")

    @classmethod
    def from_config(cls, config: ShellPermissionConfig) -> "ShellPolicy":
        return cls(
            default=cast(ShellDefault, config.default),
            blacklist=tuple(config.blacklist),
        )

    def evaluate(self, command: str) -> ShellPolicyDecision:
        try:
            matched_rule = self.match_blacklist(command)
        except ValueError as exc:
            return ShellPolicyDecision(
                allowed=False,
                reason=f"Shell command denied: could not parse command for policy check: {exc}",
            )
        if matched_rule is not None:
            return ShellPolicyDecision(
                allowed=False,
                reason=f"Shell command denied by blacklist rule: {matched_rule}",
                matched_rule=matched_rule,
            )
        if self.default == "deny":
            return ShellPolicyDecision(
                allowed=False,
                reason="Shell command denied by permissions.shell.default=deny.",
            )
        return ShellPolicyDecision(
            allowed=True,
            needs_approval=self.default == "ask",
        )

    def match_blacklist(self, command: str) -> str | None:
        tokens = _split_shell_tokens(command)
        command_starts = _command_start_indices(tokens)
        for rule in self.blacklist:
            rule_tokens = _split_shell_tokens(rule)
            if not rule_tokens:
                continue
            if _is_redirection_rule(rule, rule_tokens):
                if _contains_sequence(tokens, rule_tokens):
                    return rule
                continue
            for start in command_starts:
                if tokens[start : start + len(rule_tokens)] == rule_tokens:
                    return rule
        return None


@tool(
    name="shell",
    description="Run a shell command in the workspace.",
    exclusive=True,
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "timeout_seconds": {"type": "integer", "default": 60},
        },
        "required": ["command"],
    },
)
class ShellTool(Tool):
    def __init__(
        self,
        cwd: Path,
        approver: Approver,
        shell_policy: ShellPolicy | None = None,
    ) -> None:
        self.cwd = cwd
        self.approver = approver
        self.shell_policy = shell_policy or ShellPolicy()

    async def execute(self, command: str, timeout_seconds: int = 60) -> ToolResult:
        decision = self.shell_policy.evaluate(command)
        if not decision.allowed:
            return ToolResult.fail(decision.reason or "Shell command denied.")
        if decision.needs_approval:
            await _require(self.approver, "command", self.cwd.as_posix(), summary=command)
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=self.cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            return ToolResult.fail(f"Command timed out after {timeout_seconds}s.")
        content = (
            f"exit_code={process.returncode}\n"
            f"stdout:\n{stdout.decode(errors='replace')}\n"
            f"stderr:\n{stderr.decode(errors='replace')}"
        )
        return ToolResult(content=content, is_error=process.returncode != 0)


def _split_shell_tokens(value: str) -> list[str]:
    lexer = shlex.shlex(value, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    return list(lexer)


def _command_start_indices(tokens: list[str]) -> list[int]:
    starts: list[int] = []
    expect_command = True
    skip_redirection_target = False
    for index, token in enumerate(tokens):
        if skip_redirection_target:
            skip_redirection_target = False
            continue
        if _is_control_token(token):
            expect_command = True
            continue
        if token.isdigit() and index + 1 < len(tokens) and _is_redirection_token(tokens[index + 1]):
            continue
        if _is_redirection_token(token):
            skip_redirection_target = True
            continue
        if expect_command:
            if _ASSIGNMENT_RE.match(token):
                continue
            starts.append(index)
            expect_command = False
    return starts


def _contains_sequence(tokens: list[str], needle: list[str]) -> bool:
    if len(needle) > len(tokens):
        return False
    return any(tokens[index : index + len(needle)] == needle for index in range(len(tokens)))


def _is_redirection_rule(rule: str, tokens: list[str]) -> bool:
    return rule in _REDIRECTION_RULES or any(_is_redirection_token(token) for token in tokens)


def _is_redirection_token(token: str) -> bool:
    return token in _REDIRECTION_RULES or token in {">", ">>", ">|", "&>"}


def _is_control_token(token: str) -> bool:
    return bool(token) and set(token) <= _CONTROL_CHARS and ">" not in token


async def _require(approver: Approver, action: str, target: str, *, summary: str) -> None:
    allowed = await approver.require(action, target, summary=summary)
    if not allowed:
        raise PermissionError(f"Denied {action} for {target}")
