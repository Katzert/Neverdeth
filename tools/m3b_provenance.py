#!/usr/bin/env python3
"""m3b_provenance.py - where do the remaining cross-TU goto targets come from?

Prints, for every target of the O3 audit, which list declares it:
  M2B  = REQUIRED_NODE_ADDRESSES      (the 672 of the M2b loop)
  M3B  = M3B_CROSS_TU_GOTO_TARGETS     (the 318 of O3)
  M1   = the original 1206-entry seed (nd_seed_overrides.toml)
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_seed_from_pdata import (M3B_CROSS_TU_GOTO_TARGETS,
                                   REQUIRED_NODE_ADDRESSES, read_seed)

audit = Path(sys.argv[1] if len(sys.argv) > 1 else ".agents/teamwork/worker/m3b_goto_audit.json")
d = json.loads(audit.read_text(encoding="utf-8"))
rows = d["rows"]
m2b = set(REQUIRED_NODE_ADDRESSES)
m3b = set(M3B_CROSS_TU_GOTO_TARGETS)
m1 = set(read_seed(Path("nd_seed_overrides.toml")))

tally = {}
for r in rows:
    va = int(r["va"], 16)
    prov = []
    if va in m2b:
        prov.append("M2B")
    if va in m3b:
        prov.append("M3B")
    if va in m1:
        prov.append("M1")
    key = "+".join(prov) or "NONE"
    tally.setdefault(key, []).append(r)

for key in sorted(tally):
    print(f"=== {key}: {len(tally[key])} targets ===")
    for r in tally[key]:
        print(f"   {r['va']} owner={r['owner_tu']} fstart={r['is_func_start_in_tree']} "
              f"seed={r['in_seed_v2']} sz={r['seed_size']}")
