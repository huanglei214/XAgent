from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from xagent.agent.prompts import PromptRenderer
from xagent.agent.runner import call_model
from xagent.config import xagent_home
from xagent.providers import ModelRequest
from xagent.session import sanitize_id, utc_now

if TYPE_CHECKING:
    from xagent.agent.loop import Agent
    from xagent.session import Session


USER_MEMORY_TEMPLATE = """# User Memory

"""

SOUL_MEMORY_TEMPLATE = """# Soul

"""

WORKSPACE_MEMORY_TEMPLATE = """# Workspace Memory

## 项目定位

## 架构决策

## 当前约定

## 已完成事项

## 待处理事项

## 注意事项
"""


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
        _ensure_file(self.user_path, USER_MEMORY_TEMPLATE)
        _ensure_file(self.soul_path, SOUL_MEMORY_TEMPLATE)
        paths.path.mkdir(parents=True, exist_ok=True)
        _ensure_file(paths.memory_path, WORKSPACE_MEMORY_TEMPLATE)
        if not paths.meta_path.exists():
            paths.meta_path.write_text(
                json.dumps(
                    {
                        "workspace_id": paths.workspace_id,
                        "workspace_path": str(workspace_path.resolve()),
                        "created_at": utc_now(),
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
            "last_dream_at": utc_now(),
        }
        self.write_dream_state(paths.workspace_id, state)

    def write_workspace_memory(self, workspace_path: Path, content: str) -> Path:
        paths = self.workspace_paths(workspace_path)
        self.ensure_workspace(workspace_path)
        if paths.memory_path.exists():
            shutil.copyfile(paths.memory_path, paths.backup_path)
        paths.memory_path.write_text(content.rstrip() + "\n", encoding="utf-8")
        return paths.memory_path

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
    """把 compact summary 整理进 workspace memory。"""

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
        state = store.read_dream_state(session.workspace_path)
        session_state = state.get("sessions", {}).get(session.session_id, {})
        last_summary_id = session_state.get("last_summary_id")
        summaries = session.summary_records_after(str(last_summary_id) if last_summary_id else None)
        if not summaries:
            session.append_trace(
                "memory_update",
                {"scope": "workspace", "skipped": "no_new_summary"},
            )
            return

        new_memory = await self._build_memory(agent=agent, bundle=bundle, summaries=summaries)
        if not new_memory.strip():
            session.append_trace(
                "memory_update",
                {"scope": "workspace", "skipped": "empty_model_output"},
            )
            raise RuntimeError("Dream produced an empty memory update.")

        memory_path = store.write_workspace_memory(session.workspace_path, new_memory)
        latest_summary_id = str(summaries[-1]["summary_id"])
        store.update_dream_state_for_session(
            workspace_path=session.workspace_path,
            session_id=session.session_id,
            latest_summary_id=latest_summary_id,
        )
        session.append_trace(
            "memory_update",
            {
                "scope": "workspace",
                "memory_path": str(memory_path),
                "summary_ids": [record.get("summary_id") for record in summaries],
            },
        )

    async def _build_memory(
        self,
        *,
        agent: Agent,
        bundle: MemoryBundle,
        summaries: list[dict[str, Any]],
    ) -> str:
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
        return str(message.get("content") or "").strip()


def workspace_memory_id(workspace_path: Path) -> str:
    resolved = workspace_path.expanduser().resolve()
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:10]
    return f"{sanitize_id(resolved.name)}-{digest}"


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
            "<existing_memory>",
            bundle.workspace,
            "</existing_memory>",
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
