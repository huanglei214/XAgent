import tempfile
import unittest
from pathlib import Path

from xagent.coding.context import load_project_rules


class ProjectRulesTests(unittest.TestCase):
    def test_load_project_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "AGENTS.md").write_text("Follow project rules", encoding="utf-8")

            nested = root / "src" / "pkg"
            nested.mkdir(parents=True)

            rules = load_project_rules(nested)

        self.assertEqual(rules, "Follow project rules")
