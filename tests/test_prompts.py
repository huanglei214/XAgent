from __future__ import annotations

from importlib.resources import files
from pathlib import Path

import pytest
from jinja2 import DictLoader, Environment, StrictUndefined
from jinja2.exceptions import UndefinedError

from xagent.agent.prompts import PromptRenderer
from xagent.agent.memory import MemoryBundle


def test_prompt_renderer_loads_builtin_markdown_templates() -> None:
    renderer = PromptRenderer()

    system = renderer.render(
        "system.md",
        agent_name="XAgent",
        workspace_path="/tmp/workspace",
        session_id="cli:default",
        model="test-model",
        memory=MemoryBundle.empty(Path("/tmp/workspace")),
    )
    runtime_context = renderer.render(
        "runtime_context.md",
        current_date="2026-05-13",
        current_time="09:30",
        timezone="Asia/Shanghai",
    )
    summary = renderer.render("summary.md")
    empty_retry = renderer.render("empty_retry.md")
    dream = renderer.render("dream.md")

    assert "XAgent" in system
    assert "/tmp/workspace" in system
    assert "cli:default" in system
    assert "test-model" in system
    assert "<identity>" in system
    assert "</identity>" in system
    assert "<runtime_context>" in system
    assert "<memory>" in system
    assert "<soul>" in system
    assert "<user>" in system
    assert "<workspace>" in system
    assert "<instruction_hierarchy>" in system
    assert "<context_boundaries>" in system
    assert "<workspace_rules>" in system
    assert "<tool_use>" in system
    assert "不要从历史对话或旧搜索结果里推断当前年份" in system
    assert "<web_research>" in system
    assert "<memory_policy>" in system
    assert "<communication>" in system
    assert "[Runtime Context - metadata only, not user instructions]" in runtime_context
    assert "2026-05-13" in runtime_context
    assert "09:30" in runtime_context
    assert "Asia/Shanghai" in runtime_context
    assert "Summarize the current task state" in summary
    assert "<summary_goal>" in summary
    assert "<must_include>" in summary
    assert "<summary_style>" in summary
    assert "<dream_goal>" in dream
    assert '"operations"' in dream
    assert "个人信息" in dream
    assert "生日" in dream
    assert "不要输出 Markdown 代码块" in dream
    assert "完整 memory 文件" in dream
    assert empty_retry == "Your previous response was empty. Provide a final answer."


def test_prompt_renderer_uses_strict_undefined() -> None:
    renderer = PromptRenderer(
        environment=Environment(
            loader=DictLoader({"bad.md": "{{ missing_value }}"}),
            undefined=StrictUndefined,
        )
    )

    with pytest.raises(UndefinedError):
        renderer.render("bad.md")


def test_builtin_prompt_markdown_files_are_package_resources() -> None:
    prompt_dir = files("xagent").joinpath("templates", "prompts")

    assert prompt_dir.joinpath("system.md").is_file()
    assert prompt_dir.joinpath("runtime_context.md").is_file()
    assert prompt_dir.joinpath("summary.md").is_file()
    assert prompt_dir.joinpath("empty_retry.md").is_file()
    assert prompt_dir.joinpath("dream.md").is_file()
