from __future__ import annotations
from dataclasses import dataclass
from typing import List, Tuple
import struct
import numpy as np

# Block tables from GS User's Manual / GT3-revisited
_BLOCK32 = [
    [0, 1, 4, 5, 16, 17, 20, 21],
    [2, 3, 6, 7, 18, 19, 22, 23],
    [8, 9, 12, 13, 24, 25, 28, 29],
    [10, 11, 14, 15, 26, 27, 30, 31],
]
_BLOCK16 = [
    [0, 2, 8, 10], [1, 3, 9, 11], [4, 6, 12, 14], [5, 7, 13, 15],
    [16, 18, 24, 26], [17, 19, 25, 27], [20, 22, 28, 30], [21, 23, 29, 31],
]
_COL_WORDS = [
    [0, 1, 4, 5, 8, 9, 12, 13],
    [2, 3, 6, 7, 10, 11, 14, 15],
]

def _build_tables():
    col32 = [[0]*8 for _ in range(8)]
    col16 = [[0]*16 for _ in range(8)]
    col8 = [[0]*16 for _ in range(16)]
    col4 = [[0]*32 for _ in range(16)]
    for y in range(8):
        for x in range(8):
            col32[y][x] = (y >> 1) * 16 + _COL_WORDS[y & 1][x]
        for x in range(16):
            col16[y][x] = (((y >> 1) * 16 + _COL_WORDS[y & 1][x & 7]) * 2
                           + ((x >> 3) & 1))
    for y in range(16):
        col = y >> 2
        r = y & 3
        swap = 4 if ((r >> 1) ^ (col & 1)) else 0
        for x in range(16):
            word = col * 16 + _COL_WORDS[r & 1][(x & 7) ^ swap]
            col8[y][x] = word * 4 + ((x >> 3) & 1) * 2 + (r >> 1)
        for x in range(32):
            word = col * 16 + _COL_WORDS[r & 1][(x & 7) ^ swap]
            col4[y][x] = word * 8 + ((x >> 3) & 3) * 2 + (r >> 1)
    return col32, col16, col8, col4

_COL32, _COL16, _COL8, _COL4 = _build_tables()
_BMASK = 0x3FFF


def _addr32(bp, bw, x, y):
    block = (bp + (y & ~31) * bw + ((x & ~63) >> 1)
             + _BLOCK32[(y >> 3) & 3][(x >> 3) & 7]) & _BMASK
    return (block << 6) + _COL32[y & 7][x & 7]


def _addr16(bp, bw, x, y):
    block = (bp + (((y & ~63) * bw) >> 1) + ((x & ~63) >> 1)
             + _BLOCK16[(y >> 3) & 7][(x >> 4) & 3]) & _BMASK
    return (block << 7) + _COL16[y & 7][x & 15]


def _addr8(bp, bw, x, y):
    block = (bp + (((y & ~63) * bw) >> 2) + ((x & ~127) >> 2)
             + _BLOCK32[(y >> 4) & 3][(x >> 4) & 7]) & _BMASK
    return (block << 8) + _COL8[y & 15][x & 15]


def _addr4(bp, bw, x, y):
    block = (bp + (((y & ~127) * bw) >> 3) + ((x & ~127) >> 2)
             + _BLOCK16[(y >> 4) & 7][(x >> 5) & 3]) & _BMASK
    return (block << 9) + _COL4[y & 15][x & 31]


def _expand_a(a):
    return min(a * 2, 255)


def _ct32_rgba(c):
    r = c & 0xFF
    g = (c >> 8) & 0xFF
    b = (c >> 16) & 0xFF
    a = _expand_a((c >> 24) & 0xFF)
    return (r, g, b, a)


class GsMemory:
    def __init__(self):
        self.mem = bytearray(4 * 1024 * 1024)

    def upload(self, dbp, dbw, dpsm, w, h, src: bytes):
        m = self.mem
        i = 0
        for y in range(h):
            for x in range(w):
                if dpsm == 0x00:  # CT32
                    off = _addr32(dbp, dbw, x, y) * 4
                    m[off:off+4] = src[i*4:i*4+4]
                elif dpsm == 0x13:  # T8
                    m[_addr8(dbp, dbw, x, y)] = src[i]
                elif dpsm == 0x14:  # T4
                    nib = (src[i >> 1] >> ((i & 1) * 4)) & 0xF
                    a = _addr4(dbp, dbw, x, y)
                    b = m[a >> 1]
                    if a & 1:
                        m[a >> 1] = (b & 0x0F) | (nib << 4)
                    else:
                        m[a >> 1] = (b & 0xF0) | nib
                i += 1

    def read_texel(self, bp, bw, psm, x, y) -> int:
        m = self.mem
        if psm in (0x00, 0x01):
            off = _addr32(bp, bw, x, y) * 4
            return struct.unpack_from('<I', m, off)[0]
        if psm == 0x13:
            return m[_addr8(bp, bw, x, y)]
        if psm == 0x14:
            a = _addr4(bp, bw, x, y)
            return (m[a >> 1] >> ((a & 1) * 4)) & 0xF
        return 0

    def clut_color(self, tbp_unused, cbp, cpsm, csa, psm, index) -> Tuple[int,int,int,int]:
        is4 = psm in (0x14, 0x24, 0x2C)
        e = ((csa & 0x0F) * 16 + (index & 0x0F)) if is4 else index
        x = (e & 7) | ((e & 0x10) >> 1)
        y = ((e >> 5) << 1) | ((e >> 3) & 1)
        c = self.read_texel(cbp, 1, 0x00, x, y)
        return _ct32_rgba(c)

    def decode_rgba(self, tbp, tbw, psm, tw, th, cbp, cpsm, csa, tcc=True) -> np.ndarray:
        width = 1 << tw
        height = 1 << th
        indexed = psm in (0x13, 0x14, 0x1B, 0x24, 0x2C)
        n = 256 if psm in (0x13, 0x1B) else 16
        palette = []
        if indexed:
            for i in range(n):
                palette.append(self.clut_color(0, cbp, cpsm, csa, psm, i))
        out = np.zeros((height, width, 4), dtype=np.uint8)
        for y in range(height):
            for x in range(width):
                v = self.read_texel(tbp, tbw, psm, x, y)
                if indexed:
                    r, g, b, a = palette[v & 0xFF]
                else:
                    r, g, b, a = _ct32_rgba(v)
                if not tcc:
                    a = 255
                out[y, x] = (r, g, b, a)
        return out


def decode_tex1_image(tex1_raw: bytes, texture_index: int = 0) -> np.ndarray | None:
    if len(tex1_raw) < 0x28 or tex1_raw[0:4] != b'Tex1':
        return None
    tex_count = struct.unpack_from('<H', tex1_raw, 0x14)[0]
    buf_count = struct.unpack_from('<H', tex1_raw, 0x16)[0]
    tex_tab = struct.unpack_from('<I', tex1_raw, 0x18)[0]
    buf_tab = struct.unpack_from('<I', tex1_raw, 0x1C)[0]
    if texture_index >= tex_count:
        return None

    gs = GsMemory()
    for i in range(buf_count):
        o = buf_tab + i * 12
        if o + 12 > len(tex1_raw):
            break
        data_off, dbp, dbw, dpsm, w, h = struct.unpack_from('<IHBBHH', tex1_raw, o)
        if data_off >= len(tex1_raw) or w == 0 or h == 0:
            continue
        bits = {0x00: 32, 0x13: 8, 0x14: 4}.get(dpsm, 0)
        if bits == 0:
            continue
        nbytes = w * h * bits // 8
        src = bytes(tex1_raw[data_off:data_off + nbytes])
        if len(src) < nbytes:
            continue
        try:
            gs.upload(dbp, dbw, dpsm, w, h, src)
        except Exception:
            continue

    rec = tex_tab + texture_index * 0x28
    if rec + 0x28 > len(tex1_raw):
        return None
    tex0 = struct.unpack_from('<Q', tex1_raw, rec)[0]
    tbp = tex0 & 0x3FFF
    tbw = (tex0 >> 14) & 0x3F
    psm = (tex0 >> 20) & 0x3F
    tw = (tex0 >> 26) & 0xF
    th = (tex0 >> 30) & 0xF
    tcc = (tex0 >> 34) & 1
    cbp = (tex0 >> 37) & 0x3FFF
    cpsm = (tex0 >> 51) & 0xF
    csa = (tex0 >> 56) & 0x1F
    return gs.decode_rgba(tbp, tbw, psm, tw, th, cbp, cpsm, csa, bool(tcc))


@dataclass
class Tex1Palette:
    entries: list  # list of (r,g,b,a) 0-255 GS alpha
    # byte offsets into tex1.raw for each entry (4 bytes each, little-endian RGBA)
    offsets: list
    texture_index: int = 0
    csa: int = 0
    cbp: int = 0


def extract_tex1_palette(tex1_raw: bytes, texture_index: int = 0) -> Tex1Palette | None:
    if len(tex1_raw) < 0x28 or tex1_raw[0:4] != b'Tex1':
        return None
    tex_count = struct.unpack_from('<H', tex1_raw, 0x14)[0]
    buf_count = struct.unpack_from('<H', tex1_raw, 0x16)[0]
    tex_tab = struct.unpack_from('<I', tex1_raw, 0x18)[0]
    buf_tab = struct.unpack_from('<I', tex1_raw, 0x1C)[0]
    if texture_index >= tex_count:
        return None

    # Parse buffers
    buffers = []
    for i in range(buf_count):
        o = buf_tab + i * 12
        if o + 12 > len(tex1_raw):
            break
        data_off, dbp, dbw, dpsm, w, h = struct.unpack_from('<IHBBHH', tex1_raw, o)
        buffers.append((data_off, dbp, dbw, dpsm, w, h))

    rec = tex_tab + texture_index * 0x28
    if rec + 8 > len(tex1_raw):
        return None
    tex0 = struct.unpack_from('<Q', tex1_raw, rec)[0]
    psm = (tex0 >> 20) & 0x3F
    cbp = (tex0 >> 37) & 0x3FFF
    cpsm = (tex0 >> 51) & 0xF
    csa = (tex0 >> 56) & 0x1F
    if psm not in (0x14, 0x13, 0x24, 0x2C, 0x1B):
        # not indexed – no palette
        return None

    n_colours = 16 if psm in (0x14, 0x24, 0x2C) else 256

    # Upload into GS
    gs = GsMemory()
    for data_off, dbp, dbw, dpsm, w, h in buffers:
        bits = {0x00: 32, 0x13: 8, 0x14: 4}.get(dpsm, 0)
        if not bits or w == 0 or h == 0:
            continue
        nbytes = w * h * bits // 8
        if data_off + nbytes > len(tex1_raw):
            continue
        try:
            gs.upload(dbp, dbw, dpsm, w, h, bytes(tex1_raw[data_off:data_off + nbytes]))
        except Exception:
            continue

    # Build reverse map for CT32 buffers: GS word addr -> (data_off, linear_index)
    rev: dict[int, tuple[int, int]] = {}
    for data_off, dbp, dbw, dpsm, w, h in buffers:
        if dpsm != 0x00:
            continue
        for ly in range(h):
            for lx in range(w):
                addr = _addr32(dbp, dbw, lx, ly)
                rev[addr] = (data_off, ly * w + lx)

    entries = []
    offsets = []
    for j in range(n_colours):
        e = ((csa & 0x0F) * 16 + (j & 0x0F)) if n_colours == 16 else j
        x = (e & 7) | ((e & 0x10) >> 1)
        y = ((e >> 5) << 1) | ((e >> 3) & 1)
        c = gs.read_texel(cbp, 1, 0x00, x, y)
        r = c & 0xFF
        g = (c >> 8) & 0xFF
        b = (c >> 16) & 0xFF
        a = (c >> 24) & 0xFF
        entries.append((r, g, b, a))

        addr = _addr32(cbp, 1, x, y)
        if addr in rev:
            data_off, lin = rev[addr]
            offsets.append(data_off + lin * 4)
        else:
            offsets.append(-1)

    return Tex1Palette(entries=entries, offsets=offsets, texture_index=texture_index,
                       csa=csa, cbp=cbp)


def apply_palette_to_tex1(tex1_raw: bytearray, palette: Tex1Palette) -> None:
    for i, (r, g, b, a) in enumerate(palette.entries):
        if i >= len(palette.offsets):
            break
        off = palette.offsets[i]
        if off < 0 or off + 4 > len(tex1_raw):
            continue
        tex1_raw[off] = r & 0xFF
        tex1_raw[off + 1] = g & 0xFF
        tex1_raw[off + 2] = b & 0xFF
        tex1_raw[off + 3] = a & 0xFF


def redecode_with_palette(tex1_raw: bytes, texture_index: int = 0) -> np.ndarray | None:
    return decode_tex1_image(tex1_raw, texture_index)


def decode_tex1_indices(tex1_raw: bytes, texture_index: int = 0) -> np.ndarray | None:
    if len(tex1_raw) < 0x28 or tex1_raw[0:4] != b'Tex1':
        return None
    tex_count = struct.unpack_from('<H', tex1_raw, 0x14)[0]
    buf_count = struct.unpack_from('<H', tex1_raw, 0x16)[0]
    tex_tab = struct.unpack_from('<I', tex1_raw, 0x18)[0]
    buf_tab = struct.unpack_from('<I', tex1_raw, 0x1C)[0]
    if texture_index >= tex_count:
        return None

    gs = GsMemory()
    for i in range(buf_count):
        o = buf_tab + i * 12
        if o + 12 > len(tex1_raw):
            break
        data_off, dbp, dbw, dpsm, w, h = struct.unpack_from('<IHBBHH', tex1_raw, o)
        bits = {0x00: 32, 0x13: 8, 0x14: 4}.get(dpsm, 0)
        if not bits or w == 0 or h == 0:
            continue
        nbytes = w * h * bits // 8
        if data_off + nbytes > len(tex1_raw):
            continue
        try:
            gs.upload(dbp, dbw, dpsm, w, h, bytes(tex1_raw[data_off:data_off + nbytes]))
        except Exception:
            continue

    rec = tex_tab + texture_index * 0x28
    if rec + 8 > len(tex1_raw):
        return None
    tex0 = struct.unpack_from('<Q', tex1_raw, rec)[0]
    tbp = tex0 & 0x3FFF
    tbw = (tex0 >> 14) & 0x3F
    psm = (tex0 >> 20) & 0x3F
    tw = (tex0 >> 26) & 0xF
    th = (tex0 >> 30) & 0xF
    if psm not in (0x13, 0x14, 0x1B, 0x24, 0x2C):
        return None
    width, height = 1 << tw, 1 << th
    out = np.zeros((height, width), dtype=np.uint8)
    for y in range(height):
        for x in range(width):
            out[y, x] = gs.read_texel(tbp, tbw, psm, x, y) & 0xFF
    return out


def make_highlight_texture(
    rgba: np.ndarray,
    indices: np.ndarray,
    selected: int,
    flash_on: bool = True,
    keep: float = 0.35,
) -> np.ndarray:
    if indices is None or selected < 0 or not flash_on:
        return rgba

    out = rgba.copy()
    sel = indices == selected
    if not np.any(sel):
        return out

    # Vivid magenta — reads on both dark and light metal
    out[sel, 0] = 255
    out[sel, 1] = 0
    out[sel, 2] = 255
    out[sel, 3] = 255
    return out


