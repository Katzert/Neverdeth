"""Scan the decrypted NeverDead image for lis/addi/b tail-call thunks in gaps.

Class of thunk (12 bytes):
    lis  r11, HI
    addi r3, r11, LO
    b    target

They sit after a terminator (blr/b/bctr/padding) inside a .pdata extent, so the
codegen never emits them as entry points. When the guest reaches one through a
computed jump, the runtime aborts with:
    FATAL: Call to invalid or unregistered function at guest address 0x........

This tool emits config/thunks_tail.toml declaring them as standalone 12-byte
functions. Only blocks that start right after a terminator are included, which
keeps interior labels of real functions out.

Usage:
    python tools/scan_tail_thunks.py [--image tools/scratch_disasm/default_dec.bin]
                                     [--out config/thunks_tail.toml] [--dry-run]
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

BASE = 0x82000000
PDATA_RVA = 0x16F800
PDATA_SIZE = 0x26600
CODE_START = 0x1A0000
CODE_END = 0x7C05E4
SIZE = 0x0C

HEADER = """# Tail-call thunks lis/addi/b en huecos tras terminador (no .pdata).
# Generado por tools/scan_tail_thunks.py.
# Patron: lis r11,imm; addi r3,r11,imm; b target (12 bytes).
# Sin declararlos, un salto indirecto del invitado provoca FATAL 'unregistered function'.

"""


def word(img: bytes, off: int) -> int:
    return struct.unpack_from(">I", img, off)[0]


def is_terminator(w: int) -> bool:
    return w in (0x4E800020, 0x4E800420, 0) or (w & 0xFC000000) == 0x48000000


def pdata_starts(img: bytes) -> set[int]:
    out = set()
    for i in range(0, PDATA_SIZE, 8):
        begin = struct.unpack_from(">I", img, PDATA_RVA + i)[0]
        if begin:
            out.add(begin)
    return out


def declared(paths: list[Path]) -> set[int]:
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
    while i < CODE_END - SIZE:
        a, b, c = word(img, i), word(img, i + 4), word(img, i + 8)
        if (
            (a & 0xFFFF0000) == 0x3D600000
            and (b & 0xFFFF0000) == 0x386B0000
            and (c & 0xFC000000) == 0x48000000
        ):
            hits.append(i)
            i += SIZE
        else:
            i += 4
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default="tools/scratch_disasm/default_dec.bin")
    ap.add_argument("--out", default="config/thunks_tail.toml")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    img = Path(args.image).read_bytes()
    starts = pdata_starts(img)
    decl = declared(
        [Path("config/vtables.toml"), Path("config/gap_chunks.toml"), Path("config/thunks.toml"),
         Path(args.out)]
    )

    hits = scan(img)
    gap = [
        h for h in hits
        if is_terminator(word(img, h - 4))
        and (BASE + h) not in starts
        and (BASE + h) not in decl
    ]

    print(f"image={args.image} hits={len(hits)} gap_after_terminator={len(gap)}")
    if args.dry_run:
        for h in gap:
            print(f"0x{BASE + h:08X}")
        return 0

    parts = [HEADER]
    for h in gap:
        addr = BASE + h
        parts.append(
            f'[functions."0x{addr:08X}"]\nsize = 0x{SIZE:02X}\nname = "nd_tail_{addr:08X}"\n\n'
        )
    Path(args.out).write_text("".join(parts), encoding="utf-8")
    print(f"wrote {args.out}: {len(gap)} entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
