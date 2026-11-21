from __future__ import annotations
from typing import List, Optional, Callable
from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtGui import QColor, QPainter, QBrush, QPen, QMouseEvent
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QColorDialog, QSpinBox, QFrame, QGridLayout
)

from formats.tex1 import ClutEntry


class PaletteSwatch(QWidget):
    clicked = pyqtSignal(int)

    def __init__(self, index: int, entry: ClutEntry, parent=None):
        super().__init__(parent)
        self.index = index
        self.entry = entry
        self.setFixedSize(28, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._selected = False

    def set_entry(self, entry: ClutEntry):
        self.entry = entry
        self.update()

    def set_selected(self, selected: bool):
        self._selected = selected
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        r, g, b, a = self.entry.to_rgba()
        color = QColor(r, g, b, a)
        if self._selected:
            p.fillRect(0, 0, 28, 28, QColor(0, 0, 128))
            p.fillRect(3, 3, 22, 22, color)
            p.setPen(QPen(QColor(255, 255, 255)))
            p.drawRect(3, 3, 21, 21)
        else:
            p.fillRect(1, 1, 26, 26, color)
            p.setPen(QPen(QColor(128, 128, 128)))
            p.drawLine(0, 0, 27, 0)
            p.drawLine(0, 0, 0, 27)
            p.setPen(QPen(QColor(255, 255, 255)))
            p.drawLine(27, 0, 27, 27)
            p.drawLine(0, 27, 27, 27)
        p.end()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.index)


class PaletteWidget(QWidget):
    palette_changed = pyqtSignal(list)  # List[ClutEntry]
    selection_changed = pyqtSignal(int)  # selected palette index

    def __init__(self, parent=None):
        super().__init__(parent)
        self.clut: List[ClutEntry] = []
        self.swatches: List[PaletteSwatch] = []
        self.selected_index = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self.title = QLabel("Palette")
        self.title.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.title)

        self.grid_frame = QFrame()
        self.grid_layout = QGridLayout(self.grid_frame)
        self.grid_layout.setSpacing(2)
        self.grid_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.grid_frame)

        self.btn_edit = QPushButton("Edit")
        self.btn_edit.setMinimumHeight(24)
        self.btn_edit.clicked.connect(self._edit_colour)
        layout.addWidget(self.btn_edit)

        spins = QGridLayout()
        spins.setHorizontalSpacing(6)
        spins.setVerticalSpacing(4)
        self.spin_r = QSpinBox()
        self.spin_g = QSpinBox()
        self.spin_b = QSpinBox()
        self.spin_a = QSpinBox()
        for i, (s, name) in enumerate((
            (self.spin_r, "R"),
            (self.spin_g, "G"),
            (self.spin_b, "B"),
            (self.spin_a, "A"),
        )):
            s.setRange(0, 255)
            s.setPrefix(f"{name}: ")
            s.setMinimumWidth(72)
            s.valueChanged.connect(self._spin_changed)
            spins.addWidget(s, i // 2, i % 2)
        layout.addLayout(spins)
        layout.addStretch()

    def set_clut(self, clut: List[ClutEntry]):
        self.clut = [ClutEntry(c.r, c.g, c.b, c.a) for c in clut]
        self._rebuild_grid()
        if self.clut:
            self.select(0)

    def _rebuild_grid(self):
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.swatches.clear()

        cols = 8
        for i, entry in enumerate(self.clut):
            sw = PaletteSwatch(i, entry)
            sw.clicked.connect(self.select)
            self.swatches.append(sw)
            self.grid_layout.addWidget(sw, i // cols, i % cols)

    def refresh_swatches(self):
        for i, sw in enumerate(self.swatches):
            if i < len(self.clut):
                sw.set_entry(self.clut[i])

    def select(self, index: int):
        if not (0 <= index < len(self.clut)):
            return
        self.selected_index = index
        for i, sw in enumerate(self.swatches):
            sw.set_selected(i == index)
        e = self.clut[index]
        for s in (self.spin_r, self.spin_g, self.spin_b, self.spin_a):
            s.blockSignals(True)
        self.spin_r.setValue(e.r)
        self.spin_g.setValue(e.g)
        self.spin_b.setValue(e.b)
        self.spin_a.setValue(e.a)
        for s in (self.spin_r, self.spin_g, self.spin_b, self.spin_a):
            s.blockSignals(False)
        self.selection_changed.emit(index)

    def _spin_changed(self):
        if not self.clut:
            return
        e = self.clut[self.selected_index]
        e.r = self.spin_r.value()
        e.g = self.spin_g.value()
        e.b = self.spin_b.value()
        e.a = self.spin_a.value()
        self.swatches[self.selected_index].set_entry(e)
        self.palette_changed.emit(self.clut)

    def _edit_colour(self):
        if not self.clut:
            return
        e = self.clut[self.selected_index]
        r, g, b, a = e.to_rgba()
        initial = QColor(r, g, b, a)
        colour = QColorDialog.getColor(initial, self, "Edit Palette Colour",
                                       QColorDialog.ColorDialogOption.ShowAlphaChannel)
        if colour.isValid():
            e.r = colour.red()
            e.g = colour.green()
            e.b = colour.blue()
            e.a = min(0x80, int(colour.alpha() * 0x80 / 255))
            self.swatches[self.selected_index].set_entry(e)
            self.select(self.selected_index)
            self.palette_changed.emit(self.clut)
