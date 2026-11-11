import tempfile
import unittest
from pathlib import Path

from services.fs_service import Workspace
from services.tools import SPEC_BY_NAME, ToolArgError, ToolRegistry, ollama_schemas, validate_args
from storage.db import Database


class ValidationTests(unittest.TestCase):
    def test_unknown_argument_rejected(self):
        with self.assertRaises(ToolArgError):
            validate_args(SPEC_BY_NAME["read_file"], {"path": "a.txt", "shell": "rm -rf /"})

    def test_missing_required(self):
        with self.assertRaises(ToolArgError):
            validate_args(SPEC_BY_NAME["create_file"], {"path": "a.txt"})
        with self.assertRaises(ToolArgError):
            validate_args(SPEC_BY_NAME["read_file"], {"path": "   "})

    def test_enum_date_integer_boolean(self):
        spec = SPEC_BY_NAME["add_task"]
        self.assertEqual(validate_args(spec, {"title": "x", "priority": "HIGH", "due": "2026-12-31"}),
                         {"title": "x", "priority": "high", "due": "2026-12-31"})
        for bad in ({"title": "x", "priority": "urgent"}, {"title": "x", "due": "next friday"}):
            with self.assertRaises(ToolArgError):
                validate_args(spec, bad)
        self.assertEqual(validate_args(SPEC_BY_NAME["complete_task"], {"id": "#3"}), {"id": 3})
        self.assertEqual(validate_args(SPEC_BY_NAME["complete_task"], {"id": 3.0}), {"id": 3})
        with self.assertRaises(ToolArgError):
            validate_args(SPEC_BY_NAME["complete_task"], {"id": True})
        self.assertEqual(validate_args(SPEC_BY_NAME["delete_file"], {"path": "a", "recursive": "true"})["recursive"], True)

    def test_json_string_and_garbage(self):
        self.assertEqual(validate_args(SPEC_BY_NAME["read_file"], '{"path": "a.txt"}'), {"path": "a.txt"})
        with self.assertRaises(ToolArgError):
            validate_args(SPEC_BY_NAME["read_file"], "not json")
        with self.assertRaises(ToolArgError):
            validate_args(SPEC_BY_NAME["read_file"], {"__invalid__": "x"})

    def test_length_limit(self):
        with self.assertRaises(ToolArgError):
            validate_args(SPEC_BY_NAME["read_file"], {"path": "a" * 501})

    def test_schemas_are_well_formed(self):
        schemas = ollama_schemas()
        self.assertEqual(len(schemas), len(SPEC_BY_NAME))
        for s in schemas:
            fn = s["function"]
            self.assertEqual(fn["parameters"]["type"], "object")
            self.assertTrue(set(fn["parameters"]["required"]) <= set(fn["parameters"]["properties"]))
        names = {s["function"]["name"] for s in schemas}
        self.assertFalse({"shell", "run", "exec", "python"} & names)


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.ws = Workspace(self.tmp / "ws")
        self.db = Database(self.tmp / "t.db")
        self.answers: list[bool] = []
        self.prompts: list[str] = []
        self.reg = ToolRegistry(self.ws, self.db, self._confirm)

    def _confirm(self, prompt):
        self.prompts.append(prompt)
        return self.answers.pop(0) if self.answers else False

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    # ---- files
    def test_create_file_is_verified_and_reports_real_path(self):
        r = self.reg.execute("create_file", {"path": "essays/modern-politics.txt", "content": "Essay text."})
        self.assertTrue(r.ok)
        real = self.ws.root / "essays" / "modern-politics.txt"
        self.assertIn(str(real), r.message)
        self.assertEqual(real.read_text(), "Essay text.")
        self.assertEqual(r.changed, ("files",))

    def test_create_conflict_default_renames_and_says_so(self):
        self.reg.execute("create_file", {"path": "a.txt", "content": "first"})
        r = self.reg.execute("create_file", {"path": "a.txt", "content": "second"})
        self.assertTrue(r.ok and "a (2).txt" in r.message and "nothing was overwritten" in r.message)
        self.assertEqual((self.ws.root / "a.txt").read_text(), "first")
        r2 = self.reg.execute("create_file", {"path": "a.txt", "content": "x", "on_conflict": "error"})
        self.assertFalse(r2.ok)

    def test_failures_are_reported_not_faked(self):
        for args in ({"path": "../escape.txt", "content": "x"}, {"path": "run.sh", "content": "x"},
                     {"path": "bad.json", "content": "{oops"}):
            r = self.reg.execute("create_file", args)
            self.assertFalse(r.ok, args)
            self.assertNotIn("Created file", r.message)
        self.assertFalse((self.tmp / "escape.txt").exists())

    def test_write_file_needs_confirmation(self):
        self.reg.execute("create_file", {"path": "n.md", "content": "old"})
        r = self.reg.execute("write_file", {"path": "n.md", "content": "new"})
        self.assertFalse(r.ok)
        self.assertEqual((self.ws.root / "n.md").read_text(), "old")
        self.answers = [True]
        r = self.reg.execute("write_file", {"path": "n.md", "content": "new"})
        self.assertTrue(r.ok)
        self.assertEqual((self.ws.root / "n.md").read_text(), "new")
        self.assertIn("Replace", self.prompts[-1])

    def test_write_file_on_missing_file(self):
        self.answers = [True]
        self.assertFalse(self.reg.execute("write_file", {"path": "nope.txt", "content": "x"}).ok)

    def test_delete_requires_confirmation_and_recursive_flag(self):
        self.reg.execute("create_file", {"path": "d/x.txt", "content": "x"})
        self.assertFalse(self.reg.execute("delete_file", {"path": "d"}).ok)  # non-empty, no recursive flag
        self.assertTrue((self.ws.root / "d").exists())
        self.assertFalse(self.reg.execute("delete_file", {"path": "d", "recursive": True}).ok)  # user said no
        self.assertTrue((self.ws.root / "d" / "x.txt").exists())
        self.answers = [True]
        r = self.reg.execute("delete_file", {"path": "d", "recursive": True})
        self.assertTrue(r.ok)
        self.assertFalse((self.ws.root / "d").exists())

    def test_move_rename_list_read_search_append(self):
        self.reg.execute("create_file", {"path": "a.txt", "content": "hello world"})
        r = self.reg.execute("move_file", {"source": "a.txt", "destination_folder": "archive/2026"})
        self.assertTrue(r.ok)
        self.assertTrue((self.ws.root / "archive" / "2026" / "a.txt").exists())
        self.assertTrue(self.reg.execute("rename_file", {"path": "archive/2026/a.txt", "new_name": "b.txt"}).ok)
        self.assertIn("hello world", self.reg.execute("read_file", {"path": "archive/2026/b.txt"}).message)
        self.assertIn("2026/", self.reg.execute("list_files", {"path": "archive"}).message)
        self.assertIn("b.txt", self.reg.execute("search_files", {"query": "b.t"}).message)
        self.assertIn("b.txt:1", self.reg.execute("search_files", {"query": "WORLD", "in_content": True}).message)
        self.assertTrue(self.reg.execute("append_file", {"path": "archive/2026/b.txt", "content": "more"}).ok)
        self.assertEqual((self.ws.root / "archive/2026/b.txt").read_text(), "hello world\nmore")

    def test_traversal_in_every_path_tool(self):
        for name, args in [("read_file", {"path": "../x"}), ("list_files", {"path": "/etc"}),
                           ("move_file", {"source": "../x", "destination_folder": "d"}),
                           ("make_folder", {"path": "../evil"}), ("delete_file", {"path": "../x"})]:
            self.assertFalse(self.reg.execute(name, args).ok, name)

    def test_unknown_tool_and_bad_args_do_not_crash(self):
        self.assertFalse(self.reg.execute("shell", {"cmd": "ls"}).ok)
        self.assertFalse(self.reg.execute("read_file", {"path": 5, "extra": 1}).ok)
        self.assertFalse(self.reg.execute("read_file", "garbage").ok)

    # ---- tasks and notes
    def test_task_tools(self):
        r = self.reg.execute("add_task", {"title": "Hand in essay", "due": "2020-01-01", "priority": "high", "category": "School"})
        self.assertTrue(r.ok and "#1" in r.message)
        self.assertIn("OVERDUE", self.reg.execute("list_tasks", {"status": "overdue"}).message)
        self.assertTrue(self.reg.execute("update_task", {"id": 1, "due": "none", "category": "Uni"}).ok)
        self.assertIsNone(self.db.get_task(1)["due_date"])
        self.assertTrue(self.reg.execute("complete_task", {"id": 1}).ok)
        self.assertEqual(self.db.get_task(1)["done"], 1)
        self.assertIn("[x]", self.reg.execute("list_tasks", {"status": "completed"}).message)
        self.assertFalse(self.reg.execute("complete_task", {"id": 99}).ok)
        self.assertFalse(self.reg.execute("delete_task", {"id": 1}).ok)  # declined
        self.assertIsNotNone(self.db.get_task(1))
        self.answers = [True]
        self.assertTrue(self.reg.execute("delete_task", {"id": 1}).ok)
        self.assertIsNone(self.db.get_task(1))

    def test_note_tools(self):
        self.reg.execute("add_task", {"title": "Project"})
        r = self.reg.execute("create_note", {"title": "Plan", "body": "step one", "project": "Xtrem", "task_id": 1})
        self.assertTrue(r.ok)
        self.assertTrue(self.reg.execute("update_note", {"id": 1, "append_text": "step two"}).ok)
        self.assertEqual(self.db.get_note(1)["body"], "step one\nstep two")
        self.assertIn("Plan", self.reg.execute("search_notes", {"query": "step"}).message)
        self.assertIn("step two", self.reg.execute("read_note", {"id": 1}).message)
        self.assertFalse(self.reg.execute("delete_note", {"id": 1}).ok)
        self.assertIsNotNone(self.db.get_note(1))
        self.answers = [True]
        self.assertTrue(self.reg.execute("delete_note", {"id": 1}).ok)
        self.assertFalse(self.reg.execute("create_note", {"title": "x", "task_id": 77}).ok)


if __name__ == "__main__":
    unittest.main()
