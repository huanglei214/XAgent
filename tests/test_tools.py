import tempfile
import unittest
from pathlib import Path

from xagent.agent.tools.workspace import (
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
        (self.root / ".git").mkdir()
        (self.root / ".git" / "ignored.txt").write_text("secret hello\n", encoding="utf-8")
        (self.root / ".venv").mkdir()
        (self.root / ".venv" / "ignored.py").write_text("print('ignored')\n", encoding="utf-8")
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
        self.assertNotIn(".venv/ignored.py", result.content)

    async def test_grep_search(self) -> None:
        result = await grep_search_tool.invoke({"path": ".", "pattern": "hello"}, self.ctx)
        self.assertIn("README.md:2: hello world", result.content)
        self.assertNotIn(".git/ignored.txt", result.content)

    async def test_list_files_recursive_ignores_hidden_runtime_directories(self) -> None:
        result = await list_files_tool.invoke({"path": ".", "recursive": True}, self.ctx)
        self.assertIn("src/main.py", result.content)
        self.assertNotIn(".git/ignored.txt", result.content)
        self.assertNotIn(".venv/ignored.py", result.content)

    async def test_write_file(self) -> None:
        result = await write_file_tool.invoke({"path": "notes.txt", "content": "hello"}, self.ctx)
        self.assertEqual(result.content, "Wrote notes.txt")
        self.assertEqual((self.root / "notes.txt").read_text(encoding="utf-8"), "hello")

    async def test_apply_patch(self) -> None:
        patch = """--- a/src/main.py
+++ b/src/main.py
@@ -1,2 +1,2 @@
-print('hello')
+print('hello world')
 value = 42
"""
        result = await apply_patch_tool.invoke(
            {"patch": patch},
            self.ctx,
        )
        self.assertIn("Applied patch to 1 file", result.content)
        self.assertIn("hello world", (self.root / "src" / "main.py").read_text(encoding="utf-8"))

    async def test_apply_patch_rejects_hunk_context_mismatch(self) -> None:
        patch = """--- a/src/main.py
+++ b/src/main.py
@@ -1,2 +1,2 @@
-print('nope')
+print('hello world')
 value = 42
"""
        result = await apply_patch_tool.invoke({"patch": patch}, self.ctx)
        self.assertTrue(result.is_error)
        self.assertEqual(result.code, "PATCH_MISMATCH")

    async def test_apply_patch_supports_multiple_files(self) -> None:
        patch = """--- a/src/main.py
+++ b/src/main.py
@@ -1,2 +1,2 @@
-print('hello')
+print('patched')
 value = 42
--- a/README.md
+++ b/README.md
@@ -1,2 +1,2 @@
 XAgent
-hello world
+hello patch
"""
        result = await apply_patch_tool.invoke({"patch": patch}, self.ctx)
        self.assertFalse(result.is_error)
        self.assertEqual(result.data["file_count"], 2)
        self.assertIn("patched", (self.root / "src" / "main.py").read_text(encoding="utf-8"))
        self.assertIn("hello patch", (self.root / "README.md").read_text(encoding="utf-8"))

    async def test_apply_patch_supports_rename_patch(self) -> None:
        patch = """diff --git a/src/main.py b/src/app.py
similarity index 50%
rename from src/main.py
rename to src/app.py
--- a/src/main.py
+++ b/src/app.py
@@ -1,2 +1,2 @@
-print('hello')
+print('renamed')
 value = 42
"""
        result = await apply_patch_tool.invoke({"patch": patch}, self.ctx)
        self.assertFalse(result.is_error)
        self.assertFalse((self.root / "src" / "main.py").exists())
        self.assertEqual(
            (self.root / "src" / "app.py").read_text(encoding="utf-8"),
            "print('renamed')\nvalue = 42\n",
        )

    async def test_apply_patch_rename_can_replace_existing_target(self) -> None:
        (self.root / "src" / "app.py").write_text("stale target\n", encoding="utf-8")
        patch = """diff --git a/src/main.py b/src/app.py
similarity index 50%
rename from src/main.py
rename to src/app.py
--- a/src/main.py
+++ b/src/app.py
@@ -1,2 +1,2 @@
-print('hello')
+print('replaced target')
 value = 42
"""
        result = await apply_patch_tool.invoke({"patch": patch}, self.ctx)
        self.assertFalse(result.is_error)
        self.assertFalse((self.root / "src" / "main.py").exists())
        self.assertEqual(
            (self.root / "src" / "app.py").read_text(encoding="utf-8"),
            "print('replaced target')\nvalue = 42\n",
        )

    async def test_apply_patch_supports_no_newline_marker(self) -> None:
        (self.root / "no_newline.txt").write_text("alpha", encoding="utf-8")
        patch = """--- a/no_newline.txt
+++ b/no_newline.txt
@@ -1 +1 @@
-alpha
\\ No newline at end of file
+beta
\\ No newline at end of file
"""
        result = await apply_patch_tool.invoke({"patch": patch}, self.ctx)
        self.assertFalse(result.is_error)
        self.assertEqual((self.root / "no_newline.txt").read_text(encoding="utf-8"), "beta")

    async def test_apply_patch_supports_creating_file_without_trailing_newline(self) -> None:
        patch = """--- /dev/null
+++ b/created_no_newline.txt
@@ -0,0 +1 @@
+created
\\ No newline at end of file
"""
        result = await apply_patch_tool.invoke({"patch": patch}, self.ctx)
        self.assertFalse(result.is_error)
        self.assertEqual((self.root / "created_no_newline.txt").read_text(encoding="utf-8"), "created")

    async def test_apply_patch_supports_deleting_file_without_trailing_newline(self) -> None:
        (self.root / "delete_no_newline.txt").write_text("gone", encoding="utf-8")
        patch = """--- a/delete_no_newline.txt
+++ /dev/null
@@ -1 +0,0 @@
-gone
\\ No newline at end of file
"""
        result = await apply_patch_tool.invoke({"patch": patch}, self.ctx)
        self.assertFalse(result.is_error)
        self.assertFalse((self.root / "delete_no_newline.txt").exists())

    async def test_bash(self) -> None:
        result = await bash_tool.invoke({"command": "printf 'ok'"}, self.ctx)
        self.assertEqual(result.content, "ok")

    async def test_str_replace(self) -> None:
        (self.root / "README.md").write_text("hello world\nhello world\n", encoding="utf-8")
        result = await str_replace_tool.invoke(
            {"path": "README.md", "old_text": "hello world", "new_text": "hello xagent", "count": 1},
            self.ctx,
        )
        self.assertIn("Replaced text in README.md", result.content)
        self.assertEqual(
            (self.root / "README.md").read_text(encoding="utf-8"),
            "hello xagent\nhello world\n",
        )

    async def test_str_replace_reports_all_occurrences_and_honors_count(self) -> None:
        (self.root / "README.md").write_text("hello world\nhello world\nhello world\n", encoding="utf-8")
        result = await str_replace_tool.invoke(
            {"path": "README.md", "old_text": "hello world", "new_text": "hello xagent", "count": 2},
            self.ctx,
        )
        self.assertFalse(result.is_error)
        self.assertEqual(result.data["occurrences_found"], 3)
        self.assertEqual(result.data["replacements"], 2)
        self.assertEqual(
            (self.root / "README.md").read_text(encoding="utf-8"),
            "hello xagent\nhello xagent\nhello world\n",
        )

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

    async def test_read_file_rejects_end_line_before_start_line(self) -> None:
        result = await read_file_tool.invoke({"path": "src/main.py", "start_line": 2, "end_line": 1}, self.ctx)
        self.assertTrue(result.is_error)
        self.assertEqual(result.code, "INVALID_LINE_RANGE")
