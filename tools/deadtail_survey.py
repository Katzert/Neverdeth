#!/usr/bin/env python3
"""M6 worker: count, in the generated tree, the call sites of a guest function
that are NOT followed by `return;` (i.e. the guest code after the call is
dead code in the guest but executed by the recompilation).

usage: deadtail_survey.py <sub_XXXXXXXX> [...]
"""
import pathlib, re, sys

GEN = pathlib.Path(r"C:\Users\katze\.gemini\antigravity-ide\scratch\vibe-recomp\neverdead_project\generated\neverdead_default")

names = sys.argv[1:]
callres = {n: re.compile(re.escape(n) + r"\s*\(\s*ctx\s*,\s*base\s*\)") for n in names}

total_sites = 0
total_dead = 0
per_fn = {}
examples = []
for f in sorted(GEN.glob("neverdead_recomp.*.cpp")):
    lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
    for i, l in enumerate(lines):
        for n, rx in callres.items():
            if rx.search(l) and not l.strip().startswith("//"):
                total_sites += 1
                # what is the next non-blank, non-comment statement?
                j = i + 1
                nxt = None
                while j < len(lines):
                    s = lines[j].strip()
                    if s and not s.startswith("//"):
                        nxt = s
                        break
                    j += 1
                if nxt is not None and nxt != "return;":
                    total_dead += 1
                    # find the enclosing DEFINE_REX_FUNC
                    k = i
                    while k >= 0 and "DEFINE_REX_FUNC(" not in lines[k]:
                        k -= 1
                    fn = lines[k].strip() if k >= 0 else "?"
                    per_fn[fn] = per_fn.get(fn, 0) + 1
                    if len(examples) < 25:
                        examples.append((f.name, i + 1, fn, nxt))

print(f"call-sites totales: {total_sites}   con codigo muerto despues: {total_dead}")
print(f"funciones afectadas: {len(per_fn)}")
for fn, c in sorted(per_fn.items(), key=lambda kv: -kv[1])[:40]:
    print(f"   {c:4d}  {fn}")
print()
for e in examples:
    print(f"   {e[0]}:{e[1]}  {e[2]}  -> siguiente: {e[3]}")
