#!/usr/bin/env python3
"""M6 worker: map a guest VA to the guest RUNTIME_FUNCTION (pdata) that contains it.

The auditor's `containing()` has a fallback that returns a NON-containing entry
(binary-search result) when no pdata range covers the VA.  Here we report the
truth: every range that really contains the VA, and the most specific one.

pdata entries are (Begin, Unwind, End) big-endian 12-byte records inside .text.
"""
import struct, pathlib, sys

BASE = pathlib.Path(r"C:\Users\katze\.gemini\antigravity-ide\scratch\vibe-recomp\NeverDead_base.exe")
IMAGE_BASE = 0x82000000
TEXT_LO, TEXT_HI = 0x821B2200, 0x827C05E4
data = BASE.read_bytes()

PDATA_RAW = 0x16F800
PDATA_LEN = 0x238D0

entries = []
for i in range(PDATA_LEN // 12):
    b, u, e = struct.unpack_from(">III", data, PDATA_RAW + i * 12)
    if TEXT_LO <= b < TEXT_HI and TEXT_LO <= e <= TEXT_HI:
        entries.append((b, u, e))
entries.sort()


def covering(va):
    return [(b, u, e) for (b, u, e) in entries if b <= va < e]


def most_specific(va):
    c = covering(va)
    if not c:
        return None
    return max(c, key=lambda t: t[0])


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        va = int(arg, 0)
        cov = covering(va)
        ms = most_specific(va)
        print(f"VA 0x{va:08X}: {len(cov)} rango(s) pdata que lo cubren")
        for b, u, e in cov:
            mark = "  <-- mas especifico" if ms and (b, u, e) == ms else ""
            print(f"    0x{b:08X}..0x{e:08X} (unwind 0x{u:08X}){mark}")
        if not cov:
            print("    NINGUNO (la heuristica del auditor devolvio un rango que NO lo contiene)")
        print()
