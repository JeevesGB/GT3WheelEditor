"""OpenGL preview: barrel GLB mesh with wheel texture on the face."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Optional, List

import numpy as np
from PyQt6.QtCore import Qt, QPoint, QTimer
from PyQt6.QtGui import QMouseEvent, QWheelEvent, QKeyEvent
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from OpenGL.GL import *
from OpenGL.GLU import *

from formats.tex1 import ClutEntry
from formats.mesh import MeshData
from formats.glb import load_glb, GlbMesh


_ASSET_GLB = Path(__file__).resolve().parent.parent / "assets" / "wheel.glb"


class GLWidget(QOpenGLWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(400, 400)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.rot_x = 18.0
        self.rot_y = -30.0
        self.zoom = -5.0
        self.last_pos: Optional[QPoint] = None

        # Legacy GTM1 mesh (kept for fallback / stats)
        self.mesh: Optional[MeshData] = None
        # Viewport display mesh
        self.barrel: Optional[GlbMesh] = None
        try:
            if _ASSET_GLB.is_file():
                self.barrel = load_glb(_ASSET_GLB)
        except Exception:
            self.barrel = None

        self.texture_rgba: Optional[np.ndarray] = None
        self.body_color = (0.62, 0.62, 0.65)  # matches face dominant RGB 0..1
        self.tex_id: Optional[int] = None
        self.palette: List[ClutEntry] = []
        self.show_wireframe = False

        self.auto_rotate = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(16)

    def initializeGL(self):
        glClearColor(0.10, 0.10, 0.12, 1.0)
        glEnable(GL_DEPTH_TEST)
        glDisable(GL_CULL_FACE)
        glShadeModel(GL_SMOOTH)
        glEnable(GL_LIGHTING)
        glEnable(GL_LIGHT0)
        glEnable(GL_LIGHT1)
        glEnable(GL_COLOR_MATERIAL)
        glColorMaterial(GL_FRONT_AND_BACK, GL_AMBIENT_AND_DIFFUSE)
        glLightfv(GL_LIGHT0, GL_POSITION, (2.0, 3.5, 4.0, 1.0))
        glLightfv(GL_LIGHT0, GL_DIFFUSE, (1.0, 1.0, 1.0, 1.0))
        glLightfv(GL_LIGHT0, GL_AMBIENT, (0.45, 0.45, 0.47, 1.0))
        glLightModelfv(GL_LIGHT_MODEL_AMBIENT, (0.35, 0.35, 0.37, 1.0))
        glLightModeli(GL_LIGHT_MODEL_TWO_SIDE, GL_TRUE)
        glLightfv(GL_LIGHT0, GL_SPECULAR, (0.7, 0.7, 0.7, 1.0))
        glLightfv(GL_LIGHT1, GL_POSITION, (-2.0, 1.0, 2.0, 1.0))
        glLightfv(GL_LIGHT1, GL_DIFFUSE, (0.35, 0.35, 0.4, 1.0))
        glMaterialfv(GL_FRONT_AND_BACK, GL_SPECULAR, (0.55, 0.55, 0.55, 1.0))
        glMaterialf(GL_FRONT_AND_BACK, GL_SHININESS, 48.0)
        glEnable(GL_TEXTURE_2D)
        glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)
        glEnable(GL_POLYGON_OFFSET_FILL)
        glPolygonOffset(1.0, 1.0)

    def resizeGL(self, w, h):
        glViewport(0, 0, max(1, w), max(1, h))
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        gluPerspective(38.0, (w / h) if h else 1.0, 0.05, 50.0)
        glMatrixMode(GL_MODELVIEW)

    def _upload_texture(self):
        if self.texture_rgba is None:
            if self.tex_id:
                glDeleteTextures([self.tex_id])
                self.tex_id = None
            return
        h, w = self.texture_rgba.shape[:2]
        if self.tex_id is None:
            self.tex_id = int(glGenTextures(1))
        glBindTexture(GL_TEXTURE_2D, self.tex_id)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
        flipped = np.ascontiguousarray(self.texture_rgba[::-1])
        glTexImage2D(
            GL_TEXTURE_2D, 0, GL_RGBA, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE, flipped
        )

    def _draw_primitive(self, prim, textured: bool):
        glEnableClientState(GL_VERTEX_ARRAY)
        glEnableClientState(GL_NORMAL_ARRAY)
        glVertexPointer(3, GL_FLOAT, 0, prim.positions)
        glNormalPointer(GL_FLOAT, 0, prim.normals)
        if textured and prim.uvs is not None and self.tex_id:
            glEnableClientState(GL_TEXTURE_COORD_ARRAY)
            uvs = prim.uvs.copy()
            # glTF UVs are top-left origin for many exporters; flip V to match GL
            uvs[:, 1] = 1.0 - uvs[:, 1]
            glTexCoordPointer(2, GL_FLOAT, 0, uvs)
        glDrawElements(GL_TRIANGLES, len(prim.indices), GL_UNSIGNED_INT, prim.indices)
        if textured and prim.uvs is not None:
            glDisableClientState(GL_TEXTURE_COORD_ARRAY)
        glDisableClientState(GL_VERTEX_ARRAY)
        glDisableClientState(GL_NORMAL_ARRAY)

    def _draw_legacy_mesh(self, textured: bool):
        if self.mesh is None or self.mesh.vertex_count == 0:
            return
        glEnableClientState(GL_VERTEX_ARRAY)
        glEnableClientState(GL_NORMAL_ARRAY)
        glVertexPointer(3, GL_FLOAT, 0, self.mesh.positions)
        glNormalPointer(GL_FLOAT, 0, self.mesh.normals)
        if textured and self.mesh.uvs is not None and self.tex_id:
            glEnableClientState(GL_TEXTURE_COORD_ARRAY)
            uvs = self.mesh.uvs.copy()
            uvs[:, 1] = 1.0 - uvs[:, 1]
            glTexCoordPointer(2, GL_FLOAT, 0, uvs)
        glDrawElements(
            GL_TRIANGLES, len(self.mesh.indices), GL_UNSIGNED_INT, self.mesh.indices
        )
        if textured:
            glDisableClientState(GL_TEXTURE_COORD_ARRAY)
        glDisableClientState(GL_VERTEX_ARRAY)
        glDisableClientState(GL_NORMAL_ARRAY)

    def paintGL(self):
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        glLoadIdentity()
        glTranslatef(0.0, 0.0, self.zoom)
        glRotatef(self.rot_x, 1.0, 0.0, 0.0)
        glRotatef(self.rot_y, 0.0, 1.0, 0.0)

        has_tex = self.tex_id is not None

        if self.barrel is not None:
            def _wire(prim):
                glDisable(GL_LIGHTING)
                glDisable(GL_TEXTURE_2D)
                glPolygonMode(GL_FRONT_AND_BACK, GL_LINE)
                glColor3f(0.12, 0.12, 0.12)
                self._draw_primitive(prim, textured=False)
                glPolygonMode(GL_FRONT_AND_BACK, GL_FILL)
                glEnable(GL_LIGHTING)

            # --- tyre: dark rubber ---
            tyre = self.barrel.tyre
            if tyre is not None:
                glDisable(GL_TEXTURE_2D)
                glEnable(GL_LIGHTING)
                glColor3f(0.12, 0.12, 0.13)
                glPolygonMode(GL_FRONT_AND_BACK, GL_FILL)
                self._draw_primitive(tyre, textured=False)
                if self.show_wireframe:
                    _wire(tyre)

            # --- body: metallic grey ---
            body = self.barrel.body
            if body is not None:
                glDisable(GL_TEXTURE_2D)
                glEnable(GL_LIGHTING)
                r, g, b = self.body_color
                glColor3f(r, g, b)
                glPolygonMode(GL_FRONT_AND_BACK, GL_FILL)
                self._draw_primitive(body, textured=False)
                if self.show_wireframe:
                    _wire(body)

            # --- face: wheel texture (GT Tex1) ---
            face = self.barrel.face
            if face is not None:
                if has_tex:
                    glEnable(GL_TEXTURE_2D)
                    glBindTexture(GL_TEXTURE_2D, self.tex_id)
                    glDisable(GL_LIGHTING)
                    glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_REPLACE)
                    glColor3f(1.0, 1.0, 1.0)
                else:
                    glDisable(GL_TEXTURE_2D)
                    glEnable(GL_LIGHTING)
                    glColor3f(0.75, 0.75, 0.78)
                glPolygonMode(GL_FRONT_AND_BACK, GL_FILL)
                self._draw_primitive(face, textured=has_tex)
                glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)
                glEnable(GL_LIGHTING)
                if self.show_wireframe:
                    _wire(face)
        elif self.mesh is not None:
            # Fallback: original GTM1 rim
            if has_tex:
                glEnable(GL_TEXTURE_2D)
                glBindTexture(GL_TEXTURE_2D, self.tex_id)
                glColor3f(1.0, 1.0, 1.0)
            else:
                glDisable(GL_TEXTURE_2D)
                glColor3f(0.75, 0.75, 0.78)
            glEnable(GL_LIGHTING)
            glPolygonMode(GL_FRONT_AND_BACK, GL_FILL)
            self._draw_legacy_mesh(has_tex)
        else:
            glDisable(GL_LIGHTING)
            glColor3f(0.35, 0.35, 0.38)
            glBegin(GL_LINE_LOOP)
            for i in range(48):
                a = 2 * math.pi * i / 48
                glVertex3f(math.cos(a), 0.0, math.sin(a))
            glEnd()
            glEnable(GL_LIGHTING)

        glDisable(GL_TEXTURE_2D)
        glDisable(GL_LIGHTING)
        glColor4f(0.14, 0.14, 0.16, 1.0)
        glBegin(GL_QUADS)
        glVertex3f(-2.2, -1.35, -2.2)
        glVertex3f(2.2, -1.35, -2.2)
        glVertex3f(2.2, -1.35, 2.2)
        glVertex3f(-2.2, -1.35, 2.2)
        glEnd()
        glEnable(GL_LIGHTING)

    def set_mesh(self, mesh: Optional[MeshData]):
        self.mesh = mesh
        self.update()

    def set_texture(self, rgba: Optional[np.ndarray]):
        self.texture_rgba = rgba
        self.body_color = self._dominant_rgb(rgba)
        self.makeCurrent()
        self._upload_texture()
        self.doneCurrent()
        self.update()

    @staticmethod
    def _dominant_rgb(rgba: Optional[np.ndarray]):
        if rgba is None or rgba.size == 0:
            return (0.62, 0.62, 0.65)
        px = rgba.reshape(-1, rgba.shape[-1]).astype(np.float32)
        # Prefer opaque-ish texels
        if px.shape[1] >= 4:
            opaque = px[:, 3] > 32
            if np.any(opaque):
                px = px[opaque]
        rgb = px[:, :3]
        # Discard near-black (holes / tyre bleed) and near-white outliers
        luma = rgb[:, 0] * 0.299 + rgb[:, 1] * 0.587 + rgb[:, 2] * 0.114
        mid = (luma > 25) & (luma < 240)
        if np.any(mid):
            rgb = rgb[mid]
        if len(rgb) == 0:
            return (0.62, 0.62, 0.65)
        # Median is stabler than mean on spoke textures
        med = np.median(rgb, axis=0)
        r, g, b = (float(med[0]) / 255.0, float(med[1]) / 255.0, float(med[2]) / 255.0)
        # Slight lift so the barrel reads as painted metal under lighting
        lift = 1.08
        return (min(1.0, r * lift), min(1.0, g * lift), min(1.0, b * lift))

    def set_palette(self, clut: List[ClutEntry]):
        self.palette = clut
        self.update()

    def _tick(self):
        if self.auto_rotate:
            self.rot_y += 0.35
            self.update()

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_W:
            self.show_wireframe = not self.show_wireframe
            self.update()
        elif event.key() == Qt.Key.Key_R:
            self.auto_rotate = not self.auto_rotate
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event: QMouseEvent):
        self.last_pos = event.position().toPoint()
        if event.button() == Qt.MouseButton.LeftButton:
            self.auto_rotate = False

    def mouseMoveEvent(self, event: QMouseEvent):
        if self.last_pos is None:
            return
        pos = event.position().toPoint()
        dx = pos.x() - self.last_pos.x()
        dy = pos.y() - self.last_pos.y()
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.rot_y += dx * 0.5
            self.rot_x += dy * 0.5
            self.rot_x = max(-89, min(89, self.rot_x))
            self.update()
        self.last_pos = pos

    def mouseReleaseEvent(self, event: QMouseEvent):
        pass

    def wheelEvent(self, event: QWheelEvent):
        delta = event.angleDelta().y() / 120.0
        self.zoom += delta * 0.25
        self.zoom = max(-10.0, min(-1.2, self.zoom))
        self.update()
