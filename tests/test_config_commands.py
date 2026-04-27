import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from xagent.cli.commands.config import config_app
from xagent.cli.config import add_model, default_base_url, remove_model, set_default_model_name
from xagent.cli.config import ModelConfig, default_config


class ConfigCommandTests(unittest.TestCase):
    def test_loader_add_remove_and_set_default_helpers(self) -> None:
        config = default_config()
        updated = add_model(
            config,
            ModelConfig(
                name="gpt-4.1",
                provider="openai",
                base_url=default_base_url("openai"),
                api_key="test-key",
            ),
            make_default=True,
        )
        self.assertEqual(updated.default_model, "gpt-4.1")

        updated = set_default_model_name(updated, config.models[0].name)
        self.assertEqual(updated.default_model, config.models[0].name)

        updated = remove_model(updated, "gpt-4.1")
        self.assertEqual(len(updated.models), 1)

    def test_model_add_list_remove_commands(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("xagent.agent.paths.find_project_root", return_value=root):
                from xagent.cli.config import save_config

                save_config(default_config())

                result = runner.invoke(
                    config_app,
                    [
                        "model",
                        "add",
                        "gpt-4.1",
                        "--provider",
                        "openai",
                        "--api-key",
                        "test-key",
                        "--default",
                    ],
                )
                self.assertEqual(result.exit_code, 0)

                result = runner.invoke(config_app, ["model", "list"])
                self.assertEqual(result.exit_code, 0)
                self.assertIn("gpt-4.1", result.output)
                self.assertIn("api_key=set", result.output)

                result = runner.invoke(config_app, ["model", "remove", "gpt-4.1"])
                self.assertEqual(result.exit_code, 0)
                self.assertIn("Default is now", result.output)
