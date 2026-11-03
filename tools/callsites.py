#!/usr/bin/env python3
"""M6 worker: find every call site of a guest function in generated/neverdead_default
and report the ctx.lr that the codegen sets just before the call.

usage: callsites.py <sub_XXXXXXXX> [--ctx N]
"""
import pathlib, re, sys

GEN = pathlib.Path(r"C:\Users\katze\.gemini\antigravity-ide\scratch\vibe-recomp\neverdead_project\generated\neverdead_default")
name = sys.argv[1]
maxhits = 400

call = re.compile(re.escape(name) + r"\s*\(\s*ctx\s*,\s*base\s*\)")
lr = re.compile(r"ctx\.lr\s*=\s*(0x[0-9A-Fa-f]+)\s*;")

hits = []
for f in sorted(GEN.glob("neverdead_recomp.*.cpp")):
    lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
    for i, l in enumerate(lines):
        if call.search(l) and not l.strip().startswith("//"):
            # find the nearest preceding "ctx.lr = " within 6 lines
            val = None
            for j in range(i - 1, max(-1, i - 8), -1):
                m = lr.search(lines[j])
                if m:
                    val = m.group(1)
                    break
            hits.append((f.name, i + 1, val, l.strip()))

print(f"call-sites de {name}: {len(hits)}")
from collections import Counter
c = Counter(h[2] for h in hits)
for v, n in c.most_common(30):
    print(f"   ctx.lr={v}  x{n}")
print()
if "--ctx" in sys.argv:
    want = sys.argv[sys.argv.index("--ctx") + 1]
    for h in hits:
        if h[2] and h[2].lower() == want.lower():
            print(f"   {h[0]}:{h[1]}  lr={h[2]}  | {h[3]}")
