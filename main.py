#!/usr/bin/env python3
"""GT3 Wheel Painter – entry point."""

import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont, QIcon
from ui.main_window import MainWindow


def load_stylesheet(app: QApplication) -> None:
    qss_path = Path(__file__).resolve().parent / "ui" / "win98.qss"
    if qss_path.is_file():
        icons = (qss_path.parent / "icons").as_posix()
        qss = qss_path.read_text(encoding="utf-8").replace("@ICONS@", icons)
        app.setStyleSheet(qss)


def main():
    # Own taskbar identity on Windows so the custom icon is shown (not python's)
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("JeevesGB.GT3WheelPainter")
        except Exception:
            pass

    app = QApplication(sys.argv)
    icon_path = Path(__file__).resolve().parent / "assets" / "icon.ico"
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))
    app.setApplicationName("GT3 Wheel Painter")
    app.setOrganizationName("JeevesGB")
    app.setStyle("Windows")  # flat classic base (Fusion paints gradients); QSS overlays Win98 look

    # Prefer a classic-looking system font
    font = QFont("MS Sans Serif", 8)
    if not font.exactMatch():
        font = QFont("Tahoma", 8)
    if not font.exactMatch():
        font = QFont("Segoe UI", 9)
    app.setFont(font)

    load_stylesheet(app)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
