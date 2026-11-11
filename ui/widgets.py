"""Neumorphic building blocks, painted directly with QPainter (no graphics effects, so
scrolling content stays fast): NeuFrame (raised / inset panels), NeuButton, wrap_inset."""
from __future__ import annotations

import math

from PySide6.QtCore import QEvent, QObject, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ui import icons
from ui.theme import Palette, current


def _alpha(color: str, a: float) -> QColor:
    c = QColor(color)
    c.setAlphaF(max(0.0, min(1.0, a)))
    return c


def _round_path(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def _soft_shadow(p: QPainter, rect: QRectF, radius: float, color: str, spread: float,
                 dx: float, dy: float, total_alpha: float, layers: int = 8) -> None:
    per = total_alpha / layers
    for i in range(layers, 0, -1):
        grow = spread * i / layers
        r = rect.translated(dx, dy).adjusted(-grow, -grow, grow, grow)
        p.fillPath(_round_path(r, radius + grow), _alpha(color, per))


def draw_raised(p: QPainter, rect: QRectF, radius: float, t: Palette, depth: float,
                face_top: str | None = None, face_bottom: str | None = None) -> None:
    off = depth * 0.55
    _soft_shadow(p, rect, radius, t.shadow, depth, off, off, 0.50)
    _soft_shadow(p, rect, radius, t.light, depth, -off, -off, 0.95)
    grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
    grad.setColorAt(0, QColor(face_top or t.face_hi))
    grad.setColorAt(1, QColor(face_bottom or t.face))
    p.fillPath(_round_path(rect, radius), grad)
    p.setPen(QPen(_alpha(t.light, 0.7), 1))
    p.setBrush(Qt.NoBrush)
    p.drawPath(_round_path(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius))


def draw_inset(p: QPainter, rect: QRectF, radius: float, t: Palette, depth: float) -> None:
    path = _round_path(rect, radius)
    p.fillPath(path, QColor(t.inset))
    p.save()
    p.setClipPath(path)
    big = QPainterPath()
    big.addRect(rect.adjusted(-60, -60, 60, 60))
    layers = 6
    for i in range(1, layers + 1):
        s = depth * i / layers
        dark = big.subtracted(path.translated(s, s))
        light = big.subtracted(path.translated(-s, -s))
        p.fillPath(dark, _alpha(t.shadow, 0.20))
        p.fillPath(light, _alpha(t.light, 0.35))
    p.restore()


class NeuFrame(QFrame):
    """A panel that paints itself raised (default), inset (fields), or flat (no chrome).
    It reserves a margin for its own shadow; child layouts add their own padding inside."""

    def __init__(self, kind: str = "raised", radius: int = 22, depth: int = 7, parent=None) -> None:
        super().__init__(parent)
        self.kind, self.radius, self.depth = kind, radius, depth
        self._focused = False
        m = int(math.ceil(depth * 1.6)) + 1 if kind == "raised" else 2
        self.setContentsMargins(m, m, m, m)
        self.setAttribute(Qt.WA_StyledBackground, False)

    def set_focused(self, on: bool) -> None:
        if on != self._focused:
            self._focused = on
            self.update()

    def paintEvent(self, _event) -> None:
        t = current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        m = self.contentsMargins().left()
        rect = QRectF(self.rect()).adjusted(m, m, -m, -m)
        if self.kind == "raised":
            draw_raised(p, rect, self.radius, t, self.depth)
        elif self.kind == "inset":
            draw_inset(p, rect, self.radius, t, self.depth)
            if self._focused:
                p.setPen(QPen(QColor(t.accent), 1.8))
                p.setBrush(Qt.NoBrush)
                p.drawPath(_round_path(rect.adjusted(1, 1, -1, -1), self.radius))
        p.end()


def panel(layout_cls=QVBoxLayout, padding: int = 18, kind: str = "raised", radius: int = 22,
          depth: int = 7, spacing: int = 10):
    frame = NeuFrame(kind, radius, depth)
    lay = layout_cls(frame)
    lay.setContentsMargins(padding, padding, padding, padding)
    lay.setSpacing(spacing)
    return frame, lay


class _FocusWatcher(QObject):
    def __init__(self, frame: NeuFrame) -> None:
        super().__init__(frame)
        self.frame = frame

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.FocusIn:
            self.frame.set_focused(True)
        elif event.type() == QEvent.FocusOut:
            self.frame.set_focused(False)
        return False


def wrap_inset(widget: QWidget, radius: int = 14, depth: int = 4, pad=(10, 6, 10, 6)) -> NeuFrame:
    """Put a (borderless, transparent) input widget into a recessed neumorphic well."""
    frame = NeuFrame("inset", radius, depth)
    lay = QHBoxLayout(frame)
    lay.setContentsMargins(*pad)
    lay.addWidget(widget)
    widget.installEventFilter(_FocusWatcher(frame))
    return frame


def label(text: str = "", name: str = "", wrap: bool = True) -> QLabel:
    lab = QLabel(text)
    if name:
        lab.setObjectName(name)
    lab.setWordWrap(wrap)
    return lab


class NeuButton(QPushButton):
    """kind: default | primary | danger | nav. Raised at rest, inset when pressed or checked."""
    M = 7

    def __init__(self, text: str = "", kind: str = "default", icon_name: str | None = None,
                 parent=None) -> None:
        super().__init__(text, parent)
        self.kind, self.icon_name = kind, icon_name
        self._hover = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)
        if kind == "nav":
            self.setCheckable(True)

    def sizeHint(self) -> QSize:
        w = self.fontMetrics().horizontalAdvance(self.text()) + 34 + (26 if self.icon_name else 0)
        return QSize(max(w, 40) + 2 * self.M, 40 + 2 * self.M)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def enterEvent(self, e) -> None:
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _event) -> None:
        t = current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(self.M, self.M, -self.M, -self.M)
        pressed = self.isDown() or (self.isChecked() and self.kind == "nav")
        radius, enabled = 14, self.isEnabled()
        text_color = t.text
        if self.kind == "nav":
            if pressed:
                draw_inset(p, rect, radius, t, 3)
                text_color = t.accent
            elif self._hover:
                draw_raised(p, rect, radius, t, 3)
        elif pressed:
            draw_inset(p, rect, radius, t, 3)
        elif not enabled:
            draw_inset(p, rect, radius, t, 2)
        elif self.kind == "primary":
            draw_raised(p, rect, radius, t, 4, t.accent2 if self._hover else t.accent, t.accent)
            text_color = t.on_accent
        else:
            draw_raised(p, rect, radius, t, 4, t.face_hi if not self._hover else "#ffffff", t.face)
            if self.kind == "danger":
                text_color = t.danger
        if not enabled:
            text_color = t.muted
        if self.hasFocus():
            p.setPen(QPen(QColor(t.accent), 1.6))
            p.setBrush(Qt.NoBrush)
            p.drawPath(_round_path(rect.adjusted(1, 1, -1, -1), radius))
        nudge = 1 if self.isDown() else 0
        inner = rect.adjusted(14, nudge, -14, nudge)
        x = inner.left()
        if self.icon_name:
            pm = icons.pixmap(self.icon_name, text_color, 18)
            icon_w = 18 + 8
            text_w = self.fontMetrics().horizontalAdvance(self.text())
            if self.kind != "nav":
                x = inner.center().x() - (icon_w + text_w) / 2 if self.text() else inner.center().x() - 9
            p.drawPixmap(int(x), int(rect.center().y() - 9 + nudge), pm)
            x += icon_w if self.text() else 0
        font = self.font()
        font.setWeight(font.Weight.DemiBold)
        p.setFont(font)
        p.setPen(QColor(text_color))
        align = Qt.AlignVCenter | (Qt.AlignLeft if (self.kind == "nav" or self.icon_name) else Qt.AlignHCenter)
        box = QRectF(x, inner.top(), inner.right() - x, inner.height()) if (self.kind == "nav" or self.icon_name) else inner
        p.drawText(box, int(align), self.text())
        p.end()
