from __future__ import annotations
import struct
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from .tex1 import Tex1


@dataclass
class TextureSetRef:
    index: int
    offset: int          # relative to GTM1 start
    tex1: Optional[Tex1] = None


@dataclass
class GTM1:
    raw: bytearray
    node_count: int = 0
    chunk_count: int = 0
    material_count: int = 0
    texture_set_count: int = 0
    texture_lod_count: int = 0

    texture_sets: List[TextureSetRef] = field(default_factory=list)
    # For wheels the interesting set is usually the one referenced by the
    # texture-LOD table entry that feeds the runtime slot.

    @classmethod
    def from_bytes(cls, data: bytes) -> "GTM1":
        if len(data) < 0x40:
            raise ValueError("Data too small for GTM1 header")

        magic = data[0:4]
        if magic != b"GTM1":
            raise ValueError(f"Not a GTM1 (magic={magic!r})")

        node_count = struct.unpack_from("<H", data, 0x10)[0]
        chunk_count = struct.unpack_from("<H", data, 0x12)[0]
        material_count = struct.unpack_from("<H", data, 0x14)[0]
        texture_set_count = struct.unpack_from("<H", data, 0x16)[0]
        texture_lod_count = struct.unpack_from("<H", data, 0x18)[0]

        # Pointer table (offsets relative to GTM1 start)
        # 0x2C -> array of u32 Tex1 offsets (0 = empty)
        tex_table_off = struct.unpack_from("<I", data, 0x2C)[0]

        texture_sets: List[TextureSetRef] = []
        if tex_table_off and texture_set_count:
            for i in range(texture_set_count):
                off = struct.unpack_from("<I", data, tex_table_off + i * 4)[0]
                if off == 0:
                    continue
                try:
                    tex1 = Tex1.from_bytes(data[off:])
                    texture_sets.append(TextureSetRef(index=i, offset=off, tex1=tex1))
                except Exception as e:
                    # Keep going; some sets may be incomplete in early testing
                    print(f"[GTM1] Tex1 {i} at 0x{off:X} failed: {e}")

        return cls(
            raw=bytearray(data),
            node_count=node_count,
            chunk_count=chunk_count,
            material_count=material_count,
            texture_set_count=texture_set_count,
            texture_lod_count=texture_lod_count,
            texture_sets=texture_sets,
        )

    def to_bytes(self) -> bytes:
        return bytes(self.raw)

    def get_primary_rim_tex1(self) -> Optional[Tex1]:
        if not self.texture_sets:
            return None
        # Prefer the first non-empty set; later we can follow the LOD table properly.
        return self.texture_sets[0].tex1

    def patch_tex1(self, tex_ref: TextureSetRef, new_tex1_bytes: bytes) -> None:
        if len(new_tex1_bytes) != len(tex_ref.tex1.raw):
            raise ValueError("Tex1 size changed – in-place patch not supported yet")
        start = tex_ref.offset
        end = start + len(new_tex1_bytes)
        self.raw[start:end] = new_tex1_bytes
        tex_ref.tex1 = Tex1.from_bytes(new_tex1_bytes)
