import re
import glob
from pathlib import Path

re_fatal = re.compile(r'REX_FATAL\("Unresolved call from (0x[0-9A-Fa-f]+) to (0x[0-9A-Fa-f]+)"\);')
re_func = re.compile(r'(?m)^DEFINE_REX_FUNC\(([A-Za-z0-9_]+)\)')

found = 0
for f in sorted(glob.glob('generated/neverdead_default/neverdead_recomp.*.cpp')):
    txt = Path(f).read_text(encoding='utf-8', errors='replace')
    for m in re_fatal.finditer(txt):
        pos = m.start()
        funcs = [mf for mf in re_func.finditer(txt) if mf.start() <= pos]
        if not funcs: continue
        fn = funcs[-1].group(1)
        fn_start = funcs[-1].start()
        next_fn = re_func.search(txt, funcs[-1].end())
        fn_end = next_fn.start() if next_fn else len(txt)
        body = txt[fn_start:fn_end]
        dst = m.group(2)[2:].upper()
        if f'loc_{dst}:' in body:
            found += 1
            print(f"{Path(f).name}: {fn} {m.group(1)} -> {m.group(2)} has loc_{dst}: in body!")

print(f"Total found: {found}")
