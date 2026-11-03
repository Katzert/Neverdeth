#!/usr/bin/env python3
"""m3b_goto_targets.py - extract the distinct cross-TU goto target VAs from the
O3 audit JSON and emit them as a python list / plain text list."""
import json
import re
import sys
from pathlib import Path

audit = Path(sys.argv[1] if len(sys.argv) > 1 else ".agents/teamwork/worker/m3b_goto_audit.json")
seed_path = Path(sys.argv[2] if len(sys.argv) > 2 else "nd_seed_overrides_v2.toml")
mode = sys.argv[3] if len(sys.argv) > 3 else "stats"

d = json.loads(audit.read_text(encoding="utf-8"))
rows = d["rows"]
addrs = sorted({int(r["va"], 16) for r in rows})
seed = {int(m, 16) for m in re.findall(
    r'\[functions\."(0x[0-9A-Fa-f]+)"\]', seed_path.read_text(encoding="utf-8"))}

if mode == "py":
    lines = []
    for i in range(0, len(addrs), 8):
        lines.append("    " + " ".join(f"0x{a:08X}," for a in addrs[i:i + 8]))
    print("\n".join(lines))
elif mode == "txt":
    for a in addrs:
        print(f"0x{a:08X}")
else:
    print(f"distinct targets        : {len(addrs)}")
    print(f"already in seed v2      : {len([a for a in addrs if a in seed])}")
    print(f"new (not in seed v2)    : {len([a for a in addrs if a not in seed])}")
    print(f"min / max               : 0x{min(addrs):08X} .. 0x{max(addrs):08X}")
    print(f"pdata begins            : {[r['va'] for r in rows if r['is_pdata_begin']]}")
    print(f"labels with no owner    : {len([r for r in rows if not r['owner_tu']])}")
    print(f"targets that are sub_*  : {len([r for r in rows if r['is_func_start_in_tree']])}")
