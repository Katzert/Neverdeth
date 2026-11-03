import glob, re

fatal_regex = re.compile(r'REX_FATAL\("([^"]+)"\)')
fatals = {}
locations = {}

for path in glob.glob('generated/neverdead_default/neverdead_recomp.*.cpp'):
    with open(path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    for idx, line in enumerate(lines):
        m = fatal_regex.search(line)
        if m:
            msg = m.group(1)
            fatals[msg] = fatals.get(msg, 0) + 1
            if msg not in locations:
                locations[msg] = f"{path}:{idx+1}"

print(f"Total distinct REX_FATAL patterns: {len(fatals)}")
# Filter those related to 0x824DD or 0x826BA or 0x826AD (the D3D9 / render path)
print("\n--- D3D9 / Render Path FATALs ---")
for msg, count in sorted(fatals.items()):
    if any(addr in msg for addr in ['824DD', '824DE', '824DF', '826AD', '826BA', '827BE']):
        print(f"  [{count}x] {locations[msg]} -> {msg}")
