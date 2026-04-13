import unittest
from unittest.mock import patch

from typer.testing import CliRunner

from xagent.cli.main import app
from xagent.config.schema import default_config


class _FakeAgent:
    async def run(self, prompt, on_tool_use=None):
        class _ToolUse:
            name = "read_file"
            input = {"path": "README.md"}

        if on_tool_use:
            on_tool_use(_ToolUse())

        class _Message:
            role = "assistant"
            content = []

        from xagent.foundation.messages import Message, TextPart

        return Message(role="assistant", content=[TextPart(text="Hello from XAgent")])


class CliRunTests(unittest.TestCase):
    def test_run_command_streams_output(self) -> None:
        runner = CliRunner()
        with patch("xagent.cli.run.load_config", return_value=default_config()):
            with patch("xagent.cli.run.resolve_default_model", return_value=default_config().models[0]):
                with patch("xagent.cli.run.create_provider", return_value=object()):
                    with patch("xagent.cli.run.create_coding_agent", return_value=_FakeAgent()):
                        result = runner.invoke(app, ["run", "Say hello"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Hello from XAgent", result.output)
        self.assertIn("read_file", result.output)
