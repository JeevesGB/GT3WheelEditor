"""
GTM1 mesh decoder for GT3 wheels.

Extracts positions, normals and triangle indices from VIF packets
inside GTM1 chunks. Layout documented in matt-pulsipher/GT3-revisited
docs/formats/car_model.md (VIF packets section).
"""

from __future__ import annotations
import struct
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
import numpy as np


def _rd16(d: bytes, o: int) -> int:
    return struct.unpack_from("<H", d, o)[0]


def _rd32(d: bytes, o: int) -> int:
    return struct.unpack_from("<I", d, o)[0]


def _rdf(d: bytes, o: int) -> float:
    return struct.unpack_from("<f", d, o)[0]


@dataclass
class MeshData:
    """Decoded triangle mesh ready for OpenGL."""
    positions: np.ndarray   # (N, 3) float32
    normals: np.ndarray     # (N, 3) float32
    indices: np.ndarray     # (M,) uint32 triangle list
    uvs: Optional[np.ndarray] = None  # (N, 2) if present

    @property
    def vertex_count(self) -> int:
        return len(self.positions)

    @property
    def triangle_count(self) -> int:
        return len(self.indices) // 3


def _parse_vif_packet(data: bytes, pkt_off: int, qwc: int) -> Tuple[
    List[Tuple[float, float, float]],
    List[Tuple[float, float, float]],
    List[int],
    List[Tuple[float, float]],
]:
    """
    Walk a VIF stream and pull out:
      - positions (V3-32 @ addr 0x00)
      - normals   (V3-32 @ addr 0x80)  [lit packets]
      - strip table (S-8 @ 0x40) -> expanded to per-strip vertex counts
      - uvs       (V2-32 @ 0x40, masked) when present

    Returns (positions, normals, strip_counts, uvs).
    strip_counts[i] = number of vertices in strip i.
    """
    end = pkt_off + qwc * 16
    if end > len(data):
        end = len(data)

    positions: List[Tuple[float, float, float]] = []
    normals: List[Tuple[float, float, float]] = []
    uvs: List[Tuple[float, float]] = []
    strip_counts: List[int] = []

    i = pkt_off
    while i + 4 <= end:
        word = _rd32(data, i)
        cmd = (word >> 24) & 0xFF
        num = (word >> 16) & 0xFF
        addr = word & 0x3FF
        flg = (word >> 15) & 1
        usn = (word >> 14) & 1

        # UNPACK family: top bits 0b0110_xx_xx
        if (cmd & 0x60) == 0x60:
            vn = (cmd >> 2) & 3   # 0=S 1=V2 2=V3 3=V4
            vl = cmd & 3          # 0=32 1=16 2=8 3=5
            i += 4
            if num == 0:
                continue

            if vl == 0 and vn == 2:  # V3-32
                # tightly packed 12-byte triples
                need = num * 12
                if i + need > end:
                    break
                triples = []
                for k in range(num):
                    x = _rdf(data, i + k * 12)
                    y = _rdf(data, i + k * 12 + 4)
                    z = _rdf(data, i + k * 12 + 8)
                    triples.append((x, y, z))
                if addr == 0x00:
                    positions = triples
                elif addr == 0x80:
                    normals = triples
                elif addr == 0xC0:
                    pass  # GIF tag – ignore
                elif addr == 0x20:
                    pass  # morph key1 positions
                elif addr == 0xA0:
                    pass  # morph key1 normals
                i += need
                # pad to 4-byte boundary (already is)
                continue

            if vl == 0 and vn == 1:  # V2-32  (UVs)
                need = num * 8
                if i + need > end:
                    break
                pairs = []
                for k in range(num):
                    s = _rdf(data, i + k * 8)
                    t = _rdf(data, i + k * 8 + 4)
                    pairs.append((s, t))
                if addr in (0x40, 0x60):
                    uvs = pairs
                i += need
                continue

            if vl == 2 and vn == 0:  # S-8  (strip table)
                # data is num bytes, each a u8
                if i + num > end:
                    break
                raw = list(data[i : i + num])
                # Layout (car_model.md): first byte = strip count, then per strip
                # the value (vertices - 2) * 3. Convert back to vertex counts.
                if raw:
                    n_strips = raw[0]
                    if 1 <= n_strips < len(raw):
                        strip_counts = []
                        for b in raw[1 : 1 + n_strips]:
                            if b % 3 == 0 and b >= 3:
                                strip_counts.append(b // 3 + 2)
                            elif b >= 3:
                                strip_counts.append(max(3, b // 3 + 2))
                            else:
                                strip_counts.append(max(3, b))
                    else:
                        strip_counts = [b for b in raw if b > 0]
                i += num
                # align to 4
                i = (i + 3) & ~3
                continue

            # Unknown unpack – skip payload conservatively
            if vl == 0:
                comps = [1, 2, 3, 4][vn]
                i += num * comps * 4
            elif vl == 2:
                i += num
                i = (i + 3) & ~3
            else:
                i += num * 2
                i = (i + 3) & ~3
            continue

        # NOP / other – advance one word
        i += 4

    return positions, normals, strip_counts, uvs


def _strips_to_tris(strip_counts: List[int], base: int = 0) -> List[int]:
    """Convert triangle-strip vertex counts into a triangle index list."""
    indices: List[int] = []
    v = base
    for count in strip_counts:
        if count < 3:
            v += count
            continue
        for i in range(count - 2):
            a, b, c = v + i, v + i + 1, v + i + 2
            if i & 1:
                indices.extend([b, a, c])
            else:
                indices.extend([a, b, c])
        v += count
    return indices


def _fallback_fan(n: int, base: int = 0) -> List[int]:
    """If strip table is missing, emit a simple triangle fan (rarely useful)."""
    if n < 3:
        return []
    indices = []
    for i in range(1, n - 1):
        indices.extend([base, base + i, base + i + 1])
    return indices


def decode_gtm1_mesh(gtm1_data: bytes, prefer_lit: bool = True) -> Optional[MeshData]:
    """
    Decode lit mesh geometry from a GTM1 block.
    Combines every lit chunk (kind == 1) so rim + spokes are both present.
    """
    if len(gtm1_data) < 0x40 or gtm1_data[0:4] != b"GTM1":
        return None

    chunk_count = _rd16(gtm1_data, 0x12)
    chunk_tab = _rd32(gtm1_data, 0x24)
    if not chunk_tab or chunk_count == 0:
        return None

    all_pos: List[Tuple[float, float, float]] = []
    all_nrm: List[Tuple[float, float, float]] = []
    all_idx: List[int] = []
    all_uv: List[Tuple[float, float]] = []

    for ci in range(chunk_count):
        off = _rd32(gtm1_data, chunk_tab + 4 * ci)
        if off + 16 > len(gtm1_data):
            continue
        flags = _rd16(gtm1_data, off + 8)
        kind = flags & 0xFF
        # Lit geometry only (skip reflection 0x02/0x102, shadow 0x05, etc.)
        if prefer_lit and (kind & 0x0F) != 1:
            continue
        n_packets = _rd16(gtm1_data, off + 10)

        for k in range(n_packets):
            e = off + 16 + 8 * k
            if e + 8 > len(gtm1_data):
                break
            pkt_rel = _rd32(gtm1_data, e)
            qwc = _rd16(gtm1_data, e + 4)
            pkt_off = off + pkt_rel
            if pkt_off + 4 > len(gtm1_data) or qwc == 0:
                continue

            positions, normals, strip_counts, uvs = _parse_vif_packet(
                gtm1_data, pkt_off, qwc
            )
            if not positions:
                continue

            base = len(all_pos)
            all_pos.extend(positions)

            if normals and len(normals) == len(positions):
                all_nrm.extend(normals)
            else:
                for x, y, z in positions:
                    ln = (y * y + z * z) ** 0.5 or 1.0
                    all_nrm.append((0.0, y / ln, z / ln))

            if uvs and len(uvs) == len(positions):
                all_uv.extend(uvs)
            else:
                all_uv.extend([(0.0, 0.0)] * len(positions))

            if strip_counts and sum(strip_counts) == len(positions):
                all_idx.extend(_strips_to_tris(strip_counts, base))
            elif strip_counts:
                all_idx.extend(_strips_to_tris(strip_counts, base))
            else:
                all_idx.extend(_fallback_fan(len(positions), base))

    if not all_pos or not all_idx:
        return None

    positions_np = np.array(all_pos, dtype=np.float32)
    normals_np = np.array(all_nrm, dtype=np.float32)
    lens = np.linalg.norm(normals_np, axis=1, keepdims=True)
    lens[lens < 1e-8] = 1.0
    normals_np /= lens
    indices_np = np.array(all_idx, dtype=np.uint32)
    uvs_np = np.array(all_uv, dtype=np.float32) if all_uv else None

    return MeshData(
        positions=positions_np,
        normals=normals_np,
        indices=indices_np,
        uvs=uvs_np,
    )
