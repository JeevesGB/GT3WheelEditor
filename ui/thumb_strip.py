from __future__ import annotations
from pathlib import Path
from typing import Optional, List
from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QPen
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QScrollArea, QToolButton, QLabel, QSizePolicy, QVBoxLayout
)


class ThumbButton(QToolButton):
    def __init__(self, path: Path, pixmap: Optional[QPixmap], parent=None):
        super().__init__(parent)
        self.wheel_path = path
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setToolTip(path.name)
        self.setFixedSize(72, 72)
        self.setIconSize(QSize(64, 64))
        if pixmap is not None and not pixmap.isNull():
            self.setIcon(QIcon(pixmap))
        else:
            self.setText(path.name[:6])
        self.setStyleSheet(
            "QToolButton { background:#c0c0c0; border:2px solid; "
            "border-top-color:#dfdfdf; border-left-color:#dfdfdf; "
            "border-right-color:#808080; border-bottom-color:#808080; padding:2px; }"
            "QToolButton:checked { border-top-color:#808080; border-left-color:#808080; "
            "border-right-color:#ffffff; border-bottom-color:#ffffff; background:#a8a8c0; }"
            "QToolButton:hover { background:#d0d0d0; }"
        )


class ThumbStrip(QWidget):

    wheel_selected = pyqtSignal(object)  # Path

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(96)
        self.setMaximumHeight(110)
        self._thumbs_dir: Optional[Path] = None
        self._buttons: List[ThumbButton] = []
        self._cache: dict[str, QPixmap] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 2, 4, 2)
        root.setSpacing(2)

        self._header = QLabel("Wheels")
        self._header.setStyleSheet("font-weight: bold; font-size: 11px;")
        root.addWidget(self._header)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setFrameShape(QScrollArea.Shape.StyledPanel)
        self._scroll.setFixedHeight(84)

        self._inner = QWidget()
        self._row = QHBoxLayout(self._inner)
        self._row.setContentsMargins(4, 2, 4, 2)
        self._row.setSpacing(4)
        self._row.addStretch(1)
        self._scroll.setWidget(self._inner)
        root.addWidget(self._scroll)

    def set_thumbs_dir(self, path: Optional[Path]):
        self._thumbs_dir = Path(path) if path else None
        self._cache.clear()

    def _load_thumb(self, wheel_name: str) -> Optional[QPixmap]:
        if wheel_name in self._cache:
            return self._cache[wheel_name]
        if self._thumbs_dir is None:
            return None
        # try exact name.png, and a few variants
        candidates = [
            self._thumbs_dir / f"{wheel_name}.png",
            self._thumbs_dir / f"{wheel_name}.PNG",
        ]
        # bbw_011_s vs bbw011_s style mismatch
        if "_" not in wheel_name[3:6] and wheel_name.startswith("bbw"):
            pass
        for c in candidates:
            if c.is_file():
                pm = QPixmap(str(c))
                if not pm.isNull():
                    self._cache[wheel_name] = pm
                    return pm
        return None

    def set_wheels(self, files: List[Path], selected: Optional[Path] = None):
        # clear
        while self._row.count():
            item = self._row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._buttons.clear()

        if not files:
            self._header.setText("Wheels (none in folder)")
            self._row.addStretch(1)
            return

        self._header.setText(f"Wheels ({len(files)})")
        select_btn = None
        for p in files:
            pm = self._load_thumb(p.name)
            btn = ThumbButton(p, pm)
            btn.clicked.connect(lambda checked, path=p: self._emit(path))
            self._row.addWidget(btn)
            self._buttons.append(btn)
            if selected is not None and p.resolve() == Path(selected).resolve():
                select_btn = btn
        self._row.addStretch(1)

        if select_btn is not None:
            select_btn.setChecked(True)
            self._scroll.ensureWidgetVisible(select_btn)

    def _emit(self, path: Path):
        self.wheel_selected.emit(path)

    def select_path(self, path: Optional[Path]):
        if path is None:
            return
        target = Path(path).resolve()
        for btn in self._buttons:
            if btn.wheel_path.resolve() == target:
                btn.setChecked(True)
                self._scroll.ensureWidgetVisible(btn)
                break
