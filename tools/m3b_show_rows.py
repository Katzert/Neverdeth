#!/usr/bin/env python3
"""m3b_show_rows.py - print the rows of an O3 audit JSON in a readable table."""
import json
import sys
from pathlib import Path

d = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
rows = d["rows"]
print(f"rows={len(rows)}")
for r in rows:
    print(f"{r['label']:14s} {r['va']:12s} n={r['n_sites']:2d} "
          f"src={','.join(r['source_tus'])} owner={r['owner_tu']} "
          f"ownerFunc={r['owner_func']} seed={r['in_seed_v2']} sz={r['seed_size']} "
          f"fstart={r['is_func_start_in_tree']} fstartTU={r['func_start_tu']} "
          f"pdata={r['pdata_range']} insn={r['guest_insn']} "
          f"exSite={r['example_site']}")
