from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QFileSystemModel, QCheckBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QInputDialog,
                               QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
                               QStackedWidget, QTreeView, QVBoxLayout, QWidget)

from services.fs_service import WorkspaceError
from ui.context import AppContext
from ui.widgets import NeuButton, label, panel, wrap_inset


class FilesView(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self._open: Path | None = None
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        left, ll = panel(padding=16, spacing=8)
        ll.addWidget(label("File Studio", "Title", wrap=False))
        self.root_lbl = label("", "Muted")
        ll.addWidget(self.root_lbl)
        self.search = QLineEdit()
        self.search.setFrame(False)
        self.search.setPlaceholderText("Search files")
        ll.addWidget(wrap_inset(self.search))
        self.in_content = QCheckBox("Search text inside files")
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._search)
        self.search.textChanged.connect(lambda _t: self.timer.start())
        self.in_content.toggled.connect(lambda _c: self._search())
        ll.addWidget(self.in_content)

        self.model = QFileSystemModel(self)
        self.tree = QTreeView()
        self.tree.setFrameShape(QFrame.NoFrame)
        self.tree.setModel(self.model)
        for col in (1, 2, 3):
            self.tree.hideColumn(col)
        self.tree.setHeaderHidden(True)
        self.tree.selectionModel().currentChanged.connect(self._on_tree_current)
        self.results = QListWidget()
        self.results.setFrameShape(QFrame.NoFrame)
        self.results.currentItemChanged.connect(self._on_result)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.tree)
        self.stack.addWidget(self.results)
        ll.addWidget(wrap_inset(self.stack, pad=(8, 8, 8, 8)), 1)

        grid = QGridLayout()
        grid.setSpacing(0)
        buttons = [("New Folder", "folder_plus", self._new_folder, "default"),
                   ("New File", "file_plus", self._new_file, "default"),
                   ("Rename", "edit", self._rename, "default"), ("Move…", "move", self._move, "default"),
                   ("Open in App", "open", self._open_external, "default"),
                   ("Delete…", "trash", self._delete, "danger")]
        for i, (text, icon, fn, kind) in enumerate(buttons):
            b = NeuButton(text, kind, icon)
            b.clicked.connect(fn)
            grid.addWidget(b, i // 2, i % 2)
        ll.addLayout(grid)
        outer.addWidget(left, 2)

        right, rl = panel(padding=16, spacing=8)
        self.file_lbl = label("No file open", "Title", wrap=False)
        rl.addWidget(self.file_lbl)
        self.editor = QPlainTextEdit()
        self.editor.setFrameShape(QFrame.NoFrame)
        mono = QFont("Menlo")
        mono.setStyleHint(QFont.Monospace)
        self.editor.setFont(mono)
        self.editor.setReadOnly(True)
        self.editor.setPlaceholderText("Select a text file to view or edit it.")
        rl.addWidget(wrap_inset(self.editor, pad=(10, 8, 10, 8)), 1)
        row = QHBoxLayout()
        self.feedback = label("", "Muted")
        row.addWidget(self.feedback, 1)
        self.save_btn = NeuButton("Save", "primary", "save")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._save)
        row.addWidget(self.save_btn)
        rl.addLayout(row)
        outer.addWidget(right, 3)
        self.set_workspace()

    @property
    def ws(self):
        return self.ctx.workspace

    def set_workspace(self) -> None:
        root = str(self.ws.root)
        self.model.setRootPath(root)
        self.tree.setRootIndex(self.model.index(root))
        self.root_lbl.setText(root)
        self._close_editor()

    def refresh(self) -> None:
        if self.search.text().strip():
            self._search()

    def _msg(self, text: str, error: bool = False) -> None:
        self.feedback.setText(text)
        self.feedback.setStyleSheet("color: #9b1237; font-weight: 700;" if error else "")

    def _selected(self) -> Path | None:
        if self.stack.currentWidget() is self.results:
            it = self.results.currentItem()
            return Path(it.data(Qt.UserRole)) if it else None
        idx = self.tree.currentIndex()
        return Path(self.model.filePath(idx)) if idx.isValid() else None

    def _target_dir(self) -> Path:
        sel = self._selected()
        if sel is None:
            return self.ws.root
        return sel if sel.is_dir() else sel.parent

    def _affects_open(self, path: Path) -> bool:
        if self._open is None:
            return False
        p, q = self._open.resolve(), (path.resolve() if path.exists() else path)
        return p == q or q in p.parents

    def _confirm_discard(self) -> bool:
        if self._open is None or not self.editor.document().isModified():
            return True
        box = QMessageBox.question(self, "Unsaved changes", f"Save changes to {self._open.name}?",
                                   QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
        if box == QMessageBox.Cancel:
            return False
        return self._save() if box == QMessageBox.Save else True

    def _close_editor(self) -> None:
        self._open = None
        self.editor.clear()
        self.editor.document().setModified(False)
        self.editor.setReadOnly(True)
        self.save_btn.setEnabled(False)
        self.file_lbl.setText("No file open")

    def _on_tree_current(self, cur, _prev) -> None:
        if cur.isValid() and not self._load(Path(self.model.filePath(cur))):
            self.tree.selectionModel().blockSignals(True)
            if self._open is not None:
                self.tree.setCurrentIndex(self.model.index(str(self._open)))
            self.tree.selectionModel().blockSignals(False)

    def _on_result(self, cur, _prev) -> None:
        if cur is not None:
            self._load(Path(cur.data(Qt.UserRole)))

    def _load(self, path: Path) -> bool:
        if path.is_dir() or self._open == path:
            return True
        if not self._confirm_discard():
            return False
        try:
            text = self.ws.read_text(path)
        except WorkspaceError as exc:
            self._close_editor()
            self._msg(str(exc), error=True)
            return True
        self.editor.setPlainText(text)
        self.editor.document().setModified(False)
        self.editor.setReadOnly(False)
        self.save_btn.setEnabled(True)
        self._open = path
        self.file_lbl.setText(path.name)
        self._msg("")
        return True

    def _save(self) -> bool:
        if self._open is None:
            return False
        try:
            rep = self.ws.write_text(self._open, self.editor.toPlainText())
        except WorkspaceError as exc:
            self._msg(str(exc), error=True)
            return False
        self.editor.document().setModified(False)
        self._msg(f"Saved and verified: {rep.path} ({rep.size} bytes)")
        return True

    def _search(self) -> None:
        q = self.search.text().strip()
        if not q:
            self.stack.setCurrentWidget(self.tree)
            return
        self.results.blockSignals(True)
        self.results.clear()
        if self.in_content.isChecked():
            for p, n, text in self.ws.search_content(q):
                it = QListWidgetItem(f"{self.ws.rel(p)}:{n}  {text}")
                it.setData(Qt.UserRole, str(p))
                self.results.addItem(it)
        else:
            for p in self.ws.search(q):
                it = QListWidgetItem(self.ws.rel(p))
                it.setData(Qt.UserRole, str(p))
                self.results.addItem(it)
        self.results.blockSignals(False)
        self.stack.setCurrentWidget(self.results)
        self._msg(f"{self.results.count()} match(es)")

    def _ask(self, title: str, prompt: str, default: str = "") -> str | None:
        text, ok = QInputDialog.getText(self, title, prompt, QLineEdit.Normal, default)
        return text.strip() if ok and text.strip() else None

    def _new_folder(self) -> None:
        name = self._ask("New folder", "Folder name:")
        if name:
            try:
                self._msg(f"Created folder {self.ws.make_folder(self._target_dir(), name)}")
            except WorkspaceError as exc:
                self._msg(str(exc), error=True)

    def _new_file(self) -> None:
        name = self._ask("New file", "File name (e.g. notes.txt):")
        if not name:
            return
        try:
            rep = self.ws.create_file(self._target_dir(), name)
        except WorkspaceError as exc:
            return self._msg(str(exc), error=True)
        self._msg(f"Created and verified: {rep.path}")
        if self._confirm_discard():
            self._load(rep.path)

    def _rename(self) -> None:
        sel = self._selected()
        if sel is None:
            return self._msg("Select something first.", error=True)
        name = self._ask("Rename", "New name:", sel.name)
        if not name or not self._confirm_discard():
            return
        was_open = self._affects_open(sel)
        try:
            new = self.ws.rename(sel, name)
        except WorkspaceError as exc:
            return self._msg(str(exc), error=True)
        if was_open:
            self._close_editor()
        self._msg(f"Renamed to {new}")

    def _move(self) -> None:
        sel = self._selected()
        if sel is None:
            return self._msg("Select something first.", error=True)
        dest = QFileDialog.getExistingDirectory(self, "Move to folder", str(self.ws.root))
        if not dest or not self._confirm_discard():
            return
        was_open = self._affects_open(sel)
        try:
            new = self.ws.move(sel, dest)
        except WorkspaceError as exc:
            return self._msg(str(exc), error=True)
        if was_open:
            self._close_editor()
        self._msg(f"Moved to {new}")

    def _delete(self) -> None:
        sel = self._selected()
        if sel is None:
            return self._msg("Select something first.", error=True)
        try:
            count = self.ws.count_tree(sel)
            what = (f"the folder '{sel.name}' and its {count} item(s)" if count else f"'{sel.name}'")
            ok = QMessageBox.warning(self, "Delete", f"Permanently delete {what}? This cannot be undone.",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ok != QMessageBox.Yes:
                return
            affects = self._affects_open(sel)
            self.ws.delete(sel, recursive=bool(count))
        except WorkspaceError as exc:
            return self._msg(str(exc), error=True)
        if affects:
            self._close_editor()
        self._msg(f"Deleted {sel.name}")

    def _open_external(self) -> None:
        sel = self._selected()
        if sel is None:
            return self._msg("Select something first.", error=True)
        try:
            self.ws.open_external(sel)
            self._msg(f"Asked macOS to open {sel.name}")
        except WorkspaceError as exc:
            self._msg(str(exc), error=True)

    def confirm_close(self) -> bool:
        return self._confirm_discard()
