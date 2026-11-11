from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QButtonGroup, QHBoxLayout, QMainWindow, QStackedWidget,
                               QVBoxLayout, QWidget)

from ui.chat_view import AssistantView
from ui.context import AppContext
from ui.files_view import FilesView
from ui.notes_view import NotesView
from ui.overview_view import OverviewView
from ui.settings_view import SettingsView
from ui.tasks_view import TasksView
from ui.theme import build_stylesheet
from ui.widgets import NeuButton, NeuFrame, label


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle("Xtrem")
        self.resize(1220, 800)
        self.setMinimumSize(960, 640)
        self.apply_theme()

        root = QWidget()
        root.setObjectName("Root")
        root.setAttribute(Qt.WA_StyledBackground, True)
        self.setCentralWidget(root)
        lay = QHBoxLayout(root)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(10)

        self.overview = OverviewView(ctx)
        self.assistant = AssistantView(ctx)
        self.files = FilesView(ctx)
        self.notes = NotesView(ctx)
        self.tasks = TasksView(ctx)
        self.settings = SettingsView(ctx)
        pages = [("Overview", "overview", self.overview), ("Assistant", "assistant", self.assistant),
                 ("Files", "files", self.files), ("Notes", "notes", self.notes),
                 ("Tasks", "tasks", self.tasks), ("Settings", "settings", self.settings)]

        side = NeuFrame("raised", radius=26, depth=8)
        side.setFixedWidth(224)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(14, 22, 14, 18)
        sl.setSpacing(2)
        sl.addWidget(label("Xtrem", "Brand", wrap=False))
        sl.addWidget(label("private · local", "Chip", wrap=False))
        sl.addSpacing(18)
        self.stack = QStackedWidget()
        group = QButtonGroup(self)
        group.setExclusive(True)
        self.nav: list[NeuButton] = []
        for i, (name, icon, page) in enumerate(pages):
            b = NeuButton(name, "nav", icon)
            b.clicked.connect(lambda _=False, i=i: self.show_page(i))
            group.addButton(b)
            sl.addWidget(b)
            self.nav.append(b)
            self.stack.addWidget(page)
            QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self, activated=lambda i=i: self.show_page(i))  # Cmd+1..6
        sl.addStretch(1)
        lay.addWidget(side)
        lay.addWidget(self.stack, 1)

        self.overview.go.connect(self._go)
        self.settings.changed.connect(self._settings_changed)
        self.settings.history_cleared.connect(self.assistant.reset_view)
        self.show_page(0)

    def apply_theme(self) -> None:
        QApplication.instance().setStyleSheet(build_stylesheet(self.ctx.cfg["theme"]))
        for w in self.findChildren(QWidget):
            w.update()

    def show_page(self, index: int) -> None:
        if self.stack.currentWidget() is self.notes and index != 3:
            self.notes.flush()
        self.stack.setCurrentIndex(index)
        self.nav[index].setChecked(True)
        page = self.stack.currentWidget()
        if hasattr(page, "refresh"):
            page.refresh()

    def _go(self, key: str) -> None:
        pages = {"assistant": 1, "files": 2, "notes": 3, "tasks": 4}
        if key == "new_note":
            self.show_page(3)
            self.notes.new_note()
        elif key == "new_task":
            self.show_page(4)
            self.tasks.focus_entry()
        else:
            self.show_page(pages[key])

    def _settings_changed(self) -> None:
        self.apply_theme()
        self.files.set_workspace()
        self.assistant.refresh_labels()
        self.settings.load()

    def closeEvent(self, event) -> None:
        if not self.files.confirm_close():
            event.ignore()
            return
        self.notes.flush()
        self.assistant.shutdown()
        self.overview.shutdown()
        self.settings.shutdown()
        self.ctx.db.close()
        event.accept()
