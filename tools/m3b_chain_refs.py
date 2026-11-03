#!/usr/bin/env python3
"""m3b_chain_refs.py - which functions referenced by the apply_*.py chain are
MISSING from a given recompiled tree?  A missing reference breaks the build
("use of undeclared identifier 'sub_XXX'")."""
import re
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
tree = Path(sys.argv[2] if len(sys.argv) > 2 else "generated/neverdead_default_v2")

have = set()
for p in sorted(tree.glob("neverdead_recomp.*.cpp")):
    t = p.read_text(encoding="utf-8", errors="replace")
    have |= set(re.findall(r"DEFINE_REX_FUNC\(\s*(sub_[0-9A-Fa-f]+)\s*\)", t))

refs: dict[str, list[str]] = {}
for p in sorted(root.glob("apply_*.py")):
    t = p.read_text(encoding="utf-8", errors="replace")
    for name in set(re.findall(r"\bsub_[0-9A-Fa-f]{8}\b", t)):
        refs.setdefault(name, []).append(p.name)

missing = {k: v for k, v in refs.items() if k not in have}
print(f"tree          : {tree}")
print(f"functions in tree: {len(have)}")
print(f"chain references  : {len(refs)}")
print(f"MISSING in tree   : {len(missing)}")
for k in sorted(missing):
    print(f"  {k}  <- {', '.join(sorted(set(missing[k])))}")
