#!/usr/bin/env python3
"""m3b_dup_funcs.py - find guest functions defined in more than one TU."""
import re
import sys
from collections import Counter
from pathlib import Path

tree = Path(sys.argv[1] if len(sys.argv) > 1 else "generated/neverdead_default_v2")
rx = re.compile(r"DEFINE_REX_FUNC\((sub_[0-9A-Fa-f]+)\)")
c: Counter = Counter()
where: dict[str, list[str]] = {}
for p in sorted(tree.glob("neverdead_recomp.*.cpp")):
    for m in rx.finditer(p.read_text(encoding="utf-8", errors="replace")):
        c[m.group(1)] += 1
        where.setdefault(m.group(1), []).append(p.name)
dups = {k: v for k, v in c.items() if v > 1}
print(f"tree            : {tree}")
print(f"DEFINE_REX_FUNC : {sum(c.values())}")
print(f"distinct        : {len(c)}")
print(f"duplicated      : {len(dups)}")
for k in sorted(dups):
    print(f"  {k} x{dups[k]} -> {', '.join(where[k])}")
