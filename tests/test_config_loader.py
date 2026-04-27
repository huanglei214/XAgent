import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from xagent.cli.config import ensure_env_file, load_project_env
from xagent.cli.config import dump_config_yaml, load_config, resolve_default_model, save_config
from xagent.cli.config import AppConfig, ModelConfig, default_config
from xagent.cli.config import ensure_config_example_file
from xagent.agent.paths import get_config_example_file, get_config_file, get_env_file


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
        self.assertEqual(loaded.models[0].api_key_env, "ARK_API_KEY")
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
                    api_key_env="ARK_API_KEY",
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

    def test_ensure_env_file_and_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("xagent.agent.paths.find_project_root", return_value=root):
                env_path = ensure_env_file()
                env_path.write_text("ARK_API_KEY=test-key\n", encoding="utf-8")
                loaded = load_project_env()

        self.assertEqual(env_path, root / ".env")
        self.assertEqual(get_env_file(root), root.resolve() / ".env")
        self.assertEqual(loaded["ARK_API_KEY"], "test-key")

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

    def test_dump_config_yaml(self) -> None:
        content = dump_config_yaml(default_config())
        self.assertIn("max_model_calls: 100", content)
        self.assertIn("models:", content)
        self.assertIn('provider: "ark"', content)
