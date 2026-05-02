from __future__ import annotations

from importlib.resources import files

import pytest
from jinja2 import DictLoader, Environment, StrictUndefined
from jinja2.exceptions import UndefinedError

from xagent.agent.prompts import PromptRenderer


def test_prompt_renderer_loads_builtin_markdown_templates() -> None:
    renderer = PromptRenderer()

    system = renderer.render(
        "system.md",
        agent_name="XAgent",
        workspace_path="/tmp/workspace",
        session_id="cli:default",
        model="test-model",
    )
    summary = renderer.render("summary.md")
    empty_retry = renderer.render("empty_retry.md")

    assert "XAgent" in system
    assert "/tmp/workspace" in system
    assert "cli:default" in system
    assert "test-model" in system
    assert "<identity>" in system
    assert "</identity>" in system
    assert "<runtime_context>" in system
    assert "<workspace_rules>" in system
    assert "<tool_use>" in system
    assert "<communication>" in system
    assert "Summarize the current task state" in summary
    assert "<summary_goal>" in summary
    assert "<must_include>" in summary
    assert "<summary_style>" in summary
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
    prompt_dir = files("xagent").joinpath("prompts")

    assert prompt_dir.joinpath("system.md").is_file()
    assert prompt_dir.joinpath("summary.md").is_file()
    assert prompt_dir.joinpath("empty_retry.md").is_file()
