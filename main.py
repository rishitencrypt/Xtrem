"""Xtrem: a private, local desktop assistant (PySide6 + Ollama)."""
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from storage.db import Database, StorageError
from ui.context import AppContext
from ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Xtrem")
    try:
        db = Database()
    except StorageError as exc:
        QMessageBox.critical(None, "Xtrem", str(exc))
        return 1
    window = MainWindow(AppContext(db))
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
