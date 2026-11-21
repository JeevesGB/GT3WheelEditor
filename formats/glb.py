"""Minimal GLB loader for the viewport barrel mesh."""
from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np


def _quat_to_matrix(q: List[float]) -> np.ndarray:
    """glTF quaternion [x,y,z,w] → 3x3 rotation matrix."""
    x, y, z, w = q
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ],
        dtype=np.float32,
    )


class GlbPrimitive:
    __slots__ = ("name", "positions", "normals", "uvs", "indices")

    def __init__(
        self,
        name: str,
        positions: np.ndarray,
        normals: np.ndarray,
        uvs: Optional[np.ndarray],
        indices: np.ndarray,
    ):
        self.name = name
        self.positions = np.ascontiguousarray(positions, dtype=np.float32)
        self.normals = np.ascontiguousarray(normals, dtype=np.float32)
        self.uvs = (
            np.ascontiguousarray(uvs, dtype=np.float32) if uvs is not None else None
        )
        self.indices = np.ascontiguousarray(indices, dtype=np.uint32)


class GlbMesh:
    def __init__(self, primitives: List[GlbPrimitive]):
        self.primitives = primitives

    @property
    def face(self) -> Optional[GlbPrimitive]:
        for p in self.primitives:
            if p.name.lower() == "face":
                return p
        return self.primitives[-1] if self.primitives else None

    @property
    def body(self) -> Optional[GlbPrimitive]:
        for p in self.primitives:
            if p.name.lower() == "body":
                return p
        return self.primitives[0] if self.primitives else None

    @property
    def tyre(self) -> Optional[GlbPrimitive]:
        for p in self.primitives:
            if p.name.lower() in ("tyre", "tire"):
                return p
        return None


def load_glb(path: str | Path) -> GlbMesh:
    data = Path(path).read_bytes()
    if data[:4] != b"glTF":
        raise ValueError("Not a GLB file")
    _version, length = struct.unpack_from("<II", data, 4)
    off = 12
    js = None
    blob = None
    while off + 8 <= length:
        clen, ctype = struct.unpack_from("<I4s", data, off)
        chunk = data[off + 8 : off + 8 + clen]
        if ctype == b"JSON":
            js = json.loads(chunk)
        elif ctype.startswith(b"BIN"):
            blob = chunk
        off += 8 + clen
    if js is None or blob is None:
        raise ValueError("GLB missing JSON or BIN chunk")

    def accessor(acc_idx: int) -> np.ndarray:
        acc = js["accessors"][acc_idx]
        bv = js["bufferViews"][acc["bufferView"]]
        base = bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
        ctype = acc["componentType"]
        typ = acc["type"]
        count = acc["count"]
        ncomp = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[typ]
        fmt = {5126: "f", 5123: "H", 5125: "I", 5121: "B", 5120: "b"}[ctype]
        size = {5126: 4, 5123: 2, 5125: 4, 5121: 1, 5120: 1}[ctype]
        raw = blob[base : base + count * ncomp * size]
        dt = np.dtype("<" + fmt)
        if ncomp > 1:
            return np.frombuffer(raw, dtype=dt).reshape(count, ncomp).copy()
        return np.frombuffer(raw, dtype=dt).copy()

    # Node transform (rotation only for this file)
    rot = None
    for node in js.get("nodes", []):
        if "rotation" in node:
            rot = _quat_to_matrix(node["rotation"])
            break

    materials = js.get("materials", [])
    primitives: List[GlbPrimitive] = []
    for mesh in js.get("meshes", []):
        for prim in mesh.get("primitives", []):
            mat_idx = prim.get("material", 0)
            mat_name = materials[mat_idx]["name"] if mat_idx < len(materials) else "mat"
            attrs = prim["attributes"]
            pos = accessor(attrs["POSITION"]).astype(np.float32)
            nrm = (
                accessor(attrs["NORMAL"]).astype(np.float32)
                if "NORMAL" in attrs
                else np.zeros_like(pos)
            )
            uvs = (
                accessor(attrs["TEXCOORD_0"]).astype(np.float32)
                if "TEXCOORD_0" in attrs
                else None
            )
            idx = accessor(prim["indices"]).astype(np.uint32)
            if rot is not None:
                pos = pos @ rot.T
                nrm = nrm @ rot.T
                # re-normalize
                lengths = np.linalg.norm(nrm, axis=1, keepdims=True)
                lengths = np.maximum(lengths, 1e-8)
                nrm = nrm / lengths
            # Ensure face normals point toward +Z (camera side of the disc)
            if mat_name.lower() == "face":
                if nrm.mean(axis=0)[2] < 0:
                    nrm = -nrm
                # Barrel export has collapsed UVs (all vertices share one UV) —
                # rebuild planar UVs from the disc's X/Y positions.
                collapsed = uvs is None or len(np.unique(np.round(uvs, 5), axis=0)) <= 1
                if collapsed:
                    xy = pos[:, :2].astype(np.float64)
                    mn = xy.min(axis=0)
                    mx = xy.max(axis=0)
                    span = np.maximum(mx - mn, 1e-6)
                    uvs = ((xy - mn) / span).astype(np.float32)
            primitives.append(GlbPrimitive(mat_name, pos, nrm, uvs, idx))

    return GlbMesh(primitives)
