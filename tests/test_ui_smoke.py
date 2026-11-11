"""GUI smoke tests. They run headless (offscreen) and are skipped if PySide6 isn't installed."""
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class UiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from storage.db import Database
        from ui.context import AppContext
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.db = Database(tmp / "x.db")
        self.db.set_setting("workspace", str(tmp / "ws"))
        self.ctx = AppContext(self.db)

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def test_main_window_and_every_page_open_and_resize(self):
        from ui.main_window import MainWindow
        win = MainWindow(self.ctx)
        win.show()
        for i in range(6):
            win.show_page(i)
            self.app.processEvents()
        win.resize(960, 640)
        self.app.processEvents()
        win.resize(1500, 900)
        self.app.processEvents()
        win.close()

    def test_long_messages_are_fully_sized_not_clipped(self):
        from ui.chat_view import Bubble
        bubble = Bubble("assistant", "word " * 3000)
        bubble.resize(500, 100)
        bubble.show()
        self.app.processEvents()
        self.assertGreater(bubble.body.sizeHint().height(), 400)
        narrow = bubble.body.sizeHint().height()
        bubble.resize(900, 100)
        self.app.processEvents()
        self.assertLess(bubble.body.sizeHint().height(), narrow)  # re-wraps on resize

    def test_tasks_view_adds_task_with_due_date(self):
        from ui.tasks_view import TasksView
        view = TasksView(self.ctx)
        view.entry.setText("Write essay")
        view.due.setText("2030-01-02")
        view._add()
        self.assertEqual(self.db.list_tasks()[0]["due_date"], "2030-01-02")
        self.assertEqual(view.list.count(), 1)

    def test_notes_view_saves(self):
        from ui.notes_view import NotesView
        view = NotesView(self.ctx)
        view.new_note()
        view.title.setText("Plan")
        view.body.setPlainText("text")
        view._save()
        self.assertEqual(self.db.list_notes()[0]["title"], "Plan")

    def test_settings_rejects_remote_url(self):
        from ui.settings_view import SettingsView
        view = SettingsView(self.ctx)
        view.url.setText("https://api.example.com")
        view._apply()
        self.assertEqual(self.db.all_settings()["ollama_url"], "http://localhost:11434")


if __name__ == "__main__":
    unittest.main()
