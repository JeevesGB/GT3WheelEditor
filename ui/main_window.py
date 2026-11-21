from __future__ import annotations
from pathlib import Path
import shutil
from typing import Optional
from PyQt6.QtCore import Qt, QSettings
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QFileDialog, QMessageBox, QLabel, QStatusBar, QToolBar,
    QGroupBox, QFrame, QSizePolicy, QListWidget, QListWidgetItem
)
from formats.gttw import GTTW
from formats.tex1 import ClutEntry
from formats.mesh import decode_gtm1_mesh
from formats.recolor import recolor_gtm1_hue, hue_shift_entry
import colorsys
from formats.texture_io import (
    export_texture_png, replace_texture_from_image, replace_all_textures_from_image,
)
from formats.gs_memory import (
    decode_tex1_image, extract_tex1_palette, apply_palette_to_tex1,
    decode_tex1_indices, make_highlight_texture,
)
from ui.palette_widget import PaletteWidget
from ui.gl_widget import GLWidget


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GT3 Wheel Painter")
        self.resize(1024, 680)
        self.setMinimumSize(800, 520)

        self.gttw: Optional[GTTW] = None
        self.current_path: Optional[Path] = None
        self._settings = QSettings("GT3WheelPainter", "GT3WheelPainter")
        self._folder_files: list[Path] = []
        self._tex_palette = None  # Tex1Palette for active rim texture
        self._tex_ref_index = 0   # which texture_sets[] entry holds the rim
        self._tex_rgba = None     # base decoded texture (no highlight)
        self._tex_indices = None  # HxW palette indices
        self._selected_index = 0
        self._highlight_on = False

        self._build_ui()
        self._build_menu()
        self._build_toolbar()

        self.status = QStatusBar()
        self.status.setSizeGripEnabled(True)
        self.setStatusBar(self.status)
        self._status_file = QLabel("No file")
        self._status_file.setMinimumWidth(120)
        self._status_mesh = QLabel("Mesh: —")
        self._status_mesh.setMinimumWidth(140)
        self._status_hint = QLabel("Open a GTTW wheel file to begin")
        self._status_hint.setMinimumWidth(180)
        self.status.addWidget(self._status_file, 1)
        self.status.addPermanentWidget(self._status_mesh)
        self.status.addPermanentWidget(self._status_hint)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        view_box = QGroupBox("Preview")
        view_layout = QVBoxLayout(view_box)
        view_layout.setContentsMargins(6, 14, 6, 6)

        view_frame = QFrame()
        view_frame.setObjectName("sunkenPanel")
        view_frame.setFrameShape(QFrame.Shape.NoFrame)
        vf_layout = QVBoxLayout(view_frame)
        vf_layout.setContentsMargins(2, 2, 2, 2)

        self.gl_view = GLWidget()
        self.gl_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        vf_layout.addWidget(self.gl_view)
        view_layout.addWidget(view_frame)

        hint = QLabel("Drag: orbit   Scroll: zoom   W: wireframe   R: rotate")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        view_layout.addWidget(hint)

        splitter.addWidget(view_box)

        right = QWidget()
        right.setMinimumWidth(260)
        right.setMaximumWidth(340)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)

        info_box = QGroupBox("File Info")
        info_layout = QVBoxLayout(info_box)
        info_layout.setContentsMargins(6, 14, 6, 6)
        self.info_label = QLabel("No file loaded.\n\nUse File → Open or the toolbar.")
        self.info_label.setObjectName("infoLabel")
        self.info_label.setWordWrap(True)
        self.info_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.info_label.setMinimumHeight(110)
        info_layout.addWidget(self.info_label)
        right_layout.addWidget(info_box)

        wheels_box = QGroupBox("Wheels in folder")
        wheels_layout = QVBoxLayout(wheels_box)
        wheels_layout.setContentsMargins(4, 12, 4, 4)
        self.wheel_list = QListWidget()
        self.wheel_list.setMinimumHeight(120)
        self.wheel_list.setMaximumHeight(200)
        self.wheel_list.itemActivated.connect(self._on_wheel_item_activated)
        self.wheel_list.itemClicked.connect(self._on_wheel_item_activated)
        wheels_layout.addWidget(self.wheel_list)
        right_layout.addWidget(wheels_box)

        pal_box = QGroupBox("Palette")
        pal_layout = QVBoxLayout(pal_box)
        pal_layout.setContentsMargins(6, 14, 6, 6)
        self.palette = PaletteWidget()
        self.palette.palette_changed.connect(self._on_palette_changed)
        self.palette.selection_changed.connect(self._on_palette_selected)
        if hasattr(self.palette, "title"):
            self.palette.title.hide()
        pal_layout.addWidget(self.palette)

        from PyQt6.QtWidgets import QCheckBox
        self.chk_highlight = QCheckBox("Show selected on wheel")
        self.chk_highlight.setChecked(False)
        self.chk_highlight.setToolTip(
            "Paint texels that use the selected palette entry bright magenta "
            "so you can see exactly where that colour is used on the rim."
        )
        self.chk_highlight.toggled.connect(self._on_highlight_toggled)
        pal_layout.addWidget(self.chk_highlight)

        self.chk_whole = QCheckBox("Recolour whole wheel")
        self.chk_whole.setChecked(True)
        self.chk_whole.setToolTip(
            "On: editing a colour also moves every other colour, in every texture "
            "of the wheel, to the same hue. Off: only the selected entry changes."
        )
        pal_layout.addWidget(self.chk_whole)

        right_layout.addWidget(pal_box, 1)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([720, 280])
        layout.addWidget(splitter, 1)

    def _build_menu(self):
        menubar = self.menuBar()

        file_menu = menubar.addMenu("&File")

        open_act = QAction("&Open...", self)
        open_act.setShortcut(QKeySequence.StandardKey.Open)
        open_act.triggered.connect(self.open_file)
        file_menu.addAction(open_act)

        save_act = QAction("&Save", self)
        save_act.setShortcut(QKeySequence.StandardKey.Save)
        save_act.triggered.connect(self.save_file)
        file_menu.addAction(save_act)

        save_as_act = QAction("Save &As...", self)
        save_as_act.setShortcut(QKeySequence.StandardKey.SaveAs)
        save_as_act.triggered.connect(self.save_file_as)
        file_menu.addAction(save_as_act)

        file_menu.addSeparator()

        quit_act = QAction("E&xit", self)
        quit_act.setShortcut(QKeySequence("Alt+F4"))
        quit_act.triggered.connect(self.close)
        file_menu.addAction(quit_act)

        view_menu = menubar.addMenu("&View")
        reset_cam = QAction("&Reset Camera", self)
        reset_cam.triggered.connect(self._reset_camera)
        view_menu.addAction(reset_cam)

        wire_act = QAction("&Wireframe", self)
        wire_act.setShortcut(QKeySequence("W"))
        wire_act.triggered.connect(self._toggle_wireframe)
        view_menu.addAction(wire_act)


        tex_menu = menubar.addMenu("&Texture")
        exp_act = QAction("&Export PNG...", self)
        exp_act.setToolTip("Save the current rim texture as a PNG image")
        exp_act.triggered.connect(self.export_texture)
        tex_menu.addAction(exp_act)

        rep_act = QAction("&Replace from PNG...", self)
        rep_act.setToolTip("Quantize an image to 16 colours and write it into the wheel texture")
        rep_act.triggered.connect(self.replace_texture)
        tex_menu.addAction(rep_act)

        rep_keep = QAction("Replace keeping &palette...", self)
        rep_keep.setToolTip("Map a new image onto the existing 16-colour palette (no new colours)")
        rep_keep.triggered.connect(lambda: self.replace_texture(keep_palette=True))
        tex_menu.addAction(rep_keep)

        help_menu = menubar.addMenu("&Help")
        about_act = QAction("&About GT3 Wheel Painter...", self)
        about_act.triggered.connect(self._about)
        help_menu.addAction(about_act)

    def _build_toolbar(self):
        tb = QToolBar("Standard")
        tb.setMovable(False)
        tb.setFloatable(False)
        self.addToolBar(tb)

        open_btn = QAction("Open", self)
        open_btn.setToolTip("Open a GTTW wheel file")
        open_btn.triggered.connect(self.open_file)
        tb.addAction(open_btn)

        prev_btn = QAction("◀ Prev", self)
        prev_btn.setToolTip("Previous wheel in folder (Ctrl+[)")
        prev_btn.setShortcut(QKeySequence("Ctrl+["))
        prev_btn.triggered.connect(lambda: self._step_wheel(-1))
        tb.addAction(prev_btn)

        next_btn = QAction("Next ▶", self)
        next_btn.setToolTip("Next wheel in folder (Ctrl+])")
        next_btn.setShortcut(QKeySequence("Ctrl+]"))
        next_btn.triggered.connect(lambda: self._step_wheel(1))
        tb.addAction(next_btn)

        save_btn = QAction("Save", self)
        save_btn.setToolTip("Save the current wheel")
        save_btn.triggered.connect(self.save_file)
        tb.addAction(save_btn)

        tb.addSeparator()

        reset_btn = QAction("Reset View", self)
        reset_btn.setToolTip("Reset camera")
        reset_btn.triggered.connect(self._reset_camera)
        tb.addAction(reset_btn)

    def open_file(self):
        start = self._settings.value("last_dir", "", str)
        if not start or not Path(start).is_dir():
            start = str(Path.cwd())
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open GT3 Wheel",
            start,
            "All Files (*.*);;GTTW Wheels (*.gttw)"
        )
        if not path:
            return
        self._load_path(Path(path))

    def _load_path(self, path: Path):
        try:
            gttw = GTTW.from_file(path)
        except Exception as e:
            QMessageBox.critical(self, "GT3 Wheel Painter", f"Failed to open file:\n\n{e}")
            return

        self.gttw = gttw
        self.current_path = path
        self.setWindowTitle(f"GT3 Wheel Painter - {path.name}")

        mesh = decode_gtm1_mesh(bytes(gttw.gtm1.raw))
        self.gl_view.set_mesh(mesh)

        clut: list[ClutEntry] = []
        tex_img = None
        self._tex_palette = None
        self._tex_ref_index = 0
        if gttw.gtm1.texture_sets:
            ref = gttw.gtm1.texture_sets[0]
            self._tex_ref_index = 0
            raw = bytes(ref.tex1.raw)
            self._tex_palette = extract_tex1_palette(raw, 0)
            if self._tex_palette:
                for r, g, b, a in self._tex_palette.entries:
                    clut.append(ClutEntry(r, g, b, a))
            self._tex_rgba = decode_tex1_image(raw, 0)
            self._tex_indices = decode_tex1_indices(raw, 0)
        else:
            self._tex_rgba = None
            self._tex_indices = None
        self.palette.set_clut(clut)
        self.gl_view.set_palette(clut)
        self._selected_index = 0
        self._apply_viewport_highlight()

        mesh_line = "—"
        if mesh:
            mesh_line = f"{mesh.vertex_count} verts, {mesh.triangle_count} tris"
        tex_line = "none"
        if self._tex_rgba is not None:
            tex_line = f"{self._tex_rgba.shape[1]}×{self._tex_rgba.shape[0]}"

        info = (
            f"<b>{path.name}</b><br><br>"
            f"Flags: 0x{gttw.flags:X}<br>"
            f"Nodes: {gttw.gtm1.node_count}<br>"
            f"Chunks: {gttw.gtm1.chunk_count}<br>"
            f"Texture sets: {gttw.gtm1.texture_set_count}<br>"
            f"Palette: {len(clut)} colours<br>"
            f"Mesh: {mesh_line}<br>"
            f"Texture: {tex_line}"
        )
        self.info_label.setText(info)
        self._status_file.setText(path.name)
        self._status_mesh.setText(f"Mesh: {mesh_line}")
        self._status_hint.setText("Loaded")
        self._settings.setValue("last_dir", str(path.parent))
        self._refresh_wheel_list(path.parent, select=path)

    def _on_palette_changed(self, clut: list[ClutEntry]):
        if self.gttw is None or self._tex_palette is None:
            self.gl_view.set_palette(clut)
            return
        if not self.gttw.gtm1.texture_sets:
            self.gl_view.set_palette(clut)
            return

        edited = self.palette.selected_index
        whole = self.chk_whole.isChecked()
        target_hue = None
        if whole and 0 <= edited < len(clut):
            e = clut[edited]
            h, s_, v_ = colorsys.rgb_to_hsv(e.r / 255.0, e.g / 255.0, e.b / 255.0)
            if s_ >= 0.12:
                target_hue = h
        if target_hue is not None:
            for i, c in enumerate(clut):
                if i == edited:
                    continue
                r, g, b, a = hue_shift_entry((c.r, c.g, c.b, c.a), target_hue)
                c.r, c.g, c.b = r, g, b
            self.palette.refresh_swatches()
        self.gl_view.set_palette(clut)

        for i, entry in enumerate(clut):
            if i < len(self._tex_palette.entries):
                self._tex_palette.entries[i] = (entry.r, entry.g, entry.b, entry.a)

        ref = self.gttw.gtm1.texture_sets[self._tex_ref_index]
        raw = bytearray(ref.tex1.raw)
        apply_palette_to_tex1(raw, self._tex_palette)
        ref.tex1.raw = raw
        try:
            self.gttw.gtm1.patch_tex1(ref, bytes(raw))
        except ValueError:
            pass

        if target_hue is not None:
            try:
                e = clut[edited]
                recolor_gtm1_hue(self.gttw.gtm1, (e.r, e.g, e.b),
                                 skip=(self._tex_ref_index, 0))
            except Exception as ex:
                self._status_hint.setText(f"Recolour of other textures failed: {ex}")

        raw = bytearray(self.gttw.gtm1.texture_sets[self._tex_ref_index].tex1.raw)
        self._tex_rgba = decode_tex1_image(bytes(raw), 0)
        self._tex_indices = decode_tex1_indices(bytes(raw), 0)
        self._apply_viewport_highlight()

    def save_file(self):
        if self.gttw is None or self.current_path is None:
            self.save_file_as()
            return
        if not self._offer_backup("before saving"):
            return
        try:
            self.gttw.save(self.current_path)
            self._set_status(str(self.current_path.name), "Saved")
        except Exception as e:
            QMessageBox.critical(self, "GT3 Wheel Painter", str(e))

    def save_file_as(self):
        if self.gttw is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save GTTW",
            str(self.current_path) if self.current_path else "",
            "All Files (*.*)"
        )
        if not path:
            return
        try:
            self.gttw.save(path)
            self.current_path = Path(path)
            self.setWindowTitle(f"GT3 Wheel Painter - {self.current_path.name}")
            self._status_file.setText(str(self.current_path.name))
            self._set_status(str(self.current_path.name), "Saved")
        except Exception as e:
            QMessageBox.critical(self, "GT3 Wheel Painter", str(e))

    def _reset_camera(self):
        self.gl_view.rot_x = 18.0
        self.gl_view.rot_y = -30.0
        self.gl_view.zoom = -5.0
        self.gl_view.auto_rotate = False
        self.gl_view.update()

    def _toggle_wireframe(self):
        self.gl_view.show_wireframe = not self.gl_view.show_wireframe
        self.gl_view.update()

    def _on_highlight_toggled(self, checked: bool):
        self._highlight_on = bool(checked)
        self._apply_viewport_highlight()

    def _on_palette_selected(self, index: int):
        self._selected_index = index
        self._apply_viewport_highlight()
        if self._tex_indices is not None:
            count = int((self._tex_indices == index).sum())
            self._status_hint.setText(f"Index {index}: {count} texels")
        else:
            self._status_hint.setText(f"Index {index}")

    def _apply_viewport_highlight(self):
        if self._tex_rgba is None:
            self.gl_view.set_texture(None)
            return
        if self._tex_indices is not None and self._highlight_on:
            img = make_highlight_texture(
                self._tex_rgba, self._tex_indices, self._selected_index, flash_on=True
            )
        else:
            img = self._tex_rgba
        self.gl_view.set_texture(img)



    def _offer_backup(self, reason: str = "before changing the file") -> bool:
        if self.current_path is None or not self.current_path.is_file():
            return True
        box = QMessageBox(self)
        box.setWindowTitle("GT3 Wheel Painter")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(f"Create a backup {reason}?")
        box.setInformativeText(
            f"Original:\n{self.current_path}\n\n"
            f"Backup will be saved as:\n{self.current_path.with_suffix(self.current_path.suffix + '.bak')}"
        )
        yes = box.addButton("Create backup", QMessageBox.ButtonRole.YesRole)
        no = box.addButton("Don't backup", QMessageBox.ButtonRole.NoRole)
        cancel = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(yes)
        box.exec()
        clicked = box.clickedButton()
        if clicked is cancel:
            return False
        if clicked is yes:
            bak = self.current_path.with_suffix(self.current_path.suffix + ".bak")
            if self.current_path.suffix == "":
                bak = Path(str(self.current_path) + ".bak")
            try:
                shutil.copy2(self.current_path, bak)
                self._status_hint.setText(f"Backup: {bak.name}")
            except Exception as e:
                QMessageBox.warning(
                    self,
                    "GT3 Wheel Painter",
                    f"Could not create backup:\n{e}\n\nContinue anyway?",
                )

                r = QMessageBox.question(
                    self,
                    "GT3 Wheel Painter",
                    "Backup failed. Continue without a backup?",
                )
                if r != QMessageBox.StandardButton.Yes:
                    return False
        return True


    def _set_status(self, message: str = "", hint=None):
        if message:
            self._status_file.setText(message)
        if hint is not None:
            self._status_hint.setText(hint)
    def export_texture(self):
        if self.gttw is None or not self.gttw.gtm1.texture_sets:
            QMessageBox.information(self, "GT3 Wheel Painter", "Open a wheel first.")
            return
        default = "rim.png"
        if self.current_path:
            default = self.current_path.stem + "_rim.png"
        start_dir = self._settings.value("last_dir", "", str)
        if start_dir and Path(start_dir).is_dir():
            default = str(Path(start_dir) / Path(default).name)
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Rim Texture", default, "PNG Image (*.png)"
        )
        if not path:
            return
        try:
            ref = self.gttw.gtm1.texture_sets[self._tex_ref_index]
            export_texture_png(bytes(ref.tex1.raw), path, 0)
            self._status_hint.setText(f"Exported {Path(path).name}")
        except Exception as e:
            QMessageBox.critical(self, "GT3 Wheel Painter", str(e))

    def replace_texture(self, keep_palette: bool = False):
        if self.gttw is None or not self.gttw.gtm1.texture_sets:
            QMessageBox.information(self, "GT3 Wheel Painter", "Open a wheel first.")
            return
        start = self._settings.value("last_dir", "", str)
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Replace Rim Texture",
            start if start else "",
            "Images (*.png *.bmp *.jpg *.jpeg *.tga *.webp);;All Files (*.*)"
        )
        if not path:
            return
        if not self._offer_backup("before replacing the texture"):
            return
        try:
            n = replace_all_textures_from_image(
                self.gttw.gtm1, path, keep_palette=bool(keep_palette)
            )
            ref = self.gttw.gtm1.texture_sets[self._tex_ref_index]
            raw = bytes(ref.tex1.raw)

            self._tex_palette = extract_tex1_palette(raw, 0)
            clut = []
            if self._tex_palette:
                for r, g, b, a in self._tex_palette.entries:
                    clut.append(ClutEntry(r, g, b, a))
            self.palette.set_clut(clut)
            self.gl_view.set_palette(clut)

            self._tex_rgba = decode_tex1_image(raw, 0)
            self._tex_indices = decode_tex1_indices(raw, 0)
            self._selected_index = 0
            self._apply_viewport_highlight()

            mode = "kept palette" if keep_palette else "new 16-colour palette"
            self._status_hint.setText(f"Replaced {n} textures from {Path(path).name} ({mode})")
        except Exception as e:
            QMessageBox.critical(self, "GT3 Wheel Painter", f"Replace failed:\n{e}")


    def _refresh_wheel_list(self, folder: Path, select: Optional[Path] = None):
        folder = Path(folder)
        if not folder.is_dir():
            return
        files = []
        for p in sorted(folder.iterdir(), key=lambda x: x.name.lower()):
            if not p.is_file():
                continue
            name = p.name
            if name.startswith('.'):
                continue
            if name.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.bak', '.qss', '.py', '.txt', '.md', '.glb', '.blend')):
                continue
            if name.lower().endswith(('.zip', '.exe', '.dll')):
                continue
            files.append(p)
        self._folder_files = files
        self.wheel_list.blockSignals(True)
        self.wheel_list.clear()
        select_row = -1
        for i, p in enumerate(files):
            item = QListWidgetItem(p.name)
            item.setData(Qt.ItemDataRole.UserRole, str(p))
            self.wheel_list.addItem(item)
            if select is not None and p.resolve() == Path(select).resolve():
                select_row = i
        if select_row >= 0:
            self.wheel_list.setCurrentRow(select_row)
            self.wheel_list.scrollToItem(self.wheel_list.item(select_row))
        self.wheel_list.blockSignals(False)

    def _on_wheel_item_activated(self, item):
        if item is None:
            return
        path = Path(item.data(Qt.ItemDataRole.UserRole))
        if self.current_path is not None and path.resolve() == self.current_path.resolve():
            return
        if path.is_file():
            self._load_path(path)

    def _step_wheel(self, delta: int):
        if not self._folder_files:
            return
        if self.current_path is None:
            idx = 0
        else:
            cur = self.current_path.resolve()
            idx = next((i for i, p in enumerate(self._folder_files) if p.resolve() == cur), 0)
            idx = (idx + delta) % len(self._folder_files)
        self._load_path(self._folder_files[idx])

    def _about(self):
        QMessageBox.about(
            self,
            "About GT3 Wheel Painter",
            "<b>GT3 Wheel Painter</b><br><br>"
            "Palette editor for Gran Turismo 3 wheel files (GTTW).<br><br>"
            "Loads real GTM1 mesh + Tex1 rim texture.<br>"
            "Inspired by pPainter / GT3-revisited formats."
        )
