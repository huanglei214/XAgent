from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Any

from xagent.agent.prompts import PromptRenderer
from xagent.agent.runner import call_model
from xagent.config import xagent_home
from xagent.providers import ModelRequest
from xagent.session import local_now, sanitize_id

if TYPE_CHECKING:
    from xagent.agent.loop import Agent
    from xagent.session import Session


SECTION_ALLOWLIST: dict[str, set[str]] = {
    "workspace": {"项目定位", "架构决策", "当前约定", "已完成事项", "待处理事项", "注意事项"},
    "user": {"个人信息", "交流偏好", "工程偏好", "协作偏好"},
    "soul": {"沟通方式", "思考方式", "执行原则"},
}

VALID_OPERATIONS = {"append", "update", "delete"}


@dataclass(frozen=True)
class MemoryBundle:
    soul: str
    user: str
    workspace: str
    workspace_id: str
    workspace_path: Path
    memory_path: Path

    @classmethod
    def empty(cls, workspace_path: Path) -> MemoryBundle:
        return cls(
            soul="",
            user="",
            workspace="",
            workspace_id="",
            workspace_path=workspace_path,
            memory_path=workspace_path,
        )


class MemoryStore:
    """管理长期 Markdown memory 的路径、初始化和读写。"""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or xagent_home() / "memory"

    @property
    def user_path(self) -> Path:
        return self.root / "user.md"

    @property
    def soul_path(self) -> Path:
        return self.root / "soul.md"

    @property
    def user_backup_path(self) -> Path:
        return self.root / "user.md.bak"

    @property
    def soul_backup_path(self) -> Path:
        return self.root / "soul.md.bak"

    def load_bundle(self, workspace_path: Path) -> MemoryBundle:
        paths = self.workspace_paths(workspace_path)
        self.ensure_workspace(workspace_path)
        return MemoryBundle(
            soul=self.soul_path.read_text(encoding="utf-8"),
            user=self.user_path.read_text(encoding="utf-8"),
            workspace=paths.memory_path.read_text(encoding="utf-8"),
            workspace_id=paths.workspace_id,
            workspace_path=workspace_path.resolve(),
            memory_path=paths.memory_path,
        )

    def ensure_workspace(self, workspace_path: Path) -> None:
        paths = self.workspace_paths(workspace_path)
        self.root.mkdir(parents=True, exist_ok=True)
        _ensure_file(self.user_path, _memory_template("user.md"))
        _ensure_file(self.soul_path, _memory_template("soul.md"))
        paths.path.mkdir(parents=True, exist_ok=True)
        _ensure_file(paths.memory_path, _memory_template("memory.md"))
        if not paths.meta_path.exists():
            paths.meta_path.write_text(
                json.dumps(
                    {
                        "workspace_id": paths.workspace_id,
                        "workspace_path": str(workspace_path.resolve()),
                        "created_at": local_now(),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        if not paths.dream_state_path.exists():
            self.write_dream_state(paths.workspace_id, self.default_dream_state())

    def read_dream_state(self, workspace_path: Path) -> dict[str, Any]:
        paths = self.workspace_paths(workspace_path)
        self.ensure_workspace(workspace_path)
        return json.loads(paths.dream_state_path.read_text(encoding="utf-8"))

    def write_dream_state(self, workspace_id: str, state: dict[str, Any]) -> None:
        paths = self.workspace_paths_by_id(workspace_id)
        paths.path.mkdir(parents=True, exist_ok=True)
        paths.dream_state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def update_dream_state_for_session(
        self,
        *,
        workspace_path: Path,
        session_id: str,
        latest_summary_id: str,
    ) -> None:
        paths = self.workspace_paths(workspace_path)
        state = self.read_dream_state(workspace_path)
        sessions = state.setdefault("sessions", {})
        sessions[session_id] = {
            "last_summary_id": latest_summary_id,
            "last_dream_at": local_now(),
        }
        self.write_dream_state(paths.workspace_id, state)

    def last_dream_summary_id(self, *, workspace_path: Path, session_id: str) -> str | None:
        state = self.read_dream_state(workspace_path)
        session_state = state.get("sessions", {}).get(session_id, {})
        if not isinstance(session_state, dict):
            return None
        if isinstance(session_state.get("last_summary_id"), str):
            return session_state["last_summary_id"]
        if isinstance(session_state.get("workspace_last_summary_id"), str):
            return session_state["workspace_last_summary_id"]
        scopes = session_state.get("scopes", {})
        if isinstance(scopes, dict):
            workspace_scope = scopes.get("workspace", {})
            if isinstance(workspace_scope, dict) and isinstance(workspace_scope.get("last_summary_id"), str):
                return workspace_scope["last_summary_id"]
        return None

    def write_workspace_memory(self, workspace_path: Path, content: str) -> Path:
        paths = self.workspace_paths(workspace_path)
        self.ensure_workspace(workspace_path)
        _write_memory_file(paths.memory_path, paths.backup_path, content)
        return paths.memory_path

    def write_user_memory(self, content: str) -> Path:
        _write_memory_file(self.user_path, self.user_backup_path, content)
        return self.user_path

    def write_soul_memory(self, content: str) -> Path:
        _write_memory_file(self.soul_path, self.soul_backup_path, content)
        return self.soul_path

    def workspace_paths(self, workspace_path: Path) -> WorkspaceMemoryPaths:
        workspace_id = workspace_memory_id(workspace_path)
        return self.workspace_paths_by_id(workspace_id)

    def workspace_paths_by_id(self, workspace_id: str) -> WorkspaceMemoryPaths:
        path = self.root / "workspaces" / workspace_id
        return WorkspaceMemoryPaths(
            workspace_id=workspace_id,
            path=path,
            meta_path=path / "meta.json",
            memory_path=path / "memory.md",
            backup_path=path / "memory.md.bak",
            dream_state_path=path / "dream_state.json",
        )

    @staticmethod
    def default_dream_state() -> dict[str, Any]:
        return {"sessions": {}}


@dataclass(frozen=True)
class WorkspaceMemoryPaths:
    workspace_id: str
    path: Path
    meta_path: Path
    memory_path: Path
    backup_path: Path
    dream_state_path: Path


class Dream:
    """把 compact summary 整理成结构化 memory operations 并应用。"""

    def __init__(self, prompt_renderer: PromptRenderer | None = None) -> None:
        self.prompt_renderer = prompt_renderer or PromptRenderer()

    async def run(self, *, agent: Agent, compact: bool = False) -> None:
        if agent.memory_store is None:
            raise RuntimeError("Memory is not enabled for this agent.")
        if compact:
            await agent.compact(force=True)

        store = agent.memory_store
        session = agent.session
        bundle = store.load_bundle(session.workspace_path)
        last_summary_id = store.last_dream_summary_id(
            workspace_path=session.workspace_path,
            session_id=session.session_id,
        )
        summaries = session.summary_records_after(last_summary_id)
        if not summaries:
            session.append_trace(
                "memory_update",
                {"skipped": "no_new_summary"},
            )
            return

        operations = await self._build_operations(agent=agent, bundle=bundle, summaries=summaries)
        session.append_trace("memory_operations", {"count": len(operations)})

        contents = {
            "workspace": bundle.workspace,
            "user": bundle.user,
            "soul": bundle.soul,
        }
        changed_scopes: set[str] = set()
        for raw_operation in operations:
            result = apply_memory_operation(contents=contents, raw_operation=raw_operation)
            session.append_trace("memory_operation", result)
            scope = result.get("scope")
            if result.get("result") == "applied" and isinstance(scope, str):
                changed_scopes.add(scope)

        written_paths = self._write_changed_memories(
            store=store,
            workspace_path=session.workspace_path,
            old_contents={
                "workspace": bundle.workspace,
                "user": bundle.user,
                "soul": bundle.soul,
            },
            new_contents=contents,
            changed_scopes=changed_scopes,
        )
        latest_summary_id = str(summaries[-1]["summary_id"])
        store.update_dream_state_for_session(
            workspace_path=session.workspace_path,
            session_id=session.session_id,
            latest_summary_id=latest_summary_id,
        )
        session.append_trace(
            "memory_update",
            {
                "changed_scopes": sorted(changed_scopes),
                "written_paths": [str(path) for path in written_paths],
                "summary_ids": [record.get("summary_id") for record in summaries],
            },
        )

    async def _build_operations(
        self,
        *,
        agent: Agent,
        bundle: MemoryBundle,
        summaries: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        request = ModelRequest(
            model=agent.model,
            messages=[
                {"role": "system", "content": self.prompt_renderer.render("dream.md")},
                {
                    "role": "user",
                    "content": _dream_input(
                        session=agent.session,
                        bundle=bundle,
                        summaries=summaries,
                    ),
                },
            ],
            tools=[],
            temperature=agent.temperature,
            max_tokens=agent.max_tokens,
            metadata={"session_id": agent.session.session_id, "purpose": "dream"},
        )
        message = await call_model(
            agent.provider,
            request,
            on_trace=agent.session.append_trace,
            trace_model_events=agent.trace_model_events,
        )
        content = str(message.get("content") or "").strip()
        return parse_memory_operations(content)

    @staticmethod
    def _write_changed_memories(
        *,
        store: MemoryStore,
        workspace_path: Path,
        old_contents: dict[str, str],
        new_contents: dict[str, str],
        changed_scopes: set[str],
    ) -> list[Path]:
        written: list[Path] = []
        for scope in sorted(changed_scopes):
            if new_contents[scope] == old_contents[scope]:
                continue
            if scope == "workspace":
                written.append(store.write_workspace_memory(workspace_path, new_contents[scope]))
            elif scope == "user":
                written.append(store.write_user_memory(new_contents[scope]))
            elif scope == "soul":
                written.append(store.write_soul_memory(new_contents[scope]))
        return written


def parse_memory_operations(content: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Dream response is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Dream response must be a JSON object.")
    operations = payload.get("operations")
    if not isinstance(operations, list):
        raise ValueError("Dream response must contain an operations list.")
    return operations


def apply_memory_operation(
    *,
    contents: dict[str, str],
    raw_operation: object,
) -> dict[str, Any]:
    if not isinstance(raw_operation, dict):
        return {"result": "skipped_invalid", "reason": "operation_not_object"}

    scope = raw_operation.get("scope")
    op = raw_operation.get("op")
    section = raw_operation.get("section")
    trace = {
        "scope": scope,
        "op": op,
        "section": section,
    }
    if not isinstance(scope, str) or scope not in SECTION_ALLOWLIST:
        return {**trace, "result": "skipped_invalid", "reason": "invalid_scope"}
    if not isinstance(op, str) or op not in VALID_OPERATIONS:
        return {**trace, "result": "skipped_invalid", "reason": "invalid_op"}
    if not isinstance(section, str) or section not in SECTION_ALLOWLIST[scope]:
        return {**trace, "result": "skipped_invalid", "reason": "invalid_section"}

    content = contents[scope]
    if op == "append":
        text = raw_operation.get("text")
        if not isinstance(text, str) or not text.strip():
            return {**trace, "result": "skipped_invalid", "reason": "missing_text"}
        new_content, result = _append_to_section(content, section=section, text=text.strip())
    elif op == "update":
        old_text = raw_operation.get("old_text")
        new_text = raw_operation.get("new_text")
        if not isinstance(old_text, str) or not old_text.strip() or not isinstance(new_text, str):
            return {**trace, "result": "skipped_invalid", "reason": "missing_update_text"}
        new_content, result = _replace_in_section(
            content,
            section=section,
            old_text=old_text.strip(),
            new_text=new_text.strip(),
        )
    else:
        text = raw_operation.get("text")
        if not isinstance(text, str) or not text.strip():
            return {**trace, "result": "skipped_invalid", "reason": "missing_text"}
        new_content, result = _replace_in_section(
            content,
            section=section,
            old_text=text.strip(),
            new_text="",
        )

    if result == "applied":
        contents[scope] = new_content
    return {**trace, "result": result}


def workspace_memory_id(workspace_path: Path) -> str:
    resolved = workspace_path.expanduser().resolve()
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:10]
    return f"{sanitize_id(resolved.name)}-{digest}"


def _append_to_section(content: str, *, section: str, text: str) -> tuple[str, str]:
    lines = _split_markdown(content)
    section_range = _section_range(lines, section)
    if section_range.result == "skipped_ambiguous":
        return content, section_range.result
    if section_range.result == "skipped_not_found":
        lines = _append_section(lines, section)
        section_range = _section_range(lines, section)
    start = section_range.start
    end = section_range.end
    section_body = "\n".join(lines[start:end])
    if text in section_body:
        return content, "skipped_duplicate"
    insert_lines = text.splitlines()
    if end > start and lines[end - 1].strip():
        insert_lines = ["", *insert_lines]
    lines = [*lines[:end], *insert_lines, *lines[end:]]
    return _join_markdown(lines), "applied"


def _replace_in_section(
    content: str,
    *,
    section: str,
    old_text: str,
    new_text: str,
) -> tuple[str, str]:
    lines = _split_markdown(content)
    section_range = _section_range(lines, section)
    if section_range.result in {"skipped_not_found", "skipped_ambiguous"}:
        return content, section_range.result
    start = section_range.start
    end = section_range.end
    section_body = "\n".join(lines[start:end])
    count = section_body.count(old_text)
    if count == 0:
        return content, "skipped_not_found"
    if count > 1:
        return content, "skipped_ambiguous"
    if old_text == new_text:
        return content, "skipped_no_change"
    new_body = section_body.replace(old_text, new_text, 1)
    new_lines = new_body.splitlines()
    lines = [*lines[:start], *new_lines, *lines[end:]]
    return _join_markdown(lines), "applied"


@dataclass(frozen=True)
class SectionRange:
    start: int = 0
    end: int = 0
    result: str = "applied"


def _section_range(lines: list[str], section: str) -> SectionRange:
    heading = f"## {section}"
    indexes = [idx for idx, line in enumerate(lines) if line.strip() == heading]
    if not indexes:
        return SectionRange(result="skipped_not_found")
    if len(indexes) > 1:
        return SectionRange(result="skipped_ambiguous")
    start = indexes[0] + 1
    end = len(lines)
    for idx in range(start, len(lines)):
        if lines[idx].startswith("## "):
            end = idx
            break
    return SectionRange(start=start, end=end)


def _append_section(lines: list[str], section: str) -> list[str]:
    result = list(lines)
    if result and result[-1].strip():
        result.append("")
    result.extend([f"## {section}", ""])
    return result


def _split_markdown(content: str) -> list[str]:
    return content.rstrip("\n").splitlines()


def _join_markdown(lines: list[str]) -> str:
    return "\n".join(lines).rstrip() + "\n"


def _write_memory_file(path: Path, backup_path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copyfile(path, backup_path)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def _memory_template(name: str) -> str:
    return files("xagent").joinpath("templates", "memory", name).read_text(encoding="utf-8")


def _ensure_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def _dream_input(
    *,
    session: Session,
    bundle: MemoryBundle,
    summaries: list[dict[str, Any]],
) -> str:
    agents_md = _read_optional(session.workspace_path / "AGENTS.md")
    return "\n\n".join(
        [
            "<workspace_meta>",
            f"- Workspace id: {bundle.workspace_id}",
            f"- Workspace path: {bundle.workspace_path}",
            f"- Session id: {session.session_id}",
            "</workspace_meta>",
            "<old_workspace_memory>",
            bundle.workspace,
            "</old_workspace_memory>",
            "<old_user_memory>",
            bundle.user,
            "</old_user_memory>",
            "<old_soul_memory>",
            bundle.soul,
            "</old_soul_memory>",
            "<agents_md>",
            agents_md,
            "</agents_md>",
            "<new_summaries>",
            json.dumps(summaries, ensure_ascii=False, indent=2),
            "</new_summaries>",
        ]
    )


def _read_optional(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")[:30_000]
