import glob, re

pattern = re.compile(r'REX_FATAL\("Unresolved branch from (0x[0-9A-Fa-f]+) to (0x[0-9A-Fa-f]+)"\)')
matches = []
for p in sorted(glob.glob('generated/neverdead_default/neverdead_recomp.*.cpp')):
    with open(p, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
    for m in pattern.finditer(content):
        matches.append((m.group(1), m.group(2), p))

print(f"Total Unresolved branch sites: {len(matches)}")
for src, dst, p in matches[:20]:
    print(f"  {src} -> {dst} in {p}")
