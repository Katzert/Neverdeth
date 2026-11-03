"""Scan the decrypted NeverDead image for leaf vcall thunks missing from .pdata.

Class of thunk (16 bytes):
    lwz r11, 0(r3)
    lwz r10, OFF(r11)
    mtctr r10
    bctr

They live in gaps between .pdata extents, so the codegen never emits them and
the runtime aborts with:
    FATAL: Call to invalid or unregistered function at guest address 0x........

This tool emits config/thunks.toml declaring them as chunks of their containing
.pdata function (parent/end), which keeps the no-overlap rule for standalone
functions.

Usage:
    python tools/scan_vcall_thunks.py [--image tools/scratch_disasm/default_dec.bin]
                                      [--out config/thunks.toml] [--dry-run]

The image is the decrypted XEX payload where file offset == RVA (image base
0x82000000). If it does not exist, decrypt assets/default.xex first (see
tools/unpack_assets_xex.py).
"""

from __future__ import annotations

import argparse
import bisect
import struct
from pathlib import Path

BASE = 0x82000000
PDATA_RVA = 0x16F800
PDATA_SIZE = 0x26600
CODE_START = 0x1A0000
CODE_END = 0x7C05E4
PATTERN_FIRST = bytes.fromhex("81630000")   # lwz r11,0(r3)
PATTERN_MTCTR = bytes.fromhex("7d4903a6")   # mtctr r10
PATTERN_BCTR = bytes.fromhex("4e800420")    # bctr

HEADER = """# Thunks hoja que NO estan en .pdata (clase: llamada virtual).
#
# Generado por tools/scan_vcall_thunks.py a partir de la imagen descifrada.
# Patron: lwz r11,0(r3); lwz r10,OFF(r11); mtctr r10; bctr  (16 bytes).
# El codegen solo conoce entradas .pdata; sin estas entradas el dispatcher
# lanza FATAL 'Call to invalid or unregistered function'.
#
# Se declaran como CHUNK con parent = inicio .pdata contenedor para no violar
# la regla de no-solapamiento de funciones standalone.

"""


def pdata_starts(img: bytes) -> list[int]:
    starts = set()
    for i in range(0, PDATA_SIZE, 8):
        begin, _unwind = struct.unpack_from(">II", img, PDATA_RVA + i)
        if begin:
            starts.add(begin)
    return sorted(starts)


def declared_addresses(paths: list[Path]) -> set[int]:
    out: set[int] = set()
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line.startswith('[functions."0x'):
                try:
                    out.add(int(line.split('"')[1], 16))
                except (IndexError, ValueError):
                    pass
    return out


def scan(img: bytes) -> list[int]:
    hits: list[int] = []
    i = CODE_START
    while i < CODE_END - 16:
        if (
            img[i : i + 4] == PATTERN_FIRST
            and img[i + 8 : i + 12] == PATTERN_MTCTR
            and img[i + 12 : i + 16] == PATTERN_BCTR
        ):
            hits.append(BASE + i)
            i += 16
        else:
            i += 4
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default="tools/scratch_disasm/default_dec.bin")
    ap.add_argument("--out", default="config/thunks.toml")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    img = Path(args.image).read_bytes()
    starts = pdata_starts(img)
    decl = declared_addresses(
        [Path("config/vtables.toml"), Path("config/gap_chunks.toml"), Path(args.out)]
    )

    hits = scan(img)
    missing = [a for a in hits if a not in starts and a not in decl]

    print(f"image={args.image} size={len(img):,}")
    print(f"pdata_starts={len(starts)} thunk_hits={len(hits)} missing={len(missing)}")

    if args.dry_run:
        for a in missing:
            print(f"0x{a:08X}")
        return 0

    def parent_of(addr: int) -> int:
        j = bisect.bisect_right(starts, addr) - 1
        return starts[j] if j >= 0 else 0

    parts = [HEADER]
    for addr in missing:
        parent = parent_of(addr)
        if parent == 0:
            parts.append(
                f'[functions."0x{addr:08X}"]\nsize = 0x10\nname = "nd_vcall_{addr:08X}"\n\n'
            )
        else:
            parts.append(
                f'[functions."0x{addr:08X}"]\n'
                f"parent = {parent}   # 0x{parent:08X}\n"
                f"end = {addr + 0x10}      # 0x{addr + 0x10:08X}\n"
                f'name = "nd_vcall_{addr:08X}"\n\n'
            )

    Path(args.out).write_text("".join(parts), encoding="utf-8")
    print(f"wrote {args.out}: {len(missing)} entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
