from __future__ import annotations
import struct
from pathlib import Path
from typing import Optional, Tuple, List
import numpy as np
from PIL import Image

from formats.gs_memory import (
    GsMemory,
    _addr32,
    _addr4,
    decode_tex1_image,
    decode_tex1_indices,
    extract_tex1_palette,
    apply_palette_to_tex1,
)


def export_texture_png(tex1_raw: bytes, path: str | Path, texture_index: int = 0) -> Path:
    img = decode_tex1_image(tex1_raw, texture_index)
    if img is None:
        raise ValueError("Could not decode texture")
    path = Path(path)
    Image.fromarray(img, "RGBA").save(path)
    return path


def _quantize_rgba(rgba: np.ndarray, n_colors: int = 16) -> Tuple[np.ndarray, List[Tuple[int, int, int, int]]]:
    n_colors = min(n_colors, 16)
    h, w = rgba.shape[:2]
    transparent = rgba[:, :, 3] < 128
    has_t = bool(transparent.any())
    opaque = ~transparent
    idx = np.zeros((h, w), dtype=np.uint8)
    entries: List[Tuple[int, int, int, int]] = []

    n_opaque = n_colors - 1 if has_t else n_colors
    first = 1 if has_t else 0

    if opaque.any():
        px = rgba[:, :, :3][opaque]                      # (N,3)
        strip = Image.fromarray(px.reshape(1, -1, 3), "RGB")
        q = strip.quantize(colors=n_opaque, method=Image.Quantize.MEDIANCUT,
                           dither=Image.Dither.NONE)
        pal = q.getpalette() or []
        qi = np.array(q, dtype=np.uint8).reshape(-1)
        idx[opaque] = qi + first
        used = int(qi.max()) + 1
        for i in range(used):
            entries.append((pal[i * 3], pal[i * 3 + 1], pal[i * 3 + 2], 0x80))
        mean_rgb = tuple(int(v) for v in px.mean(axis=0))
    else:
        mean_rgb = (0, 0, 0)

    if has_t:
        idx[transparent] = 0
        entries.insert(0, (mean_rgb[0], mean_rgb[1], mean_rgb[2], 0))

    while len(entries) < 16:
        entries.append((0, 0, 0, 0))
    return idx, entries


def _tex0_fields(tex1_raw: bytes, texture_index: int = 0):
    tex_tab = struct.unpack_from("<I", tex1_raw, 0x18)[0]
    rec = tex_tab + texture_index * 0x28
    tex0 = struct.unpack_from("<Q", tex1_raw, rec)[0]
    return {
        "tbp": tex0 & 0x3FFF,
        "tbw": (tex0 >> 14) & 0x3F,
        "psm": (tex0 >> 20) & 0x3F,
        "tw": (tex0 >> 26) & 0xF,
        "th": (tex0 >> 30) & 0xF,
        "cbp": (tex0 >> 37) & 0x3FFF,
        "cpsm": (tex0 >> 51) & 0xF,
        "csa": (tex0 >> 56) & 0x1F,
        "width": 1 << ((tex0 >> 26) & 0xF),
        "height": 1 << ((tex0 >> 30) & 0xF),
    }


def _parse_buffers(tex1_raw: bytes):
    buf_tab = struct.unpack_from("<I", tex1_raw, 0x1C)[0]
    buf_count = struct.unpack_from("<H", tex1_raw, 0x16)[0]
    out = []
    for i in range(buf_count):
        o = buf_tab + i * 12
        if o + 12 > len(tex1_raw):
            break
        data_off, dbp, dbw, dpsm, w, h = struct.unpack_from("<IHBBHH", tex1_raw, o)
        out.append((data_off, dbp, dbw, dpsm, w, h))
    return out


def encode_texture_into_tex1(
    tex1_raw: bytearray,
    indices: np.ndarray,
    palette: List[Tuple[int, int, int, int]],
    texture_index: int = 0,
) -> bytearray:
    fields = _tex0_fields(tex1_raw, texture_index)
    if fields["psm"] not in (0x14, 0x24, 0x2C, 0x13, 0x1B):
        raise ValueError(f"Texture is not indexed (psm={fields['psm']})")

    tw, th = fields["width"], fields["height"]
    if indices.shape != (th, tw):
        raise ValueError(f"Index map must be {th}x{tw}, got {indices.shape}")

    # Seed GS with existing uploads so unrelated memory stays valid
    gs = GsMemory()
    buffers = _parse_buffers(tex1_raw)
    for data_off, dbp, dbw, dpsm, w, h in buffers:
        bits = {0x00: 32, 0x13: 8, 0x14: 4}.get(dpsm, 0)
        if not bits:
            continue
        nbytes = w * h * bits // 8
        if data_off + nbytes > len(tex1_raw):
            continue
        try:
            gs.upload(dbp, dbw, dpsm, w, h, bytes(tex1_raw[data_off : data_off + nbytes]))
        except Exception:
            continue

    tbp, tbw, cbp, csa = fields["tbp"], fields["tbw"], fields["cbp"], fields["csa"]

    # CLUT (CSM1)
    for j in range(min(16, len(palette))):
        r, g, b, a = palette[j]
        e = (csa & 0x0F) * 16 + j
        x = (e & 7) | ((e & 0x10) >> 1)
        y = ((e >> 5) << 1) | ((e >> 3) & 1)
        word = (r & 0xFF) | ((g & 0xFF) << 8) | ((b & 0xFF) << 16) | ((a & 0xFF) << 24)
        off = _addr32(cbp, 1, x, y) * 4
        struct.pack_into("<I", gs.mem, off, word)

    # T4 indices
    for y in range(th):
        for x in range(tw):
            nib = int(indices[y, x]) & 0xF
            a = _addr4(tbp, tbw, x, y)
            b = gs.mem[a >> 1]
            if a & 1:
                gs.mem[a >> 1] = (b & 0x0F) | (nib << 4)
            else:
                gs.mem[a >> 1] = (b & 0xF0) | nib

    # Dump CT32 buffers back to file
    for data_off, dbp, dbw, dpsm, w, h in buffers:
        if dpsm != 0x00:
            continue
        for ly in range(h):
            for lx in range(w):
                off = _addr32(dbp, dbw, lx, ly) * 4
                lin = data_off + (ly * w + lx) * 4
                if lin + 4 <= len(tex1_raw):
                    tex1_raw[lin : lin + 4] = gs.mem[off : off + 4]

    return tex1_raw


def replace_texture_from_image(
    tex1_raw: bytearray,
    image_path: str | Path,
    texture_index: int = 0,
    keep_palette: bool = False,
) -> Tuple[bytearray, np.ndarray, List[Tuple[int, int, int, int]]]:
    fields = _tex0_fields(tex1_raw, texture_index)
    tw, th = fields["width"], fields["height"]

    img = Image.open(image_path).convert("RGBA")
    if img.size != (tw, th):
        img = img.resize((tw, th), Image.Resampling.LANCZOS)
    rgba = np.array(img, dtype=np.uint8)

    if keep_palette:
        pal_obj = extract_tex1_palette(bytes(tex1_raw), texture_index)
        if pal_obj is None:
            keep_palette = False
        else:
            # Map each pixel to nearest existing palette colour
            palette = list(pal_obj.entries)
            pals = np.array([[r, g, b] for r, g, b, a in palette], dtype=np.float32)
            pix = rgba[:, :, :3].astype(np.float32).reshape(-1, 3)
            # brute force nearest (128*128*16 is fine)
            d = ((pix[:, None, :] - pals[None, :, :]) ** 2).sum(axis=2)
            t_entries = [i for i, e in enumerate(palette) if e[3] == 0]
            if t_entries:
                t_mask = rgba[:, :, 3].reshape(-1) < 128
                d[t_mask] = 1e9
                d[np.ix_(t_mask, t_entries)] = 0
            indices = d.argmin(axis=1).astype(np.uint8).reshape(th, tw)
            encode_texture_into_tex1(tex1_raw, indices, palette, texture_index)
            return tex1_raw, indices, palette

    indices, palette = _quantize_rgba(rgba, 16)
    encode_texture_into_tex1(tex1_raw, indices, palette, texture_index)
    return tex1_raw, indices, palette


def replace_all_textures_from_image(
    gtm1,
    image_path: str | Path,
    keep_palette: bool = False,
) -> int:
    count = 0
    for ref in gtm1.texture_sets:
        raw = bytearray(ref.tex1.raw)
        tex_count = struct.unpack_from("<H", raw, 0x14)[0]
        for t in range(tex_count):
            try:
                replace_texture_from_image(raw, image_path, t, keep_palette=keep_palette)
                count += 1
            except ValueError:
                continue  # not an indexed texture
        gtm1.patch_tex1(ref, bytes(raw))
    return count
