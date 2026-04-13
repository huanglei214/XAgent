import tempfile
import unittest
from pathlib import Path

from xagent.coding.tools import (
    apply_patch_tool,
    bash_tool,
    file_info_tool,
    glob_search_tool,
    grep_search_tool,
    list_files_tool,
    mkdir_tool,
    move_path_tool,
    read_file_tool,
    str_replace_tool,
    write_file_tool,
)
from xagent.foundation.tools import ToolContext


class ToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        (self.root / "src").mkdir()
        (self.root / "src" / "main.py").write_text("print('hello')\nvalue = 42\n", encoding="utf-8")
        (self.root / "README.md").write_text("XAgent\nhello world\n", encoding="utf-8")
        self.ctx = ToolContext(cwd=str(self.root))

    async def asyncTearDown(self) -> None:
        self.tempdir.cleanup()

    async def test_list_files(self) -> None:
        result = await list_files_tool.invoke({"path": ".", "recursive": False}, self.ctx)
        self.assertIn("README.md", result.content)
        self.assertIn("src/", result.content)

    async def test_read_file(self) -> None:
        result = await read_file_tool.invoke({"path": "src/main.py", "start_line": 2}, self.ctx)
        self.assertIn("2:", result.content)
        self.assertIn("value = 42", result.content)

    async def test_glob_search(self) -> None:
        result = await glob_search_tool.invoke({"path": ".", "pattern": "**/*.py"}, self.ctx)
        self.assertIn("src/main.py", result.content)

    async def test_grep_search(self) -> None:
        result = await grep_search_tool.invoke({"path": ".", "pattern": "hello"}, self.ctx)
        self.assertIn("README.md:2: hello world", result.content)

    async def test_write_file(self) -> None:
        result = await write_file_tool.invoke({"path": "notes.txt", "content": "hello"}, self.ctx)
        self.assertEqual(result.content, "Wrote notes.txt")
        self.assertEqual((self.root / "notes.txt").read_text(encoding="utf-8"), "hello")

    async def test_apply_patch(self) -> None:
        result = await apply_patch_tool.invoke(
            {"path": "src/main.py", "old_text": "value = 42", "new_text": "value = 43"},
            self.ctx,
        )
        self.assertIn("Applied patch to src/main.py", result.content)
        self.assertIn("value = 43", (self.root / "src" / "main.py").read_text(encoding="utf-8"))

    async def test_bash(self) -> None:
        result = await bash_tool.invoke({"command": "printf 'ok'"}, self.ctx)
        self.assertEqual(result.content, "ok")

    async def test_str_replace(self) -> None:
        result = await str_replace_tool.invoke(
            {"path": "README.md", "old_text": "hello world", "new_text": "hello xagent"},
            self.ctx,
        )
        self.assertIn("Replaced text in README.md", result.content)
        self.assertIn("hello xagent", (self.root / "README.md").read_text(encoding="utf-8"))

    async def test_mkdir(self) -> None:
        result = await mkdir_tool.invoke({"path": "nested/dir"}, self.ctx)
        self.assertEqual(result.content, "Created directory nested/dir")
        self.assertTrue((self.root / "nested" / "dir").is_dir())

    async def test_move_path(self) -> None:
        result = await move_path_tool.invoke({"source": "README.md", "destination": "docs/README.md"}, self.ctx)
        self.assertEqual(result.content, "Moved README.md to docs/README.md")
        self.assertTrue((self.root / "docs" / "README.md").exists())
        self.assertFalse((self.root / "README.md").exists())

    async def test_file_info(self) -> None:
        result = await file_info_tool.invoke({"path": "src/main.py"}, self.ctx)
        self.assertIn("type: file", result.content)
        self.assertIn("size_bytes:", result.content)
