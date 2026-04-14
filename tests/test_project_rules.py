import tempfile
import unittest
from pathlib import Path

from xagent.coding.agents.coding_agent import create_coding_agent
from xagent.coding.context import load_project_rules
from xagent.coding.middleware.project_rules import ProjectRulesMiddleware
from xagent.foundation.messages import Message, TextPart
from xagent.foundation.models import ModelRequest


class ProjectRulesTests(unittest.TestCase):
    def test_load_project_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "AGENTS.md").write_text("Follow project rules", encoding="utf-8")

            nested = root / "src" / "pkg"
            nested.mkdir(parents=True)

            rules = load_project_rules(nested)

        self.assertEqual(
            rules,
            '<agents_scope path="./AGENTS.md">\nFollow project rules\n</agents_scope>',
        )

    def test_load_project_rules_merges_scoped_agents_in_root_to_leaf_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "AGENTS.md").write_text("Root rules", encoding="utf-8")

            package_dir = root / "src"
            package_dir.mkdir()
            (package_dir / "AGENTS.md").write_text("Src rules", encoding="utf-8")

            nested = package_dir / "pkg"
            nested.mkdir(parents=True)
            (nested / "AGENTS.md").write_text("Pkg rules", encoding="utf-8")

            rules = load_project_rules(nested)

        self.assertEqual(
            rules,
            (
                '<agents_scope path="./AGENTS.md">\n'
                "Root rules\n"
                "</agents_scope>\n\n"
                '<agents_scope path="src/AGENTS.md">\n'
                "Src rules\n"
                "</agents_scope>\n\n"
                '<agents_scope path="src/pkg/AGENTS.md">\n'
                "Pkg rules\n"
                "</agents_scope>"
            ),
        )

    def test_create_coding_agent_injects_project_rules_as_user_context_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "AGENTS.md").write_text("Root rules", encoding="utf-8")

            agent = create_coding_agent(
                provider=object(),
                model="ep-test",
                cwd=str(root),
            )

        self.assertNotIn("Root rules", agent.system_prompt)
        self.assertTrue(agent.context_messages)
        self.assertEqual(agent.context_messages[0].role, "user")
        self.assertIn("Root rules", agent.context_messages[0].content[0].text)
        self.assertIn('<agent name="XAgent" role="coding_agent"', agent.system_prompt)
        self.assertIn(f'<working_directory dir="{root.resolve().as_posix()}/" />', agent.system_prompt)
        self.assertIn("<tool_usage>", agent.system_prompt)
        self.assertIn("<editing_rules>", agent.system_prompt)
        self.assertIn("<notes>", agent.system_prompt)
        self.assertTrue(any(type(middleware).__name__ == "ProjectRulesMiddleware" for middleware in agent.middlewares))

    def test_create_coding_agent_includes_ask_user_question_tool_when_callback_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()

            agent = create_coding_agent(
                provider=object(),
                model="ep-test",
                cwd=str(root),
                ask_user_question=lambda params: {"answers": []},
            )

        self.assertIn("ask_user_question", [tool.name for tool in agent.tools])

    def test_project_rules_middleware_emits_trace_and_runtime_events(self) -> None:
        middleware = ProjectRulesMiddleware(
            '<agents_scope path="./AGENTS.md">\nRoot rules\n</agents_scope>\n\n'
            '<agents_scope path="src/AGENTS.md">\nSrc rules\n</agents_scope>'
        )
        trace_events = []
        runtime_events = []

        class _Recorder:
            def emit(self, event_type, payload, tags=None, parent_event_id=None):
                trace_events.append((event_type, payload, tags or {}))
                return "evt"

        agent = type("AgentStub", (), {})()
        agent.trace_recorder = _Recorder()
        agent.runtime_event_sink = lambda event_type, payload: runtime_events.append((event_type, payload))
        agent.context_messages = [Message(role="user", content=[TextPart(text="rules")])]

        request = ModelRequest(
            model="ep-test",
            messages=[Message(role="system", content=[TextPart(text="base prompt")])],
        )

        import asyncio

        asyncio.run(middleware.before_agent_run(agent=agent, user_text="hello"))
        asyncio.run(middleware.before_model(agent=agent, request=request))

        trace_event_types = [event[0] for event in trace_events]
        runtime_event_types = [event[0] for event in runtime_events]
        self.assertIn("project_rules_loaded", trace_event_types)
        self.assertIn("project_rules_context_injected", trace_event_types)
        self.assertIn("project_rules_loaded", runtime_event_types)
        self.assertIn("project_rules_context_injected", runtime_event_types)
