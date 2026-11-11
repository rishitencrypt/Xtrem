from __future__ import annotations

import os
import re
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLineEdit,
                               QMessageBox, QSpinBox, QVBoxLayout, QWidget)

from services.fs_service import Workspace, WorkspaceError
from services.ollama_client import validate_url
from storage.db import StorageError
from ui.context import AppContext
from ui.theme import PALETTES
from ui.widgets import NeuButton, label, panel, wrap_inset
from ui.workers import StatusWorker

THINK_MODES = [("Hidden thinking (reliable)", "hidden"), ("Fast: thinking off", "off")]


def _line() -> QLineEdit:
    e = QLineEdit()
    e.setFrame(False)
    return e


class SettingsView(QWidget):
    changed = Signal()
    history_cleared = Signal()

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self._worker: StatusWorker | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        box, pl = panel(padding=20, spacing=12)
        outer.addWidget(box)
        pl.addWidget(label("Settings", "Title", wrap=False))

        form = QFormLayout()
        form.setSpacing(6)
        self.model = QComboBox()
        self.model.setEditable(True)
        refresh = NeuButton("Refresh", icon_name="refresh")
        refresh.clicked.connect(self._load_models)
        mrow = QHBoxLayout()
        mrow.addWidget(wrap_inset(self.model, pad=(8, 2, 8, 2)), 1)
        mrow.addWidget(refresh)
        self.model_hint = label("", "Muted")
        self.think = QComboBox()
        for text, _v in THINK_MODES:
            self.think.addItem(text)
        self.tools = QCheckBox("Let the assistant use tools (files, tasks, notes)")
        self.url = _line()
        self.workspace = _line()
        browse = NeuButton("Browse…")
        browse.clicked.connect(self._browse)
        wrow = QHBoxLayout()
        wrow.addWidget(wrap_inset(self.workspace), 1)
        wrow.addWidget(browse)
        self.theme = QComboBox()
        self.theme.addItems(list(PALETTES))
        self.context = QSpinBox()
        self.context.setRange(1000, 60000)
        self.context.setSingleStep(500)
        self.context.setSuffix(" chars")
        self.num_ctx = QSpinBox()
        self.num_ctx.setRange(2048, 32768)
        self.num_ctx.setSingleStep(1024)
        self.num_ctx.setSuffix(" tokens")
        self.history = QCheckBox("Keep the conversation between sessions")
        form.addRow("Model", mrow)
        form.addRow("", self.model_hint)
        form.addRow("Reasoning", wrap_inset(self.think, pad=(8, 2, 8, 2)))
        form.addRow("", self.tools)
        form.addRow("Ollama URL", wrap_inset(self.url))
        form.addRow("Workspace", wrow)
        form.addRow("Theme", wrap_inset(self.theme, pad=(8, 2, 8, 2)))
        form.addRow("History sent", wrap_inset(self.context, pad=(8, 2, 8, 2)))
        form.addRow("Context window", wrap_inset(self.num_ctx, pad=(8, 2, 8, 2)))
        form.addRow("", self.history)
        pl.addLayout(form)
        pl.addWidget(label(
            "If a model still prints its reasoning, pick a non-thinking (instruct) model from the list. "
            "Xtrem never downloads models itself: install one in Terminal with `ollama pull <name>`.", "Muted"))
        pl.addStretch(1)

        row = QHBoxLayout()
        self.status = label("", "Muted")
        row.addWidget(self.status, 1)
        clear = NeuButton("Clear saved history…", "danger", "trash")
        clear.clicked.connect(self._clear)
        apply = NeuButton("Apply", "primary", "save")
        apply.clicked.connect(self._apply)
        row.addWidget(clear)
        row.addWidget(apply)
        pl.addLayout(row)
        self.load()

    def refresh(self) -> None:
        self._load_models()

    def load(self) -> None:
        c = self.ctx.cfg
        self.model.setEditText(c["model"])
        self.think.setCurrentIndex(next((i for i, (_t, v) in enumerate(THINK_MODES) if v == c["think_mode"]), 0))
        self.tools.setChecked(c["tools_enabled"] == "1")
        self.url.setText(c["ollama_url"])
        self.workspace.setText(c["workspace"])
        self.theme.setCurrentText(c["theme"])
        self.context.setValue(int(c["context_chars"]))
        self.num_ctx.setValue(int(c["num_ctx"]))
        self.history.setChecked(c["save_history"] == "1")

    def _load_models(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        w = StatusWorker(self.url.text().strip() or self.ctx.cfg["ollama_url"], self.model.currentText().strip())
        w.result.connect(self._on_models)
        w.finished.connect(w.deleteLater)
        w.finished.connect(lambda: setattr(self, "_worker", None))
        self._worker = w
        w.start()

    def _on_models(self, ok: bool, models: list, caps: list, err: str) -> None:
        if not ok:
            self.model_hint.setText(err)
            return
        current = self.model.currentText()
        self.model.clear()
        self.model.addItems(models)
        self.model.setEditText(current)
        if not caps:
            self.model_hint.setText("This model is not installed." if current else "")
        else:
            self.model_hint.setText(("Can think · " if "thinking" in caps else "No thinking mode · ")
                                    + ("supports tools" if "tools" in caps else "no tool support (chat only)"))

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Workspace folder", self.workspace.text())
        if d:
            self.workspace.setText(d)

    def _validate(self) -> dict[str, str]:
        model = self.model.currentText().strip()
        if not re.fullmatch(r"[A-Za-z0-9._:/-]+", model):
            raise ValueError("Model name may only use letters, digits and . _ : / -")
        url = validate_url(self.url.text())
        raw = self.workspace.text().strip()
        if not raw:
            raise ValueError("Workspace folder is required.")
        ws = Path(raw).expanduser().resolve()
        if ws in (Path("/"), Path.home().resolve()):
            raise ValueError("Choose a dedicated folder, not / or your home folder.")
        try:
            Workspace(ws)
        except WorkspaceError as exc:
            raise ValueError(str(exc)) from exc
        if not os.access(ws, os.W_OK):
            raise ValueError("The workspace folder is not writable.")
        return {"model": model, "ollama_url": url, "workspace": str(ws),
                "think_mode": THINK_MODES[self.think.currentIndex()][1],
                "tools_enabled": "1" if self.tools.isChecked() else "0",
                "theme": self.theme.currentText(), "context_chars": str(self.context.value()),
                "num_ctx": str(self.num_ctx.value()),
                "save_history": "1" if self.history.isChecked() else "0"}

    def _apply(self) -> None:
        try:
            values = self._validate()
            for k, v in values.items():
                self.ctx.db.set_setting(k, v)
        except (ValueError, StorageError) as exc:
            self.status.setStyleSheet("color: #9b1237; font-weight: 700;")
            self.status.setText(str(exc))
            return
        self.status.setStyleSheet("")
        self.ctx.reload()
        self.status.setText("Settings applied")
        self.changed.emit()

    def _clear(self) -> None:
        ok = QMessageBox.warning(self, "Clear history", "Delete the saved conversation?",
                                 QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ok != QMessageBox.Yes:
            return
        try:
            self.ctx.db.clear_messages()
        except StorageError as exc:
            self.status.setText(str(exc))
            return
        self.status.setText("Saved history cleared")
        self.history_cleared.emit()

    def shutdown(self) -> None:
        if self._worker is not None:
            self._worker.wait(3500)
