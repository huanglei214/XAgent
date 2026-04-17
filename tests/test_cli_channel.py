import unittest
from unittest.mock import patch

from typer.testing import CliRunner

from xagent.cli.main import app


class CliChannelTests(unittest.TestCase):
    def test_feishu_serve_invokes_adapter(self) -> None:
        runner = CliRunner()
        with patch("xagent.cli.commands.channel.FeishuConfig.from_env") as from_env, patch(
            "xagent.cli.commands.channel.FeishuChannelAdapter"
        ) as adapter_cls, patch("xagent.cli.commands.channel.build_managed_runtime_boundary") as boundary_builder:
            adapter = adapter_cls.return_value
            from_env.return_value = object()
            result = runner.invoke(app, ["channel", "feishu", "serve"])

        self.assertEqual(result.exit_code, 0)
        from_env.assert_called_once()
        boundary_builder.assert_called_once()
        adapter.serve_forever.assert_called_once()

    def test_feishu_serve_returns_non_zero_on_startup_failure(self) -> None:
        runner = CliRunner()
        with patch("xagent.cli.commands.channel.FeishuConfig.from_env") as from_env, patch(
            "xagent.cli.commands.channel.FeishuChannelAdapter"
        ) as adapter_cls, patch("xagent.cli.commands.channel.build_managed_runtime_boundary"):
            from_env.return_value = object()
            adapter_cls.return_value.serve_forever.side_effect = RuntimeError("startup failed")

            result = runner.invoke(app, ["channel", "feishu", "serve"])

        self.assertEqual(result.exit_code, 1)
        self.assertIn("startup failed", result.output)
