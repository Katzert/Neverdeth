#!/usr/bin/env python3
"""m3b_range.py - list the seed entries in a VA range with their provenance."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_seed_from_pdata import (M3B_CROSS_TU_GOTO_TARGETS,
                                   REQUIRED_NODE_ADDRESSES, read_seed)

lo = int(sys.argv[1], 16)
hi = int(sys.argv[2], 16)
seed = read_seed(Path(sys.argv[3] if len(sys.argv) > 3 else "nd_seed_overrides_v2.toml"))
m2b = set(REQUIRED_NODE_ADDRESSES)
m3b = set(M3B_CROSS_TU_GOTO_TARGETS)
acc = set()
ap = Path(__file__).resolve().parent / "m3b_node_addresses.txt"
if ap.is_file():
    for line in ap.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            acc.add(int(line, 16))

for va in sorted(seed):
    if lo <= va < hi:
        prov = []
        if va in m2b:
            prov.append("M2B")
        if va in m3b:
            prov.append("M3B")
        if va in acc:
            prov.append("ACC")
        print(f"0x{va:08X} size={seed[va]:6d}  end=0x{va+seed[va]:08X}  {'+'.join(prov) or '-'}")
