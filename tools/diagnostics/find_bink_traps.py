import glob
import re

bink_traps = []
for f in sorted(glob.glob("generated/neverdead_default/neverdead_recomp.*.cpp")):
    with open(f, "r", encoding="utf-8", errors="ignore") as fp:
        for idx, line in enumerate(fp):
            if "Unresolved branch from 0x821" in line:
                m = re.search(r'REX_FATAL\("Unresolved branch from (0x[0-9A-Fa-f]+) to (0x[0-9A-Fa-f]+)"\);', line)
                if m:
                    src = int(m.group(1), 16)
                    dst = int(m.group(2), 16)
                    if 0x821A0000 <= src <= 0x821B2100:
                        bink_traps.append((f, idx + 1, m.group(1), m.group(2), line.strip()))

print(f"Total Bink unresolved branch traps found: {len(bink_traps)}")
for f, line_num, src, dst, text in bink_traps:
    print(f"{f}:{line_num} {src} -> {dst}")
