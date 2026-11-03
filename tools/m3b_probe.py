#!/usr/bin/env python3
"""m3b_probe.py - probe .pdata / seed membership / guest bytes for a few VAs."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_seed_from_pdata import GUEST_BASE, Guest, Pdata, parse_pdata, read_seed

g = Guest(Path(sys.argv[1]))
pd = Pdata(parse_pdata(g))
seeds = {}
for p in sys.argv[3:]:
    seeds[p] = read_seed(Path(p))

for arg in sys.argv[2].split(","):
    va = int(arg, 16)
    own = pd.containing(va)
    line = f"0x{va:08X}: "
    if own:
        line += f"pdata 0x{own[0]:08X}..0x{own[2]:08X} (size {own[2]-own[0]})"
    else:
        line += "pdata NONE (outside every .pdata)"
    for name, s in seeds.items():
        line += f" | {name}: {'size ' + str(s[va]) if va in s else 'NOT IN SEED'}"
    print(line)
