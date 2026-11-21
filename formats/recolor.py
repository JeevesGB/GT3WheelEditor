from __future__ import annotations
import colorsys
import struct
from typing import Tuple
from formats.gs_memory import extract_tex1_palette, apply_palette_to_tex1


def hue_shift_entry(rgba: Tuple[int, int, int, int], target_hue: float,
                    min_sat: float = 0.12) -> Tuple[int, int, int, int]:

    r, g, b, a = rgba
    h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
    if s < min_sat:
        return rgba
    nr, ng, nb = colorsys.hsv_to_rgb(target_hue, s, v)
    return (round(nr * 255), round(ng * 255), round(nb * 255), a)


def recolor_gtm1_hue(gtm1, target_rgb: Tuple[int, int, int],
                     skip: Tuple[int, int] | None = None) -> int:
    th = colorsys.rgb_to_hsv(*(c / 255.0 for c in target_rgb))[0]
    changed = 0
    for si, ref in enumerate(gtm1.texture_sets):
        raw = bytearray(ref.tex1.raw)
        tex_count = struct.unpack_from("<H", raw, 0x14)[0]
        for t in range(tex_count):
            if skip == (si, t):
                continue
            pal = extract_tex1_palette(bytes(raw), t)
            if pal is None:
                continue
            pal.entries = [hue_shift_entry(e, th) for e in pal.entries]
            apply_palette_to_tex1(raw, pal)
            changed += 1
        gtm1.patch_tex1(ref, bytes(raw))
    return changed
