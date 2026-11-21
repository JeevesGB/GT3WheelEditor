from __future__ import annotations
import struct
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
import numpy as np
from PIL import Image


@dataclass
class ClutEntry:
    r: int
    g: int
    b: int
    a: int  # 0-255, PS2 style (0x80 ≈ opaque)

    def to_rgba(self) -> Tuple[int, int, int, int]:
        # Expand PS2 alpha (0x80 = 1.0) to full 0-255 for display
        a = min(255, int(self.a * 255 / 0x80)) if self.a <= 0x80 else 255
        return (self.r, self.g, self.b, a)

    def to_ps2(self) -> bytes:
        return struct.pack("<BBBB", self.r, self.g, self.b, self.a)


@dataclass
class TextureInfo:
    index: int
    width: int
    height: int
    format: int          # rough PSMT4 / PSMT8 / PSMCT32 etc.
    pixel_data: bytes
    clut: List[ClutEntry] = field(default_factory=list)
    clut_offset: int = 0  # absolute offset inside the Tex1 raw buffer


@dataclass
class Tex1:
    raw: bytearray
    texture_count: int = 0
    textures: List[TextureInfo] = field(default_factory=list)

    @classmethod
    def from_bytes(cls, data: bytes) -> "Tex1":
        if len(data) < 0x28:
            raise ValueError("Data too small for Tex1 header")

        magic = data[0:4]
        if magic != b"Tex1":
            raise ValueError(f"Not a Tex1 (magic={magic!r})")

        size = struct.unpack_from("<I", data, 0x0C)[0]
        texture_count = struct.unpack_from("<H", data, 0x14)[0]
        # buffer_count = struct.unpack_from("<H", data, 0x16)[0]
        tex_table_off = struct.unpack_from("<I", data, 0x18)[0]
        # buffer_table_off = struct.unpack_from("<I", data, 0x1C)[0]
        # paint_table_off = struct.unpack_from("<I", data, 0x20)[0]

        textures: List[TextureInfo] = []

        # Each texture record is 0x28 bytes (common layout)
        RECORD_SIZE = 0x28
        for i in range(min(texture_count, 32)):  # safety
            rec_off = tex_table_off + i * RECORD_SIZE
            if rec_off + RECORD_SIZE > len(data):
                break

            # Very rough decode – real TEX0 is complex.
            # For wheels we mainly care about finding a usable CLUT.
            # Many wheel Tex1s store a 4-bit or 8-bit image + CT32 CLUT.
            try:
                # These fields are approximate; we improve them as we see real data.
                w = struct.unpack_from("<H", data, rec_off + 0x08)[0] or 128
                h = struct.unpack_from("<H", data, rec_off + 0x0A)[0] or 128
                # Clamp ridiculous values
                w = max(8, min(w, 512))
                h = max(8, min(h, 512))

                # Heuristic: look for a 32-bit CLUT near the end of the set
                # or after the pixel data. Real code will use the buffer table.
                clut: List[ClutEntry] = []
                clut_off = 0

                # Temporary: scan for a plausible 16- or 256-entry CT32 block
                # that looks like a palette (lots of non-zero alpha ~0x80).
                for candidate in range(0x40, len(data) - 64, 16):
                    # Check 16 consecutive CT32 colours
                    good = 0
                    for c in range(16):
                        off = candidate + c * 4
                        if off + 4 > len(data):
                            break
                        r, g, b, a = data[off], data[off+1], data[off+2], data[off+3]
                        if 0x40 <= a <= 0x90:  # typical PS2 opaque range
                            good += 1
                    if good >= 10:
                        clut_off = candidate
                        for c in range(16):
                            off = candidate + c * 4
                            r, g, b, a = data[off], data[off+1], data[off+2], data[off+3]
                            clut.append(ClutEntry(r, g, b, a))
                        break

                textures.append(TextureInfo(
                    index=i,
                    width=w,
                    height=h,
                    format=0,  # unknown for now
                    pixel_data=b"",  # filled later when we need the image
                    clut=clut,
                    clut_offset=clut_off,
                ))
            except Exception:
                continue

        return cls(raw=bytearray(data), texture_count=texture_count, textures=textures)

    def get_primary_clut(self) -> List[ClutEntry]:
        for t in self.textures:
            if t.clut:
                return t.clut
        return []

    def set_clut(self, clut: List[ClutEntry], texture_index: int = 0) -> None:
        """Write the edited CLUT back into the raw buffer."""
        if texture_index >= len(self.textures):
            return
        tex = self.textures[texture_index]
        if not tex.clut_offset or not clut:
            return

        for i, entry in enumerate(clut):
            off = tex.clut_offset + i * 4
            if off + 4 > len(self.raw):
                break
            self.raw[off:off+4] = entry.to_ps2()

        # Keep the in-memory list in sync
        tex.clut = list(clut)

    def to_bytes(self) -> bytes:
        return bytes(self.raw)

    def preview_image(self, texture_index: int = 0) -> Optional[Image.Image]:
        """Create a simple solid-colour preview from the CLUT (placeholder)."""
        clut = self.get_primary_clut()
        if not clut:
            return None
        # Just show the palette as a strip for now
        n = len(clut)
        img = Image.new("RGBA", (n * 16, 32))
        pixels = img.load()
        for i, c in enumerate(clut):
            r, g, b, a = c.to_rgba()
            for x in range(16):
                for y in range(32):
                    pixels[i * 16 + x, y] = (r, g, b, a)
        return img
