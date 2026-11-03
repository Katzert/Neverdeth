#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m3c_fatal_sites - lista las trampas REX_FATAL que quedan en un arbol generado."""
from __future__ import annotations

import argparse
import glob
import re
from pathlib import Path

FATAL_RE = re.compile(
    r'REX_FATAL\(\s*"Unresolved branch from 0x([0-9A-Fa-f]+) to 0x([0-9A-Fa-f]+)"')


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", type=Path, default=Path("generated/neverdead_default_v2"))
    ap.add_argument("--prefix", default=None,
                    help="solo los sitios cuyo from/to empieza por este prefijo hex")
    ap.add_argument("--limit", type=int, default=60)
    args = ap.parse_args(argv)

    sites = []
    for f in sorted(glob.glob(str(args.tree / "neverdead_recomp.*.cpp"))):
        t = Path(f).read_text(encoding="utf-8", errors="replace")
        for m in FATAL_RE.finditer(t):
            sites.append((m.group(1), m.group(2), Path(f).name))
    print(f"tree  : {args.tree}")
    print(f"sites : {len(sites)}")
    sel = sites
    if args.prefix:
        p = args.prefix.upper().replace("0X", "")
        sel = [s for s in sites
               if s[0].upper().startswith(p) or s[1].upper().startswith(p)]
        print(f"filtered by prefix {args.prefix}: {len(sel)}")
    for a, b, f in sel[: args.limit]:
        print(f"   from 0x{a} to 0x{b}   ({f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
