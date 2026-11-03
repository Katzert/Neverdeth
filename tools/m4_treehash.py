#!/usr/bin/env python3
"""m4_treehash - SHA-256 sobre un arbol generado (ruta parametrizable)."""
import hashlib
import pathlib
import sys

root = pathlib.Path(r"C:\Users\katze\.gemini\antigravity-ide\scratch\vibe-recomp\neverdead_project")
gen = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else root / "generated" / "neverdead_default"
if not gen.is_absolute():
    gen = root / gen
files = sorted(p for p in gen.rglob("*") if p.is_file())
agg = hashlib.sha256()
per = {}
for p in files:
    h = hashlib.sha256(p.read_bytes()).hexdigest()
    rel = str(p.relative_to(gen)).replace("\\", "/")
    per[rel] = h
    agg.update(rel.encode())
    agg.update(h.encode())
print("TREE", agg.hexdigest(), len(files), sum(p.stat().st_size for p in files))
if len(sys.argv) > 2:
    out = pathlib.Path(sys.argv[2])
    out.write_text("".join(f"{h}  {r}\n" for r, h in sorted(per.items())), encoding="utf-8")
    print("per-file written:", out)
