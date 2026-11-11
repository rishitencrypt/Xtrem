"""Small line icons drawn with QPainter (no image assets). One consistent 24x24 grid."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap


def _lines(p: QPainter, *pts: tuple[float, float]) -> None:
    path = QPainterPath(QPointF(*pts[0]))
    for pt in pts[1:]:
        path.lineTo(QPointF(*pt))
    p.drawPath(path)


def _rr(p, x, y, w, h, r=3):
    p.drawRoundedRect(QRectF(x, y, w, h), r, r)


def _overview(p):
    for x, y in ((3.5, 3.5), (13.5, 3.5), (3.5, 13.5), (13.5, 13.5)):
        _rr(p, x, y, 7, 7, 2)


def _assistant(p):
    _rr(p, 3, 4, 18, 12.5, 4)
    _lines(p, (8, 16.5), (8, 20.5), (12.5, 16.5))
    _lines(p, (8, 10), (16, 10))


def _files(p):
    _lines(p, (3, 6.5), (3, 19), (21, 19), (21, 8.5), (11.5, 8.5), (9.5, 6), (3, 6), (3, 6.5))


def _notes(p):
    _rr(p, 5, 3, 14, 18, 3)
    _lines(p, (8.5, 9), (15.5, 9)); _lines(p, (8.5, 13), (15.5, 13)); _lines(p, (8.5, 17), (12.5, 17))


def _tasks(p):
    _rr(p, 4, 4, 16, 16, 4)
    _lines(p, (8, 12.2), (10.8, 15), (16, 9))


def _settings(p):
    for y, kx in ((7, 9), (12, 15), (17, 8)):
        _lines(p, (3.5, y), (20.5, y))
        p.setBrush(p.pen().color())
        p.drawEllipse(QPointF(kx, y), 2.2, 2.2)
        p.setBrush(Qt.NoBrush)


def _plus(p):
    _lines(p, (12, 5), (12, 19)); _lines(p, (5, 12), (19, 12))


def _trash(p):
    _lines(p, (4, 7), (20, 7)); _lines(p, (9, 7), (9, 4), (15, 4), (15, 7))
    _lines(p, (6, 7), (7, 20), (17, 20), (18, 7))


def _save(p):
    _lines(p, (4, 4), (17, 4), (20, 7), (20, 20), (4, 20), (4, 4))
    _rr(p, 8, 4, 7, 5, 1); _rr(p, 7, 13, 10, 7, 1.5)


def _refresh(p):
    p.drawArc(QRectF(4.5, 4.5, 15, 15), 40 * 16, 270 * 16)
    _lines(p, (17.5, 3.5), (18.5, 8), (14, 8.5))


def _send(p):
    _lines(p, (4, 12), (20, 4), (14, 20), (11.5, 13), (4, 12))
    _lines(p, (11.5, 13), (20, 4))


def _stop(p):
    _rr(p, 6, 6, 12, 12, 2.5)


def _search(p):
    p.drawEllipse(QPointF(10.5, 10.5), 6, 6)
    _lines(p, (15, 15), (20, 20))


def _edit(p):
    _lines(p, (4, 20), (5, 15), (16, 4), (20, 8), (9, 19), (4, 20))
    _lines(p, (14, 6), (18, 10))


def _open(p):
    _lines(p, (10, 5), (5, 5), (5, 19), (19, 19), (19, 14))
    _lines(p, (13, 4), (20, 4), (20, 11)); _lines(p, (20, 4), (11, 13))


def _move(p):
    _lines(p, (3, 12), (21, 12)); _lines(p, (16, 7), (21, 12), (16, 17))


def _folder_plus(p):
    _lines(p, (3, 6.5), (3, 19), (21, 19), (21, 9), (11.5, 9), (9.5, 6.5), (3, 6.5))
    _lines(p, (12, 11.5), (12, 16.5)); _lines(p, (9.5, 14), (14.5, 14))


def _file_plus(p):
    _lines(p, (6, 3), (14, 3), (19, 8), (19, 21), (6, 21), (6, 3))
    _lines(p, (12.5, 11), (12.5, 17)); _lines(p, (9.5, 14), (15.5, 14))


DRAW = {n[1:]: f for n, f in globals().items()
        if n.startswith("_") and callable(f) and n[1:] in (
            "overview assistant files notes tasks settings plus trash save refresh send stop "
            "search edit open move folder_plus file_plus").split()}


def pixmap(name: str, color: str | QColor, size: int = 20, dpr: float = 2.0) -> QPixmap:
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size / 24, size / 24)
    p.setPen(QPen(QColor(color), 1.9, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    draw = DRAW.get(name)
    if draw:
        draw(p)
    p.end()
    return pm
