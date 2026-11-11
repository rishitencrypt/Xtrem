import sqlite3
import tempfile
import unittest
from pathlib import Path

from storage.db import DEFAULTS, Database, StorageError


class DbTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "t.db"
        self.db = Database(self.path)

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def test_settings_defaults_and_persist(self):
        self.assertEqual(self.db.all_settings()["model"], DEFAULTS["model"])
        self.db.set_setting("model", "other:1b")
        self.db.close()
        self.db = Database(self.path)
        self.assertEqual(self.db.all_settings()["model"], "other:1b")

    def test_notes_crud_search_projects_and_links(self):
        tid = self.db.add_task("Write report")
        nid = self.db.save_note(None, "Groceries", "milk and eggs", project="Home", task_id=tid)
        self.db.save_note(None, "Ideas", "build Xtrem", project="Work")
        self.assertEqual([n["title"] for n in self.db.list_notes("milk")], ["Groceries"])
        self.assertEqual([n["title"] for n in self.db.list_notes(project="work")], ["Ideas"])
        self.assertEqual(self.db.note_projects(), ["Home", "Work"])
        self.assertEqual(self.db.get_note(nid)["task_id"], tid)
        self.db.delete_task(tid)  # unlinks, keeps the note
        self.assertIsNone(self.db.get_note(nid)["task_id"])
        self.db.delete_note(nid)
        self.assertIsNone(self.db.get_note(nid))
        with self.assertRaises(StorageError):
            self.db.save_note(None, "x", "y", task_id=999)

    def test_note_search_escapes_wildcards(self):
        self.db.save_note(None, "a", "100% done")
        self.db.save_note(None, "b", "nothing")
        self.assertEqual(len(self.db.list_notes("%")), 1)

    def test_tasks_full_lifecycle(self):
        t1 = self.db.add_task("late", due="2026-01-01", priority="high", category="Work")
        t2 = self.db.add_task("soon", due="2099-01-01")
        t3 = self.db.add_task("someday", priority="low")
        today = "2026-10-20"
        self.assertEqual([t["title"] for t in self.db.list_tasks("overdue", today=today)], ["late"])
        self.assertTrue(self.db.list_tasks(today=today)[0]["overdue"])
        self.assertEqual([t["title"] for t in self.db.list_tasks("pending", today=today)], ["late", "soon", "someday"])
        self.db.update_task(t1, done=True)
        self.assertEqual([t["title"] for t in self.db.list_tasks("completed")], ["late"])
        self.assertEqual(self.db.list_tasks("overdue", today=today), [])
        self.db.update_task(t2, title="soon!", priority="high", category="Home", due="2099-02-02", details="d")
        row = self.db.get_task(t2)
        self.assertEqual((row["title"], row["priority"], row["category"], row["due_date"]),
                         ("soon!", "high", "Home", "2099-02-02"))
        self.db.update_task(t2, clear_due=True)
        self.assertIsNone(self.db.get_task(t2)["due_date"])
        self.assertEqual(self.db.task_categories(), ["Home", "Work"])
        self.db.delete_task(t3)
        self.assertIsNone(self.db.get_task(t3))

    def test_task_validation(self):
        for kwargs in ({"title": "  "}, {"title": "x", "priority": "urgent"}, {"title": "x", "due": "tomorrow"}):
            with self.assertRaises(StorageError):
                self.db.add_task(**kwargs)
        with self.assertRaises(StorageError):
            self.db.update_task(12345, done=True)

    def test_tasks_survive_restart(self):
        self.db.add_task("keep me", due="2030-05-05")
        self.db.close()
        self.db = Database(self.path)
        self.assertEqual(self.db.list_tasks()[0]["due_date"], "2030-05-05")

    def test_messages_clear(self):
        self.db.add_message("user", "hi")
        self.db.add_message("assistant", "hello")
        self.assertEqual([m["role"] for m in self.db.load_messages()], ["user", "assistant"])
        self.db.clear_messages()
        self.assertEqual(self.db.load_messages(), [])


class MigrationTests(unittest.TestCase):
    def test_v1_database_is_upgraded_without_losing_data(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "old.db"
            con = sqlite3.connect(path)
            con.executescript("""
                CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE notes (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
                    body TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL);
                CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
                    done INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL);
                CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL,
                    content TEXT NOT NULL, created_at REAL NOT NULL);
                INSERT INTO notes(title, body, updated_at) VALUES('old note', 'body', 1);
                INSERT INTO tasks(title, done, created_at) VALUES('old task', 1, 1);
                INSERT INTO settings VALUES('model', 'qwen3:4b');
                PRAGMA user_version = 1;
            """)
            con.commit(); con.close()
            db = Database(path)
            self.assertEqual(db.list_notes()[0]["title"], "old note")
            task = db.list_tasks()[0]
            self.assertEqual((task["title"], task["done"], task["priority"]), ("old task", 1, "normal"))
            self.assertTrue((Path(d) / "old.db.bak-v1").exists())
            self.assertEqual(db.all_settings()["model"], "qwen3:4b")
            db.close()
            Database(path).close()  # re-open is a no-op


if __name__ == "__main__":
    unittest.main()
