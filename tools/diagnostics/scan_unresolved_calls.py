import glob, re

matches = []
for p in sorted(glob.glob('generated/neverdead_default/neverdead_recomp.*.cpp')):
    with open(p, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
    for m in re.finditer(r'REX_FATAL\("Unresolved call from (0x[0-9A-Fa-f]+) to (0x[0-9A-Fa-f]+)"\);', content):
        matches.append((m.group(1), m.group(2), p))

print(f"Total Unresolved call sites: {len(matches)}")
for src, dst, p in matches:
    print(f"  {src} -> {dst} in {p}")
