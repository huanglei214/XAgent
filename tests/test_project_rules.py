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
