from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPlainTextEdit, QVBoxLayout, QWidget)

from storage.db import StorageError
from ui.context import AppContext
from ui.widgets import NeuButton, label, panel, wrap_inset

ALL = "All projects"
NO_TASK = "No linked task"


class NotesView(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self._id: int | None = None
        self._dirty = False
        self._loading = False
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        left, ll = panel(padding=16, spacing=8)
        ll.addWidget(label("Notes", "Title", wrap=False))
        self.search = QLineEdit()
        self.search.setFrame(False)
        self.search.setPlaceholderText("Search notes")
        self.search.textChanged.connect(lambda _t: self._reload(self._id))
        ll.addWidget(wrap_inset(self.search))
        self.project_filter = QComboBox()
        self.project_filter.currentIndexChanged.connect(lambda _i: self._reload(self._id))
        ll.addWidget(wrap_inset(self.project_filter, pad=(8, 2, 8, 2)))
        self.list = QListWidget()
        self.list.setFrameShape(QFrame.NoFrame)
        self.list.currentItemChanged.connect(self._on_select)
        ll.addWidget(wrap_inset(self.list, pad=(8, 8, 8, 8)), 1)
        new = NeuButton("New Note", icon_name="plus")
        new.clicked.connect(self.new_note)
        ll.addWidget(new)
        outer.addWidget(left, 2)

        right, rl = panel(padding=16, spacing=8)
        self.title = QLineEdit()
        self.title.setFrame(False)
        self.title.setPlaceholderText("Title")
        self.project = QLineEdit()
        self.project.setFrame(False)
        self.project.setPlaceholderText("Project (optional)")
        self.task = QComboBox()
        meta = QHBoxLayout()
        meta.addWidget(wrap_inset(self.project), 1)
        meta.addWidget(wrap_inset(self.task, pad=(8, 2, 8, 2)), 1)
        self.body = QPlainTextEdit()
        self.body.setFrameShape(QFrame.NoFrame)
        self.body.setPlaceholderText("Write something…")
        for w in (self.title, self.project):
            w.textChanged.connect(self._mark)
        self.body.textChanged.connect(self._mark)
        self.task.currentIndexChanged.connect(lambda _i: self._mark())
        rl.addWidget(wrap_inset(self.title))
        rl.addLayout(meta)
        rl.addWidget(wrap_inset(self.body, pad=(10, 8, 10, 8)), 1)
        row = QHBoxLayout()
        self.status = label("", "Muted")
        row.addWidget(self.status, 1)
        delete = NeuButton("Delete…", "danger", "trash")
        delete.clicked.connect(self._delete)
        save = NeuButton("Save", "primary", "save")
        save.clicked.connect(self._save)
        row.addWidget(delete)
        row.addWidget(save)
        rl.addLayout(row)
        outer.addWidget(right, 3)
        self.refresh()

    def _mark(self) -> None:
        if not self._loading:
            self._dirty = True

    def refresh(self) -> None:
        self._loading = True
        current = self.project_filter.currentText()
        self.project_filter.blockSignals(True)
        self.project_filter.clear()
        self.project_filter.addItem(ALL)
        try:
            self.project_filter.addItems(self.ctx.db.note_projects())
            tasks = self.ctx.db.list_tasks("all")
        except StorageError as exc:
            self.status.setText(str(exc))
            tasks = []
        idx = self.project_filter.findText(current)
        self.project_filter.setCurrentIndex(max(idx, 0))
        self.project_filter.blockSignals(False)
        linked = self.task.currentData()
        self.task.blockSignals(True)
        self.task.clear()
        self.task.addItem(NO_TASK, None)
        for t in tasks:
            self.task.addItem(f"Task #{t['id']}: {t['title'][:40]}", t["id"])
        i = self.task.findData(linked)
        self.task.setCurrentIndex(max(i, 0))
        self.task.blockSignals(False)
        self._loading = False
        self._reload(self._id)

    def _reload(self, select: int | None) -> None:
        project = self.project_filter.currentText()
        try:
            notes = self.ctx.db.list_notes(self.search.text(), None if project in ("", ALL) else project)
        except StorageError as exc:
            self.status.setText(str(exc))
            return
        self.list.blockSignals(True)
        self.list.clear()
        for n in notes:
            it = QListWidgetItem(n["title"] + (f"   · {n['project']}" if n["project"] else ""))
            it.setData(Qt.UserRole, n["id"])
            self.list.addItem(it)
            if n["id"] == select:
                self.list.setCurrentItem(it)
        self.list.blockSignals(False)

    def _open(self, note_id: int | None) -> None:
        self._loading = True
        note = self.ctx.db.get_note(note_id) if note_id is not None else None
        self._id = note["id"] if note else None
        self.title.setText(note["title"] if note else "")
        self.project.setText(note["project"] if note else "")
        self.body.setPlainText(note["body"] if note else "")
        i = self.task.findData(note["task_id"] if note else None)
        self.task.setCurrentIndex(max(i, 0))
        self._loading = False
        self._dirty = False

    def _on_select(self, cur, _prev) -> None:
        new_id = cur.data(Qt.UserRole) if cur else None
        if new_id == self._id:
            return
        if self._dirty:
            self._save()
        self._open(new_id)
        self._reload(new_id)

    def new_note(self) -> None:
        if self._dirty:
            self._save()
        self._open(None)
        self._reload(None)
        self.title.setFocus()
        self.status.setText("")

    def _save(self) -> None:
        if not (self.title.text().strip() or self.body.toPlainText().strip()):
            return
        try:
            self._id = self.ctx.db.save_note(self._id, self.title.text(), self.body.toPlainText(),
                                             self.project.text(), self.task.currentData())
        except StorageError as exc:
            self.status.setText(str(exc))
            return
        self._dirty = False
        self.status.setText("Saved")
        self.refresh()

    def _delete(self) -> None:
        if self._id is None:
            return
        ok = QMessageBox.warning(self, "Delete note", "Delete this note? This cannot be undone.",
                                 QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ok != QMessageBox.Yes:
            return
        try:
            self.ctx.db.delete_note(self._id)
        except StorageError as exc:
            self.status.setText(str(exc))
            return
        self._open(None)
        self.refresh()
        self.status.setText("Deleted")

    def flush(self) -> None:
        if self._dirty:
            self._save()
