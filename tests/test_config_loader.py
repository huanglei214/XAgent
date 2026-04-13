import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from xagent.config.loader import load_config, resolve_default_model, save_config
from xagent.config.paths import get_config_file
from xagent.config.schema import AppConfig, ModelConfig, default_config


class ConfigLoaderTests(unittest.TestCase):
    def test_save_and_load_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("xagent.config.paths.find_project_root", return_value=root):
                config = default_config()
                save_config(config)
                loaded = load_config()

        self.assertEqual(loaded.default_model, config.default_model)
        self.assertEqual(loaded.models[0].api_key_env, "ARK_API_KEY")
        self.assertEqual(get_config_file(root), (root.resolve() / ".xagent" / "config.toml"))

    def test_resolve_default_model(self) -> None:
        config = AppConfig(
            default_model="custom-model",
            models=[
                ModelConfig(
                    name="custom-model",
                    provider="ark",
                    base_url="https://example.com/v1",
                    api_key_env="ARK_API_KEY",
                )
            ],
        )

        resolved = resolve_default_model(config)
        self.assertEqual(resolved.name, "custom-model")

    def test_load_missing_config_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("xagent.config.paths.find_project_root", return_value=root):
                with self.assertRaises(FileNotFoundError):
                    load_config()
