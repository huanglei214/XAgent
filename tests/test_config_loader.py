import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from xagent.cli.config import dump_config_yaml, load_config, resolve_default_model, save_config
from xagent.cli.config import AppConfig, FeishuAppConfig, ModelConfig, default_config
from xagent.cli.config import ensure_config_example_file
from xagent.agent.paths import get_config_example_file, get_config_file


class ConfigLoaderTests(unittest.TestCase):
    def test_save_and_load_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("xagent.agent.paths.find_project_root", return_value=root):
                config = default_config()
                save_config(config)
                loaded = load_config()

        self.assertEqual(loaded.default_model, config.default_model)
        self.assertEqual(loaded.max_model_calls, 100)
        self.assertEqual(loaded.models[0].api_key, "")
        self.assertEqual(loaded.feishu.api_base_url, "https://open.feishu.cn")
        self.assertEqual(get_config_file(root), (root.resolve() / ".xagent" / "config.yaml"))

    def test_resolve_default_model(self) -> None:
        config = AppConfig(
            default_model="custom-model",
            max_model_calls=42,
            models=[
                ModelConfig(
                    name="custom-model",
                    provider="ark",
                    base_url="https://example.com/v1",
                    api_key="test-key",
                )
            ],
        )

        resolved = resolve_default_model(config)
        self.assertEqual(resolved.name, "custom-model")

    def test_load_missing_config_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("xagent.agent.paths.find_project_root", return_value=root):
                with self.assertRaises(FileNotFoundError):
                    load_config()

    def test_feishu_config_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("xagent.agent.paths.find_project_root", return_value=root):
                config = default_config()
                config.feishu = FeishuAppConfig(
                    app_id="cli_app",
                    app_secret="secret",
                    bot_open_id="bot",
                    group_mode="all_text",
                    allow_all=True,
                    allowed_user_ids=["ou_1", "ou_2"],
                    allowed_chat_ids=["oc_1"],
                )
                save_config(config)
                loaded = load_config()

        self.assertEqual(loaded.feishu.app_id, "cli_app")
        self.assertEqual(loaded.feishu.app_secret, "secret")
        self.assertEqual(loaded.feishu.group_mode, "all_text")
        self.assertTrue(loaded.feishu.allow_all)
        self.assertEqual(loaded.feishu.allowed_user_ids, ["ou_1", "ou_2"])
        self.assertEqual(loaded.feishu.allowed_chat_ids, ["oc_1"])

    def test_config_example_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("xagent.agent.paths.find_project_root", return_value=root):
                example_path = ensure_config_example_file()
                content = example_path.read_text(encoding="utf-8")

        self.assertEqual(example_path, root / "config.example.yaml")
        self.assertEqual(get_config_example_file(root), root.resolve() / "config.example.yaml")
        self.assertIn('default_model: "ep-your-ark-endpoint-id"', content)
        self.assertIn("max_model_calls: 100", content)
        self.assertIn("api_key:", content)
        self.assertIn("feishu:", content)

    def test_dump_config_yaml(self) -> None:
        content = dump_config_yaml(default_config())
        self.assertIn("max_model_calls: 100", content)
        self.assertIn("models:", content)
        self.assertIn('provider: "ark"', content)
        self.assertIn('api_key: ""', content)
        self.assertIn("feishu:", content)
