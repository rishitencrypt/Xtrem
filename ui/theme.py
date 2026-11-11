"""Palettes (pink / rose / mauve / lilac), the application stylesheet and the current theme."""
from __future__ import annotations

from dataclasses import dataclass
from string import Template


@dataclass(frozen=True)
class Palette:
    name: str
    bg1: str; bg2: str; bg3: str          # window gradient
    face: str; face_hi: str; inset: str   # panel face, hovered/top-lit face, recessed field
    light: str; shadow: str               # neumorphic highlight / shadow
    accent: str; accent2: str; on_accent: str
    text: str; muted: str; danger: str
    user: str; ai: str; tool: str; err: str


PALETTES = {
    "Rose": Palette("Rose", "#fbc3d9", "#e9b4e4", "#c5b3f1",
                    "#f0c8de", "#f8dcea", "#e5b2cf", "#fff1f8", "#a9709f",
                    "#c2185b", "#9c27b0", "#ffffff", "#2a0f2e", "#6a4271", "#9b1237",
                    "#f9d3e6", "#e7d6f7", "#f3e3ee", "#f7c4cd"),
    "Lilac": Palette("Lilac", "#d9c3f4", "#f1b9dc", "#fbc9d6",
                     "#ddc9f1", "#eadcf8", "#cdb4e8", "#f6edfd", "#8f74b8",
                     "#7a35b8", "#c2185b", "#ffffff", "#24103a", "#5c447d", "#9b1237",
                     "#ecd3f6", "#d6e2f8", "#e7dcf3", "#f7c4cd"),
}

_current = PALETTES["Rose"]


def current() -> Palette:
    return _current


def set_theme(name: str) -> Palette:
    global _current
    _current = PALETTES.get(name, PALETTES["Rose"])
    return _current


QSS = Template("""
QWidget#Root { background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
    stop:0 ${bg1}, stop:0.55 ${bg2}, stop:1 ${bg3}); }
QWidget { color: ${text}; font-size: 14px; }
QLabel { background: transparent; }
QLabel#Brand { font-size: 30px; font-weight: 900; letter-spacing: 1px; }
QLabel#Heading { font-size: 30px; font-weight: 800; }
QLabel#Title { font-size: 20px; font-weight: 800; }
QLabel#Section { font-size: 12px; font-weight: 800; color: ${muted}; letter-spacing: 1px; }
QLabel#Muted { color: ${muted}; }
QLabel#Chip { color: ${muted}; font-size: 12px; font-weight: 700; }
QLabel#BubbleRole { font-size: 11px; font-weight: 800; color: ${muted}; letter-spacing: 1px; }

QDialog, QMessageBox, QInputDialog { background: ${face}; }

QLineEdit, QPlainTextEdit, QTextEdit, QListWidget, QTreeView, QComboBox {
  background: transparent; border: none; padding: 2px 4px;
  selection-background-color: ${accent}; selection-color: ${on_accent}; }
QListWidget::item, QTreeView::item { padding: 6px 4px; border-radius: 8px; }
QListWidget::item:selected, QTreeView::item:selected { background: ${accent}; color: ${on_accent}; }
QListWidget::item:hover:!selected, QTreeView::item:hover:!selected { background: ${face_hi}; }
QComboBox QAbstractItemView { background: ${face_hi}; selection-background-color: ${accent};
  selection-color: ${on_accent}; border: 1px solid ${shadow}; }
QCheckBox { spacing: 8px; background: transparent; }

QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { width: 10px; background: transparent; margin: 2px; }
QScrollBar::handle:vertical { background: ${shadow}; border-radius: 4px; min-height: 36px; }
QScrollBar::handle:vertical:hover { background: ${accent}; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; width: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { height: 10px; background: transparent; margin: 2px; }
QScrollBar::handle:horizontal { background: ${shadow}; border-radius: 4px; min-width: 36px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; height: 0; }

QFrame#BubbleUser { background: ${user}; border-radius: 18px; border: 1px solid ${light}; }
QFrame#BubbleAI { background: ${ai}; border-radius: 18px; border: 1px solid ${light}; }
QFrame#BubbleTool { background: ${tool}; border-radius: 14px; border: 1px dashed ${shadow}; }
QFrame#BubbleError { background: ${err}; border-radius: 18px; border: 1px solid ${danger}; }
QTextBrowser#BubbleText { background: transparent; border: none; padding: 0;
  selection-background-color: ${accent}; selection-color: ${on_accent}; }
QToolTip { background: ${face_hi}; color: ${text}; border: 1px solid ${shadow}; padding: 4px; }
""")


def build_stylesheet(theme: str | None = None) -> str:
    p = set_theme(theme) if theme else _current
    return QSS.substitute({k: getattr(p, k) for k in p.__dataclass_fields__})
