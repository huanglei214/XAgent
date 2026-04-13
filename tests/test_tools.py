import tempfile
import unittest
from pathlib import Path

from xagent.coding.tools import glob_search_tool, grep_search_tool, list_files_tool, read_file_tool
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
