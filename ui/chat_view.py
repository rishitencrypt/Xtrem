from __future__ import annotations

import math

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QTextOption
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit,
                               QScrollArea, QSizePolicy, QTextBrowser, QTextEdit, QVBoxLayout, QWidget)

from services.agent import AgentSettings, RunResult
from services.conversation import Conversation
from services.ollama_client import OllamaError
from ui.context import AppContext
from ui.widgets import NeuButton, label, panel, wrap_inset
from ui.workers import AgentWorker


class AutoTextBrowser(QTextBrowser):
    """Read-only, selectable, wrapping text whose height follows its document. No scrollbars and
    no fixed height: the layout asks sizeHint(), which is recomputed when the document or width changes."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("BubbleText")
        self.setFrameShape(QFrame.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setLineWrapMode(QTextEdit.WidgetWidth)
        self.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.setOpenExternalLinks(True)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard
                                     | Qt.LinksAccessibleByMouse)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.document().setDocumentMargin(0)
        self._height = 24
        self.document().documentLayout().documentSizeChanged.connect(self._on_size)

    def _on_size(self, size) -> None:
        h = math.ceil(size.height()) + 2
        if h != self._height:
            self._height = h
            self.updateGeometry()

    def sizeHint(self) -> QSize:
        return QSize(200, self._height)

    def minimumSizeHint(self) -> QSize:
        return QSize(50, self._height)


class Bubble(QFrame):
    STYLE = {"user": ("BubbleUser", "YOU"), "assistant": ("BubbleAI", "XTREM"),
             "tool": ("BubbleTool", "ACTION"), "error": ("BubbleError", "ERROR")}

    def __init__(self, role: str, text: str = "", tag: str = "") -> None:
        super().__init__()
        self.role = role
        obj, who = self.STYLE[role]
        self.setObjectName(obj)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 14)
        lay.setSpacing(4)
        lay.addWidget(label(tag or who, "BubbleRole", wrap=False))
        self.body = AutoTextBrowser()
        lay.addWidget(self.body)
        self.set_text(text)

    def set_text(self, text: str) -> None:
        if self.role == "assistant":
            self.body.setMarkdown(text or "…")
        else:
            self.body.setPlainText(text)


class ChatInput(QPlainTextEdit):
    submitted = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setFrameShape(QFrame.NoFrame)
        self.setPlaceholderText("Message Xtrem   ·   Enter to send, Shift+Enter for a new line")
        self.document().contentsChanged.connect(self._fit)
        self._fit()

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not (e.modifiers() & Qt.ShiftModifier):
            self.submitted.emit()
            return
        super().keyPressEvent(e)

    def _fit(self) -> None:
        lines = max(1, int(self.document().size().height()))
        self.setFixedHeight(max(34, min(140, lines * self.fontMetrics().lineSpacing() + 14)))


class AssistantView(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.conv = Conversation()
        self._worker: AgentWorker | None = None
        self._threads: set[AgentWorker] = set()
        self._segments: dict[int, Bubble] = {}
        self._seg_text: dict[int, str] = {}
        self._dirty: set[int] = set()
        self._stick = True
        self._dots = 0
        self._phase = "Thinking"

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        box, pl = panel(padding=18, spacing=10)
        outer.addWidget(box)

        head = QHBoxLayout()
        head.addWidget(label("Assistant", "Title", wrap=False))
        self.model_chip = label("", "Chip", wrap=False)
        head.addWidget(self.model_chip)
        head.addStretch(1)
        self.status = label("", "Muted", wrap=False)
        head.addWidget(self.status)
        self.new_btn = NeuButton("New Chat", icon_name="plus")
        self.new_btn.clicked.connect(self.new_chat)
        head.addWidget(self.new_btn)
        pl.addLayout(head)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        container = QWidget()
        self.msg_layout = QVBoxLayout(container)
        self.msg_layout.setContentsMargins(4, 4, 10, 4)
        self.msg_layout.setSpacing(12)
        self.empty_label = label("", "Muted")
        self.msg_layout.addWidget(self.empty_label)
        self.msg_layout.addStretch(1)
        self.scroll.setWidget(container)
        pl.addWidget(self.scroll, 1)
        self._sb = self.scroll.verticalScrollBar()
        self._sb.rangeChanged.connect(self._on_range)
        self._sb.valueChanged.connect(self._on_value)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.input = ChatInput()
        self.input.submitted.connect(self._send)
        row.addWidget(wrap_inset(self.input, radius=18), 1)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.send_btn = NeuButton("Send", "primary", "send")
        self.send_btn.clicked.connect(self._send)
        self.stop_btn = NeuButton("Stop", icon_name="stop")
        self.stop_btn.clicked.connect(self._stop)
        self.stop_btn.setEnabled(False)
        col.addWidget(self.send_btn)
        col.addWidget(self.stop_btn)
        row.addLayout(col)
        pl.addLayout(row)

        self.render_timer = QTimer(self)
        self.render_timer.setInterval(80)
        self.render_timer.timeout.connect(self._render)
        self.dots_timer = QTimer(self)
        self.dots_timer.setInterval(450)
        self.dots_timer.timeout.connect(self._tick)

        self.refresh_labels()
        self._load_saved()

    # ---- scrolling ----
    def _on_range(self, _lo: int, hi: int) -> None:
        if self._stick:
            self._sb.setValue(hi)

    def _on_value(self, v: int) -> None:
        self._stick = v >= self._sb.maximum() - 4

    def _scroll_bottom(self) -> None:
        self._stick = True
        QTimer.singleShot(0, lambda: self._sb.setValue(self._sb.maximum()))

    # ---- bubbles ----
    def refresh_labels(self) -> None:
        self.model_chip.setText(f"· {self.ctx.cfg['model']}")
        self.empty_label.setText("Ask anything, or ask Xtrem to write a file, add a task or save a note. "
                                 "Everything runs locally.")

    def _add_bubble(self, role: str, text: str = "", tag: str = "") -> Bubble:
        bubble = Bubble(role, text, tag)
        row = QHBoxLayout()
        if role == "user":
            row.addStretch(2)
            row.addWidget(bubble, 8)
        else:
            row.addWidget(bubble, 8)
            row.addStretch(2 if role in ("assistant", "error") else 1)
        self.msg_layout.insertLayout(self.msg_layout.count() - 1, row)
        self.empty_label.hide()
        return bubble

    def _clear_bubbles(self) -> None:
        while self.msg_layout.count() > 2:
            item = self.msg_layout.takeAt(1)
            lay = item.layout()
            if lay is not None:
                while lay.count():
                    w = lay.takeAt(0).widget()
                    if w is not None:
                        w.deleteLater()
                lay.deleteLater()
        self.empty_label.show()
        self._segments.clear()
        self._seg_text.clear()
        self._dirty.clear()

    def _load_saved(self) -> None:
        if self.ctx.cfg["save_history"] != "1":
            return
        try:
            rows = self.ctx.db.load_messages()
        except Exception as exc:  # noqa: BLE001
            self._add_bubble("error", f"Could not load saved history: {exc}")
            return
        for m in rows:
            self.conv.messages.append(m)
        for m in self.conv.history_for_model(10**9):  # cleaned: reasoning in old saves is dropped
            self._add_bubble(m["role"], m["content"])
        self._scroll_bottom()

    def _persist(self, role: str, content: str) -> None:
        if self.ctx.cfg["save_history"] != "1":
            return
        try:
            self.ctx.db.add_message(role, content)
        except Exception as exc:  # noqa: BLE001
            self._add_bubble("error", f"Could not save this message: {exc}")

    # ---- sending ----
    def _send(self) -> None:
        if self._worker is not None:
            return
        text = self.input.toPlainText().strip()
        if not text:
            return
        self.input.clear()
        self._add_bubble("user", text)
        self.conv.add_user(text)
        self._persist("user", text)
        self._scroll_bottom()

        cfg = self.ctx.cfg
        try:
            budget = int(cfg["context_chars"])
            settings = AgentSettings(model=cfg["model"], think_mode=cfg["think_mode"],
                                     num_ctx=int(cfg["num_ctx"]), tools_enabled=cfg["tools_enabled"] == "1")
            client = self.ctx.make_client()
        except (ValueError, OllamaError) as exc:
            self._add_bubble("error", f"Invalid settings: {exc}")
            return
        w = AgentWorker(client, self.ctx.registry, settings, self.conv.history_for_model(budget))
        w.status.connect(lambda s, w=w: self._on_status(w, s))
        w.text.connect(lambda seg, s, w=w: self._on_text(w, seg, s))
        w.tool.connect(lambda n, m, ok, w=w: self._on_tool(w, n, m, ok))
        w.confirm_requested.connect(lambda p, w=w: self._on_confirm(w, p))
        w.run_finished.connect(lambda r, w=w: self._on_finished(w, r))
        w.finished.connect(lambda w=w: self._reap(w))
        self._threads.add(w)
        self._worker = w
        self._segments.clear(); self._seg_text.clear(); self._dirty.clear()
        self._phase = "Thinking"
        self._set_running(True)
        w.start()

    def _set_running(self, running: bool) -> None:
        self.send_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.input.setReadOnly(running)
        if running:
            self._dots = 0
            self.render_timer.start()
            self.dots_timer.start()
            self._tick()
        else:
            self.render_timer.stop()
            self.dots_timer.stop()
            self.input.setFocus()

    def _tick(self) -> None:
        self._dots = (self._dots + 1) % 4
        self.status.setText(self._phase + "." * self._dots)

    # ---- worker events (ignored if they come from a stopped/old worker) ----
    def _on_status(self, w: AgentWorker, s: str) -> None:
        if w is self._worker:
            self._phase = s.rstrip("…")

    def _on_text(self, w: AgentWorker, seg: int, text: str) -> None:
        if w is not self._worker:
            return
        self._seg_text[seg] = text
        self._dirty.add(seg)
        self._phase = "Writing"

    def _render(self) -> None:
        for seg in sorted(self._dirty):
            text = self._seg_text.get(seg, "")
            if not text.strip():
                continue
            bubble = self._segments.get(seg)
            if bubble is None:
                bubble = self._segments[seg] = self._add_bubble("assistant")
            bubble.set_text(text)
        self._dirty.clear()

    def _on_tool(self, w: AgentWorker, name: str, message: str, ok: bool) -> None:
        if w is not self._worker:
            return
        self._render()
        shown = message if len(message) <= 600 else message[:600] + "…"
        self._add_bubble("tool", shown, tag=f"{'✓' if ok else '✗'}  {name}")
        self._scroll_bottom()

    def _on_confirm(self, w: AgentWorker, prompt: str) -> None:
        answer = QMessageBox.warning(self, "Xtrem needs your approval", prompt,
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        w.answer(answer == QMessageBox.Yes)

    def _on_finished(self, w: AgentWorker, result: RunResult) -> None:
        if w is not self._worker:
            return
        self._render()
        if result.ok and result.text:
            bubble = self._segments.get(result.segment) or self._add_bubble("assistant")
            bubble.set_text(result.text)
            self.conv.add_assistant(result.text)
            self._persist("assistant", result.text)
        elif not result.ok and not result.cancelled:
            self._add_bubble("error", result.error)  # shown, never stored as a model answer
        self._end_run("Stopped" if result.cancelled else "")
        self._scroll_bottom()

    def _stop(self) -> None:
        w = self._worker
        if w is None:
            return
        w.cancel()
        self._render()
        last = max(self._segments) if self._segments else None
        if last is not None:
            partial = self._seg_text.get(last, "").strip()
            self._segments[last].set_text((partial + "\n\n" if partial else "") + "*(stopped)*")
            if partial:
                self.conv.add_assistant(partial)
                self._persist("assistant", partial)
        self._end_run("Stopped")

    def _end_run(self, note: str = "") -> None:
        self._worker = None
        self._set_running(False)
        self.status.setText(note)

    def _reap(self, w: AgentWorker) -> None:
        self._threads.discard(w)
        w.deleteLater()

    # ---- session control ----
    def new_chat(self) -> None:
        if self._worker is not None:
            self._stop()
        self.conv.clear()
        try:
            self.ctx.db.clear_messages()
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"Could not clear saved history: {exc}")
        self._clear_bubbles()
        self.input.setFocus()

    def reset_view(self) -> None:
        if self._worker is None:
            self.conv.clear()
            self._clear_bubbles()

    def shutdown(self) -> None:
        for t in list(self._threads):
            t.cancel()
            t.wait(2500)
