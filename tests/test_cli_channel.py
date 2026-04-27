import unittest
from types import SimpleNamespace
from unittest.mock import patch

from typer.testing import CliRunner

from xagent.cli.main import app


class CliChannelTests(unittest.TestCase):
    def test_feishu_serve_invokes_adapter(self) -> None:
        runner = CliRunner()
        with patch("xagent.cli.commands.channel.load_config") as load_config, patch(
            "xagent.cli.commands.channel.FeishuConfig.from_app_config"
        ) as from_app_config, patch(
            "xagent.cli.commands.channel.FeishuChannelAdapter"
        ) as adapter_cls, patch("xagent.cli.commands.channel.build_managed_runtime_boundary") as boundary_builder:
            adapter = adapter_cls.return_value
            load_config.return_value = SimpleNamespace(log_level="WARNING", feishu=object())
            from_app_config.return_value = object()
            result = runner.invoke(app, ["channel", "feishu", "serve"])

        self.assertEqual(result.exit_code, 0)
        from_app_config.assert_called_once_with(load_config.return_value)
        boundary_builder.assert_called_once()
        adapter.serve_forever.assert_called_once()

    def test_feishu_serve_returns_non_zero_on_startup_failure(self) -> None:
        runner = CliRunner()
        with patch("xagent.cli.commands.channel.load_config") as load_config, patch(
            "xagent.cli.commands.channel.FeishuConfig.from_app_config"
        ) as from_app_config, patch(
            "xagent.cli.commands.channel.FeishuChannelAdapter"
        ) as adapter_cls, patch("xagent.cli.commands.channel.build_managed_runtime_boundary"):
            load_config.return_value = SimpleNamespace(log_level="WARNING", feishu=object())
            from_app_config.return_value = object()
            adapter_cls.return_value.serve_forever.side_effect = RuntimeError("startup failed")

            result = runner.invoke(app, ["channel", "feishu", "serve"])

        self.assertEqual(result.exit_code, 1)
        self.assertIn("startup failed", result.output)
