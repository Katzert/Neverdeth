import re, glob
from collections import Counter
from pathlib import Path

root = Path("generated/neverdead_default")
branches = Counter()
calls = Counter()

for f in sorted(root.glob("neverdead_recomp.*.cpp")):
    txt = f.read_text(encoding="utf-8", errors="replace")
    for m in re.finditer(r'REX_FATAL\("Unresolved branch from (0x[0-9A-Fa-f]+) to (0x[0-9A-Fa-f]+)"\)', txt):
        branches[(m.group(1).upper(), m.group(2).upper())] += 1
    for m in re.finditer(r'REX_FATAL\("Unresolved call from (0x[0-9A-Fa-f]+) to (0x[0-9A-Fa-f]+)"\)', txt):
        calls[(m.group(1).upper(), m.group(2).upper())] += 1

print(f"Unique unresolved branches: {len(branches)}")
print("Top 20 branches:")
for (src, dst), c in branches.most_common(20):
    print(f"  {src} -> {dst}: {c} instances")

print(f"\nUnique unresolved calls: {len(calls)}")
print("Top 20 calls:")
for (src, dst), c in calls.most_common(20):
    print(f"  {src} -> {dst}: {c} instances")
