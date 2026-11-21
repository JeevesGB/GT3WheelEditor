from __future__ import annotations
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from .gtm1 import GTM1


@dataclass
class GTTW:
    path: Optional[Path]
    raw: bytes
    flags: int
    gtm1: GTM1

    @classmethod
    def from_bytes(cls, data: bytes, path: Optional[Path] = None) -> "GTTW":
        if len(data) < 0x20:
            raise ValueError("File too small for GTTW header")

        magic = data[0:4]
        if magic != b"GTTW":
            raise ValueError(f"Not a GTTW file (magic={magic!r})")

        size = struct.unpack_from("<I", data, 0x0C)[0]
        flags = struct.unpack_from("<I", data, 0x10)[0]
        gtm1_off = struct.unpack_from("<I", data, 0x1C)[0]

        if gtm1_off == 0:
            gtm1_off = 0x20

        if gtm1_off >= len(data):
            raise ValueError(f"Invalid GTM1 offset {gtm1_off}")

        gtm1_data = data[gtm1_off:]
        gtm1 = GTM1.from_bytes(gtm1_data)

        return cls(path=path, raw=data, flags=flags, gtm1=gtm1)

    @classmethod
    def from_file(cls, path: str | Path) -> "GTTW":
        path = Path(path)
        data = path.read_bytes()
        return cls.from_bytes(data, path=path)

    def to_bytes(self) -> bytes:

        gtm1_bytes = self.gtm1.to_bytes()
        header = bytearray(self.raw[:0x20])
        return bytes(header) + gtm1_bytes

    def save(self, path: Optional[str | Path] = None) -> None:
        out = Path(path) if path else self.path
        if out is None:
            raise ValueError("No output path given")
        out.write_bytes(self.to_bytes())
