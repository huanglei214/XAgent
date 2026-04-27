import tempfile
import unittest
from pathlib import Path

from xagent.agent.skills import SkillsMiddleware, discover_skills
from xagent.bus.types import Message, TextPart
from xagent.bus.types import ModelRequest


class SkillsTests(unittest.IsolatedAsyncioTestCase):
    async def test_discover_skills_reads_skill_markdown_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = root / "skills" / "demo-skill"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: demo\ndescription: Demo skill\n---\n\nUse this skill.\n",
                encoding="utf-8",
            )

            skills = discover_skills([str(root / "skills")])

        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0].name, "demo")
        self.assertEqual(skills[0].description, "Demo skill")
        self.assertEqual(skills[0].body, "Use this skill.")
        self.assertEqual(skills[0].dependencies, [])

    async def test_skills_middleware_injects_skill_system_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shared_dir = root / "skills" / "shared-skill"
            shared_dir.mkdir(parents=True)
            (shared_dir / "SKILL.md").write_text(
                "---\nname: shared\ndescription: Shared skill\n---\n\nShared instructions.\n",
                encoding="utf-8",
            )
            skill_dir = root / "skills" / "demo-skill"
            skill_dir.mkdir(parents=True)
            skill_file = skill_dir / "SKILL.md"
            skill_file.write_text(
                (
                    "---\nname: demo\ndescription: Demo skill\n---\n\n"
                    "Use this skill.\n\n"
                    "Read [`../shared-skill/SKILL.md`](../shared-skill/SKILL.md) first.\n"
                ),
                encoding="utf-8",
            )

            skills = discover_skills([str(root / "skills")])
            middleware = SkillsMiddleware(skills)
            agent = type("AgentStub", (), {"skills": [], "requested_skill_name": "demo"})()
            request = ModelRequest(
                model="ep-test",
                messages=[Message(role="system", content=[TextPart(text="base prompt")])],
            )

            await middleware.before_agent_run(agent=agent, user_text="use a skill")
            updated = await middleware.before_model(agent=agent, request=request)

        self.assertIsNotNone(updated)
        self.assertTrue(agent.skills)
        injected = updated.messages[0]
        self.assertEqual(len(updated.messages), 1)
        self.assertEqual(updated.messages[0].role, "system")
        self.assertIn("<skill_system>", injected.content[0].text)
        self.assertIn("explicitly selected the skill", injected.content[0].text)
        self.assertIn("<loaded_skills>", injected.content[0].text)
        self.assertIn("Shared instructions.", injected.content[0].text)
        self.assertIn("Use this skill.", injected.content[0].text)
        self.assertIn('"name": "demo"', injected.content[0].text)
        self.assertIn("base prompt", injected.content[0].text)

    async def test_skills_middleware_auto_detects_dollar_skill_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = root / "skills" / "demo-skill"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: demo\ndescription: Demo skill\n---\n\nUse this skill.\n",
                encoding="utf-8",
            )

            skills = discover_skills([str(root / "skills")])
            middleware = SkillsMiddleware(skills)
            agent = type("AgentStub", (), {"skills": [], "requested_skill_name": None})()

            await middleware.before_agent_run(agent=agent, user_text="$demo please help")

        self.assertEqual(agent.requested_skill_name, "demo")

    async def test_skills_middleware_emits_trace_events_for_loaded_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shared_dir = root / "skills" / "shared-skill"
            shared_dir.mkdir(parents=True)
            (shared_dir / "SKILL.md").write_text(
                "---\nname: shared\ndescription: Shared skill\n---\n\nShared instructions.\n",
                encoding="utf-8",
            )
            skill_dir = root / "skills" / "demo-skill"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                (
                    "---\nname: demo\ndescription: Demo skill\n---\n\n"
                    "Use this skill.\n\n"
                    "Read [`../shared-skill/SKILL.md`](../shared-skill/SKILL.md) first.\n"
                ),
                encoding="utf-8",
            )

            events = []

            class _Recorder:
                def emit(self, event_type, payload, tags=None, parent_event_id=None):
                    events.append((event_type, payload, tags or {}))
                    return "evt"

            skills = discover_skills([str(root / "skills")])
            middleware = SkillsMiddleware(skills)
            agent = type(
                "AgentStub",
                (),
                {"skills": [], "requested_skill_name": None, "trace_recorder": _Recorder()},
            )()
            request = ModelRequest(
                model="ep-test",
                messages=[Message(role="system", content=[TextPart(text="base prompt")])],
            )

            await middleware.before_agent_run(agent=agent, user_text="$demo please help")
            await middleware.before_model(agent=agent, request=request)

        event_types = [event[0] for event in events]
        self.assertIn("skill_requested_detected", event_types)
        self.assertIn("skill_bundle_resolved", event_types)
        self.assertIn("skill_prompt_injected", event_types)

    async def test_skills_middleware_emits_runtime_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = root / "skills" / "demo-skill"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: demo\ndescription: Demo skill\n---\n\nUse this skill.\n",
                encoding="utf-8",
            )

            runtime_events = []
            skills = discover_skills([str(root / "skills")])
            middleware = SkillsMiddleware(skills)
            agent = type("AgentStub", (), {"skills": [], "requested_skill_name": None})()
            agent.runtime_event_sink = lambda event_type, payload: runtime_events.append((event_type, payload))
            request = ModelRequest(
                model="ep-test",
                messages=[Message(role="system", content=[TextPart(text="base prompt")])],
            )

            await middleware.before_agent_run(agent=agent, user_text="$demo please help")
            await middleware.before_model(agent=agent, request=request)

        event_types = [event[0] for event in runtime_events]
        self.assertIn("skill_requested_detected", event_types)
        self.assertIn("skill_bundle_resolved", event_types)
        self.assertIn("skill_prompt_injected", event_types)
