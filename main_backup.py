import sys
import math
import threading
import time

import ollama

from PySide6.QtCore import Qt, QObject, Signal, QTimer
from PySide6.QtGui import (
    QFont, QPainter, QLinearGradient, QColor, QPen,
    QBrush, QRadialGradient
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QFrame, QLabel, QPushButton,
    QLineEdit, QVBoxLayout, QHBoxLayout, QMainWindow,
    QScrollArea, QSizePolicy, QGraphicsDropShadowEffect,
    QSlider
)

MODEL = "qwen3:4b"


# ---------- Background ----------

class GradientBackground(QWidget):
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        gradient = QLinearGradient(0, 0, self.width(), self.height())
        gradient.setColorAt(0.0, QColor("#D77BE5"))
        gradient.setColorAt(0.38, QColor("#F5B6E8"))
        gradient.setColorAt(0.72, QColor("#D0A8FF"))
        gradient.setColorAt(1.0, QColor("#F8C8EA"))

        painter.fillRect(self.rect(), gradient)


def add_shadow(widget, blur=24, x=8, y=9, color="#C27ABD"):
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(x, y)
    effect.setColor(QColor(color))
    widget.setGraphicsEffect(effect)


class SoftPanel(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("panel")
        add_shadow(self)


class NavButton(QPushButton):
    def __init__(self, text, active=False):
        super().__init__(text)
        self.setObjectName("navActive" if active else "nav")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(44)


# ---------- Animated orb ----------

class RotatingOrb(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.angle = 0.0
        self.speed = 1.0
        self.setMinimumSize(150, 150)
        self.setMaximumSize(210, 210)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.rotate)
        self.timer.start(30)

    def set_speed(self, value):
        self.speed = value / 100.0

    def rotate(self):
        self.angle = (self.angle + self.speed * 2.0) % 360
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        size = min(self.width(), self.height()) - 28
        cx = self.width() / 2
        cy = self.height() / 2
        radius = size / 2

        # Soft shadow beneath the orb
        shadow_gradient = QRadialGradient(cx, cy + 8, radius + 12)
        shadow_gradient.setColorAt(0.0, QColor(139, 54, 155, 100))
        shadow_gradient.setColorAt(1.0, QColor(139, 54, 155, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(shadow_gradient)
        painter.drawEllipse(
            int(cx - radius - 12),
            int(cy - radius - 4),
            int(size + 24),
            int(size + 24),
        )

        # Main orb
        gradient = QRadialGradient(
            cx - radius * 0.35,
            cy - radius * 0.4,
            radius * 1.8,
        )
        gradient.setColorAt(0.0, QColor("#FFF0FF"))
        gradient.setColorAt(0.35, QColor("#E9C1FA"))
        gradient.setColorAt(0.72, QColor("#B77CE6"))
        gradient.setColorAt(1.0, QColor("#853BC4"))

        painter.setBrush(QBrush(gradient))
        painter.setPen(QPen(QColor("#FFF0FF"), 2))
        painter.drawEllipse(
            int(cx - radius),
            int(cy - radius),
            int(size),
            int(size),
        )

        # Rotating elliptical rings
        painter.save()
        painter.translate(cx, cy)
        painter.rotate(self.angle)

        ring_pen = QPen(QColor("#FFFFFF"), 2.5)
        ring_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(ring_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(
            int(-radius * 0.78),
            int(-radius * 0.35),
            int(radius * 1.56),
            int(radius * 0.70),
        )

        painter.rotate(65)
        painter.setPen(QPen(QColor("#F9C8FF"), 2))
        painter.drawEllipse(
            int(-radius * 0.72),
            int(-radius * 0.31),
            int(radius * 1.44),
            int(radius * 0.62),
        )

        # Small orbiting point
        orbit_x = radius * 0.78
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawEllipse(
            int(orbit_x - 5), -5, 10, 10
        )

        painter.restore()

        # Center mark
        painter.setPen(QPen(QColor("#FFFFFF"), 2))
        painter.setFont(QFont("Avenir Next", 23, QFont.Weight.Bold))
        painter.drawText(
            self.rect(),
            Qt.AlignmentFlag.AlignCenter,
            "✦",
        )


# ---------- Worker signals ----------

class WorkerSignals(QObject):
    response_ready = Signal(str, str)
    finished = Signal()


# ---------- Main window ----------

class XtremWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Xtrem")
        self.resize(1380, 860)
        self.setMinimumSize(1000, 680)

        self.conversation = []
        self.busy = False
        self.request_id = 0
        self.current_thread = None

        self.signals = WorkerSignals()
        self.signals.response_ready.connect(self.show_response)
        self.signals.finished.connect(self.finish_generation)

        self.setStyleSheet("""
            QMainWindow {
                background: #EFA9E5;
            }

            QWidget {
                color: #54204F;
                font-family: "Avenir Next", "Helvetica Neue", sans-serif;
                font-size: 13px;
            }

            QFrame#sidebar {
                background: #EEC0E9;
                border: 1px solid #FFE6FC;
                border-radius: 24px;
            }

            QFrame#panel {
                background: #F1C1EC;
                border: 1px solid #FFF0FF;
                border-radius: 21px;
            }

            QLabel#brand {
                font-size: 29px;
                font-weight: 800;
                letter-spacing: 5px;
                color: #772A72;
            }

            QLabel#eyebrow {
                color: #8C4E85;
                font-size: 10px;
                font-weight: 800;
                letter-spacing: 1.2px;
            }

            QLabel#title {
                color: #54204F;
                font-size: 29px;
                font-weight: 800;
            }

            QLabel#subtitle {
                color: #795174;
                font-size: 13px;
            }

            QLabel#section {
                color: #682B65;
                font-size: 16px;
                font-weight: 800;
            }

            QLabel#itemTitle {
                color: #61265D;
                font-size: 13px;
                font-weight: 800;
            }

            QLabel#itemDetail {
                color: #795174;
                font-size: 11px;
            }

            QLabel#pill {
                background: #D8EEDB;
                color: #477C58;
                border-radius: 10px;
                padding: 8px 11px;
                font-size: 10px;
                font-weight: 800;
            }

            QPushButton#nav {
                background: transparent;
                color: #80527B;
                text-align: left;
                padding: 0 14px;
                border: 1px solid transparent;
                border-radius: 13px;
                font-weight: 600;
            }

            QPushButton#nav:hover {
                background: #F5D2F1;
                border: 1px solid #FFE8FC;
            }

            QPushButton#navActive {
                background: #F4C5EE;
                color: #71276A;
                text-align: left;
                padding: 0 14px;
                border: 1px solid #FFF0FF;
                border-radius: 13px;
                font-weight: 800;
            }

            QPushButton#primary {
                background: #A52ACB;
                color: white;
                border: 1px solid #E9A7F4;
                border-radius: 13px;
                padding: 12px 17px;
                font-weight: 800;
            }

            QPushButton#primary:hover {
                background: #9120B7;
            }

            QPushButton#primary:pressed {
                background: #771895;
                padding-top: 13px;
            }

            QPushButton#primary:disabled {
                background: #BDA1C7;
                color: #F7EDF8;
            }

            QPushButton#secondary {
                background: #E9B9E7;
                color: #702A70;
                border: 1px solid #FFF0FF;
                border-radius: 12px;
                padding: 9px 13px;
                font-weight: 700;
            }

            QPushButton#secondary:hover {
                background: #F4CFF0;
            }

            QLineEdit {
                background: #E6B1E0;
                color: #54204F;
                border: 1px solid #F9DDF5;
                border-radius: 13px;
                padding: 13px 15px;
                font-weight: 600;
                selection-background-color: #B63BD1;
            }

            QLineEdit:focus {
                border: 1px solid #A52ACB;
            }

            QScrollArea {
                background: transparent;
                border: none;
            }

            QWidget#chatContents {
                background: transparent;
            }

            QFrame#messageUser {
                background: #E5B0E2;
                border: 1px solid #FBE0F7;
                border-radius: 15px;
            }

            QFrame#messageAI {
                background: #F7D8F2;
                border: 1px solid #FFF0FF;
                border-radius: 15px;
            }

            QLabel#messageText {
                color: #54204F;
                font-size: 13px;
                font-weight: 600;
            }

            QLabel#messageRole {
                color: #874A80;
                font-size: 10px;
                font-weight: 800;
                letter-spacing: 1px;
            }

            QSlider::groove:horizontal {
                height: 7px;
                background: #DFA8D9;
                border-radius: 3px;
            }

            QSlider::sub-page:horizontal {
                background: #A52ACB;
                border-radius: 3px;
            }

            QSlider::handle:horizontal {
                background: #FFFFFF;
                border: 2px solid #A52ACB;
                width: 16px;
                margin: -6px 0;
                border-radius: 9px;
            }

            QStatusBar {
                background: #EFB6E8;
                color: #754B70;
                border: none;
            }
        """)

        root = GradientBackground()
        self.setCentralWidget(root)

        outer = QHBoxLayout(root)
        outer.setContentsMargins(22, 20, 22, 16)
        outer.setSpacing(20)

        # Sidebar
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(218)

        side = QVBoxLayout(sidebar)
        side.setContentsMargins(17, 23, 17, 19)
        side.setSpacing(7)

        brand = QLabel("XTREM")
        brand.setObjectName("brand")

        tagline = QLabel("PRIVATE DESKTOP")
        tagline.setObjectName("eyebrow")

        side.addWidget(brand)
        side.addWidget(tagline)
        side.addSpacing(25)

        section = QLabel("WORKSPACE")
        section.setObjectName("eyebrow")
        section.setContentsMargins(12, 0, 0, 5)
        side.addWidget(section)

        for name, active in [
            ("⌂   Overview", True),
            ("◌   Assistant", False),
            ("▤   File studio", False),
            ("✎   Notes", False),
            ("✓   Tasks", False),
        ]:
            side.addWidget(NavButton(name, active))

        side.addSpacing(18)

        section = QLabel("APP")
        section.setObjectName("eyebrow")
        section.setContentsMargins(12, 0, 0, 5)
        side.addWidget(section)
        side.addWidget(NavButton("⚙   Settings"))
        side.addStretch()

        privacy = SoftPanel()
        privacy_layout = QVBoxLayout(privacy)
        privacy_layout.setContentsMargins(13, 13, 13, 13)
        privacy_layout.setSpacing(7)

        privacy_title = QLabel("LOCAL BY DEFAULT")
        privacy_title.setObjectName("eyebrow")

        privacy_text = QLabel(
            "Qwen runs on this Mac.\n"
            "Your data stays local."
        )
        privacy_text.setObjectName("itemDetail")
        privacy_text.setWordWrap(True)

        privacy_layout.addWidget(privacy_title)
        privacy_layout.addWidget(privacy_text)
        side.addWidget(privacy)

        # Main area
        main = QWidget()
        main.setStyleSheet("background: transparent;")

        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(2, 2, 2, 0)
        main_layout.setSpacing(15)

        header = QHBoxLayout()
        header_text = QVBoxLayout()
        header_text.setSpacing(4)

        eyebrow = QLabel("YOUR PRIVATE WORKSPACE")
        eyebrow.setObjectName("eyebrow")

        title = QLabel("Xtrem Assistant")
        title.setObjectName("title")

        subtitle = QLabel("Local AI. Your machine. Your files.")
        subtitle.setObjectName("subtitle")

        header_text.addWidget(eyebrow)
        header_text.addWidget(title)
        header_text.addWidget(subtitle)

        header.addLayout(header_text)
        header.addStretch()

        pill = QLabel("●  QWEN 3 · LOCAL")
        pill.setObjectName("pill")
        pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(pill, 0, Qt.AlignmentFlag.AlignTop)

        main_layout.addLayout(header)

        # Chat panel
        chat_panel = SoftPanel()
        chat_layout = QVBoxLayout(chat_panel)
        chat_layout.setContentsMargins(18, 17, 18, 16)
        chat_layout.setSpacing(10)

        chat_heading = QHBoxLayout()
        chat_label = QLabel("CHAT")
        chat_label.setObjectName("eyebrow")
        chat_heading.addWidget(chat_label)
        chat_heading.addStretch()

        self.clear_button = QPushButton("New chat")
        self.clear_button.setObjectName("secondary")
        self.clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_button.clicked.connect(self.new_chat)
        chat_heading.addWidget(self.clear_button)
        chat_layout.addLayout(chat_heading)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.scroll.setMinimumHeight(260)

        self.chat_contents = QWidget()
        self.chat_contents.setObjectName("chatContents")

        self.chat_layout = QVBoxLayout(self.chat_contents)
        self.chat_layout.setContentsMargins(6, 6, 6, 6)
        self.chat_layout.setSpacing(13)
        self.chat_layout.addStretch()

        self.scroll.setWidget(self.chat_contents)
        chat_layout.addWidget(self.scroll, 1)

        self.placeholder = QLabel(
            "Ask a question or give Qwen a task."
        )
        self.placeholder.setObjectName("subtitle")
        self.placeholder.setWordWrap(True)
        self.chat_layout.insertWidget(0, self.placeholder)

        input_row = QHBoxLayout()
        input_row.setSpacing(9)

        self.prompt = QLineEdit()
        self.prompt.setPlaceholderText("Message Xtrem…")
        self.prompt.setMinimumHeight(46)
        self.prompt.returnPressed.connect(self.prepare_prompt)

        self.send_button = QPushButton("Send  ↑")
        self.send_button.setObjectName("primary")
        self.send_button.setMinimumHeight(44)
        self.send_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_button.clicked.connect(self.prepare_prompt)

        input_row.addWidget(self.prompt, 1)
        input_row.addWidget(self.send_button)
        chat_layout.addLayout(input_row)

        main_layout.addWidget(chat_panel, 5)

        # Fun rotating orb panel
        orb_panel = SoftPanel()
        orb_layout = QHBoxLayout(orb_panel)
        orb_layout.setContentsMargins(18, 12, 18, 12)
        orb_layout.setSpacing(18)

        self.orb = RotatingOrb()
        orb_layout.addWidget(self.orb, 0, Qt.AlignmentFlag.AlignCenter)

        orb_info = QVBoxLayout()
        orb_info.setSpacing(7)

        orb_title = QLabel("ROTATION LAB")
        orb_title.setObjectName("eyebrow")

        orb_description = QLabel("Adjust the orb's spin.")
        orb_description.setObjectName("itemTitle")

        speed_row = QHBoxLayout()
        speed_label = QLabel("Speed")
        speed_label.setObjectName("itemDetail")

        self.speed_value = QLabel("50%")
        self.speed_value.setObjectName("itemTitle")

        speed_row.addWidget(speed_label)
        speed_row.addStretch()
        speed_row.addWidget(self.speed_value)

        self.speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.speed_slider.setRange(0, 200)
        self.speed_slider.setValue(50)
        self.speed_slider.setMinimumWidth(180)
        self.speed_slider.valueChanged.connect(self.change_orb_speed)

        orb_info.addWidget(orb_title)
        orb_info.addWidget(orb_description)
        orb_info.addLayout(speed_row)
        orb_info.addWidget(self.speed_slider)

        orb_layout.addLayout(orb_info, 1)
        main_layout.addWidget(orb_panel)

        # Local status
        status_panel = SoftPanel()
        status_layout = QHBoxLayout(status_panel)
        status_layout.setContentsMargins(14, 10, 14, 10)
        status_layout.setSpacing(14)

        local = QLabel("LOCAL")
        local.setObjectName("eyebrow")
        status_layout.addWidget(local)

        model = QLabel("●  qwen3:4b")
        model.setStyleSheet("color: #477C58; font-weight: 800;")
        status_layout.addWidget(model)

        status_layout.addStretch()

        privacy_status = QLabel("No cloud API")
        privacy_status.setObjectName("itemDetail")
        status_layout.addWidget(privacy_status)

        main_layout.addWidget(status_panel)

        outer.addWidget(sidebar)
        outer.addWidget(main, 1)

        self.statusBar().showMessage("Ready · qwen3:4b")
        self.add_message(
            "Xtrem",
            "Ready. What do you need?",
            is_user=False,
        )

    # ---------- Chat ----------

    def change_orb_speed(self, value):
        self.orb.set_speed(value)
        self.speed_value.setText(f"{value}%")

    def add_message(self, role, text, is_user):
        if hasattr(self, "placeholder") and self.placeholder is not None:
            self.placeholder.hide()

        bubble = QFrame()
        bubble.setObjectName(
            "messageUser" if is_user else "messageAI"
        )
        bubble.setSizePolicy(
            QSizePolicy.Policy.Maximum,
            QSizePolicy.Policy.Minimum,
        )
        bubble.setMaximumWidth(760)

        layout = QVBoxLayout(bubble)
        layout.setContentsMargins(15, 12, 15, 12)
        layout.setSpacing(6)

        role_label = QLabel(role.upper())
        role_label.setObjectName("messageRole")

        body = QLabel(text)
        body.setObjectName("messageText")
        body.setWordWrap(True)
        body.setMinimumWidth(0)
        body.setMaximumWidth(710)
        body.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Minimum,
        )
        body.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        layout.addWidget(role_label)
        layout.addWidget(body)

        alignment = (
            Qt.AlignmentFlag.AlignRight
            if is_user
            else Qt.AlignmentFlag.AlignLeft
        )

        self.chat_layout.insertWidget(
            self.chat_layout.count() - 1,
            bubble,
            0,
            alignment,
        )

        QTimer.singleShot(
            0,
            lambda: self.scroll.verticalScrollBar().setValue(
                self.scroll.verticalScrollBar().maximum()
            ),
        )

    def prepare_prompt(self):
        if self.busy:
            return

        message = self.prompt.text().strip()
        if not message:
            return

        self.prompt.clear()
        self.add_message("You", message, True)

        self.conversation.append({
            "role": "user",
            "content": message,
        })

        # Keep recent context short to reduce prompt processing time.
        self.conversation = self.conversation[-8:]

        self.busy = True
        self.send_button.setEnabled(False)
        self.send_button.setText("Thinking…")
        self.statusBar().showMessage("Qwen is working…")

        self.request_id += 1
        request_id = self.request_id
        messages = list(self.conversation)

        self.current_thread = threading.Thread(
            target=self.ask_model,
            args=(messages, request_id),
            daemon=True,
        )
        self.current_thread.start()

    def ask_model(self, messages, request_id):
        try:
            response = ollama.chat(
                model=MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are Xtrem, a local desktop assistant. "
                            "Be direct and useful. Prefer short answers. "
                            "Avoid filler, repeated summaries, and unnecessary headings. "
                            "Never claim you changed files or ran commands unless "
                            "an implemented tool actually did so."
                        ),
                    },
                    *messages,
                ],
                options={
                    "num_predict": 256,
                    "temperature": 0.5,
                },
            )

            answer = response["message"]["content"]
            self.signals.response_ready.emit(answer, "")

        except Exception as error:
            self.signals.response_ready.emit(
                "",
                f"Could not reach Ollama: {error}",
            )

        finally:
            self.signals.finished.emit()

    def show_response(self, answer, error):
        if error:
            self.add_message("Error", error, False)
            self.statusBar().showMessage("Request failed")
        else:
            self.add_message("Xtrem", answer, False)

            # Keep assistant responses in history for context.
            self.conversation.append({
                "role": "assistant",
                "content": answer,
            })
            self.conversation = self.conversation[-8:]
            self.statusBar().showMessage("Response received")

    def finish_generation(self):
        self.busy = False
        self.send_button.setEnabled(True)
        self.send_button.setText("Send  ↑")
        self.prompt.setFocus()

    def new_chat(self):
        if self.busy:
            self.statusBar().showMessage(
                "Wait for the current response before starting a new chat."
            )
            return

        self.conversation.clear()

        while self.chat_layout.count():
            item = self.chat_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        self.placeholder = QLabel("Ask a question or give Qwen a task.")
        self.placeholder.setObjectName("subtitle")
        self.placeholder.setWordWrap(True)
        self.chat_layout.insertWidget(0, self.placeholder)
        self.chat_layout.addStretch()

        self.add_message("Xtrem", "New chat. Ready.", False)
        self.statusBar().showMessage("New chat started")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Avenir Next", 10))

    window = XtremWindow()
    window.show()

    sys.exit(app.exec())