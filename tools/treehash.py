#!/usr/bin/env python3
"""M6 worker: SHA-256 over the whole generated tree (sorted path+content)."""
import hashlib, pathlib, sys

root = pathlib.Path(r"C:\Users\katze\.gemini\antigravity-ide\scratch\vibe-recomp\neverdead_project")
gen = root / "generated" / "neverdead_default"
files = sorted(p for p in gen.rglob("*") if p.is_file())
agg = hashlib.sha256()
per = {}
for p in files:
    h = hashlib.sha256(p.read_bytes()).hexdigest()
    rel = str(p.relative_to(gen)).replace("\\", "/")
    per[rel] = h
    agg.update(rel.encode())
    agg.update(h.encode())
print("TREE", agg.hexdigest(), len(files))
if len(sys.argv) > 1:
    out = pathlib.Path(sys.argv[1])
    out.write_text("".join(f"{h}  {r}\n" for r, h in sorted(per.items())), encoding="utf-8")
    print("per-file written:", out)
