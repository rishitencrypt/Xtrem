from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QWidget

from ui.context import AppContext
from ui.widgets import NeuButton, label, panel
from ui.workers import StatusWorker


class OverviewView(QWidget):
    go = Signal(str)  # assistant | files | notes | tasks | new_note | new_task

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self._worker: StatusWorker | None = None
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 2)
        grid.addWidget(label("Welcome to Xtrem", "Heading", wrap=False), 0, 0, 1, 2)

        status, sl = panel(padding=16)
        sl.addWidget(label("LOCAL MODEL", "Section", wrap=False))
        self.status_main = label("Checking…", "Title")
        self.status_detail = label("", "Muted")
        sl.addWidget(self.status_main)
        sl.addWidget(self.status_detail)
        again = NeuButton("Check again", icon_name="refresh")
        again.clicked.connect(self._check)
        row = QHBoxLayout()
        row.addWidget(again)
        row.addStretch(1)
        sl.addLayout(row)
        grid.addWidget(status, 1, 0)

        short, shl = panel(padding=16)
        shl.addWidget(label("START", "Section", wrap=False))
        r1, r2 = QHBoxLayout(), QHBoxLayout()
        for lay, items in ((r1, [("Chat", "assistant", "assistant"), ("Files", "files", "files")]),
                           (r2, [("New note", "new_note", "notes"), ("New task", "new_task", "tasks")])):
            for text, key, icon in items:
                b = NeuButton(text, icon_name=icon)
                b.clicked.connect(lambda _=False, k=key: self.go.emit(k))
                lay.addWidget(b)
            lay.addStretch(1)
            shl.addLayout(lay)
        grid.addWidget(short, 2, 0)

        recent, rl = panel(padding=16, spacing=6)
        self.notes_lbl, self.tasks_lbl, self.files_lbl = label(""), label(""), label("")
        for title, lab in (("RECENT NOTES", self.notes_lbl), ("OPEN TASKS", self.tasks_lbl),
                           ("RECENT FILES", self.files_lbl)):
            rl.addWidget(label(title, "Section", wrap=False))
            rl.addWidget(lab)
            rl.addSpacing(10)
        rl.addStretch(1)
        grid.addWidget(recent, 1, 1, 2, 1)
        grid.setRowStretch(3, 1)

    def refresh(self) -> None:
        try:
            notes = [n["title"] for n in self.ctx.db.list_notes()[:4]]
            tasks = self.ctx.db.list_tasks("pending")
        except Exception as exc:  # noqa: BLE001
            self.notes_lbl.setText(f"Could not read data: {exc}")
            return
        self.notes_lbl.setText("\n".join(notes) or "No notes yet.")
        lines = [("! " if t["overdue"] else "") + t["title"] + (f"  ({t['due_date']})" if t["due_date"] else "")
                 for t in tasks[:4]]
        more = f"\n+ {len(tasks) - 4} more" if len(tasks) > 4 else ""
        self.tasks_lbl.setText("\n".join(lines) + more if lines else "Nothing open.")
        files = self.ctx.workspace.recent_files(4)
        self.files_lbl.setText("\n".join(self.ctx.workspace.rel(f) for f in files) or "Workspace is empty.")
        self._check()

    def _check(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self.status_main.setText("Checking…")
        self.status_detail.setText("")
        w = StatusWorker(self.ctx.cfg["ollama_url"], self.ctx.cfg["model"])
        w.result.connect(self._on_status)
        w.finished.connect(w.deleteLater)
        w.finished.connect(lambda: setattr(self, "_worker", None))
        self._worker = w
        w.start()

    def _on_status(self, ok: bool, models: list, caps: list, err: str) -> None:
        model = self.ctx.cfg["model"]
        if not ok:
            self.status_main.setText("Ollama not reachable")
            self.status_detail.setText(err)
        elif not (model in models or f"{model}:latest" in models):
            self.status_main.setText(f"{model} is not installed")
            self.status_detail.setText(f"Install it yourself with: ollama pull {model}")
        else:
            self.status_main.setText(f"{model} is ready")
            think = ("thinks; reasoning is kept out of the chat" if "thinking" in caps and
                     self.ctx.cfg["think_mode"] == "hidden" else
                     "thinking is turned off" if "thinking" in caps else "no thinking mode")
            tools = "tools available" if "tools" in caps else "no tool support: chat only"
            self.status_detail.setText(f"{tools} · {think}")

    def shutdown(self) -> None:
        if self._worker is not None:
            self._worker.wait(3500)
