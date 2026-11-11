from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QComboBox, QDialog, QFormLayout, QFrame, QHBoxLayout, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QVBoxLayout, QWidget)

from storage.db import PRIORITIES, StorageError
from ui.context import AppContext
from ui.widgets import NeuButton, label, panel, wrap_inset

STATUSES = [("Pending", "pending"), ("Overdue", "overdue"), ("Completed", "completed"), ("All", "all")]
ALL_CATS = "All categories"


def _line(placeholder: str = "") -> QLineEdit:
    e = QLineEdit()
    e.setFrame(False)
    e.setPlaceholderText(placeholder)
    return e


class TaskDialog(QDialog):
    def __init__(self, parent, task: dict) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit task")
        self.setMinimumWidth(440)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.title = _line()
        self.title.setText(task["title"])
        self.due = _line("YYYY-MM-DD (optional)")
        self.due.setText(task["due_date"] or "")
        self.priority = QComboBox()
        self.priority.addItems(PRIORITIES)
        self.priority.setCurrentText(task["priority"])
        self.category = _line("Category")
        self.category.setText(task["category"])
        self.details = QPlainTextEdit(task["details"])
        self.details.setFixedHeight(90)
        form.addRow("Title", wrap_inset(self.title))
        form.addRow("Due", wrap_inset(self.due))
        form.addRow("Priority", wrap_inset(self.priority, pad=(8, 2, 8, 2)))
        form.addRow("Category", wrap_inset(self.category))
        form.addRow("Details", wrap_inset(self.details))
        lay.addLayout(form)
        self.error = label("", "Muted")
        lay.addWidget(self.error)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = NeuButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = NeuButton("Save", "primary")
        ok.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)

    def values(self) -> dict:
        due = self.due.text().strip()
        return {"title": self.title.text(), "due": due or None, "clear_due": not due,
                "priority": self.priority.currentText(), "category": self.category.text(),
                "details": self.details.toPlainText()}


class TasksView(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        box, pl = panel(padding=18, spacing=10)
        outer.addWidget(box)
        pl.addWidget(label("Tasks", "Title", wrap=False))

        add = QHBoxLayout()
        self.entry = _line("New task")
        self.entry.returnPressed.connect(self._add)
        self.due = _line("Due YYYY-MM-DD")
        self.priority = QComboBox()
        self.priority.addItems(PRIORITIES)
        self.priority.setCurrentText("normal")
        self.category = _line("Category")
        add.addWidget(wrap_inset(self.entry), 4)
        add.addWidget(wrap_inset(self.due), 2)
        add.addWidget(wrap_inset(self.priority, pad=(8, 2, 8, 2)), 1)
        add.addWidget(wrap_inset(self.category), 2)
        btn = NeuButton("Add", "primary", "plus")
        btn.clicked.connect(self._add)
        add.addWidget(btn)
        pl.addLayout(add)

        flt = QHBoxLayout()
        self.status_filter = QComboBox()
        for text, _v in STATUSES:
            self.status_filter.addItem(text)
        self.cat_filter = QComboBox()
        self.status_filter.currentIndexChanged.connect(lambda _i: self.refresh())
        self.cat_filter.currentIndexChanged.connect(lambda _i: self.refresh())
        flt.addWidget(wrap_inset(self.status_filter, pad=(8, 2, 8, 2)), 1)
        flt.addWidget(wrap_inset(self.cat_filter, pad=(8, 2, 8, 2)), 1)
        flt.addStretch(2)
        pl.addLayout(flt)

        self.list = QListWidget()
        self.list.setFrameShape(QFrame.NoFrame)
        self.list.itemChanged.connect(self._toggle)
        self.list.itemDoubleClicked.connect(lambda _i: self._edit())
        pl.addWidget(wrap_inset(self.list, pad=(8, 8, 8, 8)), 1)

        row = QHBoxLayout()
        self.status = label("", "Muted")
        row.addWidget(self.status, 1)
        edit = NeuButton("Edit", icon_name="edit")
        edit.clicked.connect(self._edit)
        delete = NeuButton("Delete…", "danger", "trash")
        delete.clicked.connect(self._delete)
        row.addWidget(edit)
        row.addWidget(delete)
        pl.addLayout(row)
        self.refresh()

    def _note(self, text: str) -> None:
        self.status.setText(text)

    @staticmethod
    def _style(item: QListWidgetItem, done: bool, overdue: bool) -> None:
        f = item.font()
        f.setStrikeOut(done)
        item.setFont(f)
        item.setForeground(QBrush(QColor("#9b1237" if overdue else "#6a4271" if done else "#2a0f2e")))

    def refresh(self) -> None:
        status = STATUSES[max(self.status_filter.currentIndex(), 0)][1]
        cat = self.cat_filter.currentText()
        try:
            cats = self.ctx.db.task_categories()
            tasks = self.ctx.db.list_tasks(status, category=None if cat in ("", ALL_CATS) else cat)
        except StorageError as exc:
            self._note(str(exc))
            return
        self.cat_filter.blockSignals(True)
        self.cat_filter.clear()
        self.cat_filter.addItems([ALL_CATS] + cats)
        self.cat_filter.setCurrentIndex(max(self.cat_filter.findText(cat), 0))
        self.cat_filter.blockSignals(False)
        self.list.blockSignals(True)
        self.list.clear()
        for t in tasks:
            bits = [t["title"]]
            if t["due_date"]:
                bits.append(f"due {t['due_date']}" + (" · OVERDUE" if t["overdue"] else ""))
            bits.append(t["priority"])
            if t["category"]:
                bits.append(t["category"])
            it = QListWidgetItem("   ·   ".join(bits))
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if t["done"] else Qt.Unchecked)
            it.setData(Qt.UserRole, t["id"])
            self._style(it, bool(t["done"]), t["overdue"])
            self.list.addItem(it)
        self.list.blockSignals(False)

    def focus_entry(self) -> None:
        self.entry.setFocus()

    def _add(self) -> None:
        text = self.entry.text().strip()
        if not text:
            return
        try:
            self.ctx.db.add_task(text, self.due.text().strip() or None, self.priority.currentText(),
                                 self.category.text())
        except StorageError as exc:
            return self._note(str(exc))
        for e in (self.entry, self.due, self.category):
            e.clear()
        self.refresh()
        self._note("Added")

    def _toggle(self, item: QListWidgetItem) -> None:
        try:
            self.ctx.db.update_task(item.data(Qt.UserRole), done=item.checkState() == Qt.Checked)
        except StorageError as exc:
            return self._note(str(exc))
        self.refresh()

    def _current(self) -> dict | None:
        item = self.list.currentItem()
        return self.ctx.db.get_task(item.data(Qt.UserRole)) if item else None

    def _edit(self) -> None:
        task = self._current()
        if task is None:
            return
        dlg = TaskDialog(self, task)
        if dlg.exec() != QDialog.Accepted:
            return
        try:
            self.ctx.db.update_task(task["id"], **dlg.values())
        except StorageError as exc:
            return self._note(str(exc))
        self.refresh()
        self._note("Updated")

    def _delete(self) -> None:
        task = self._current()
        if task is None:
            return
        ok = QMessageBox.warning(self, "Delete task", f"Delete '{task['title']}'?",
                                 QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ok == QMessageBox.Yes:
            try:
                self.ctx.db.delete_task(task["id"])
            except StorageError as exc:
                return self._note(str(exc))
            self.refresh()
            self._note("Deleted")
