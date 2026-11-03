#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
m4_marker_census - censo de marcadores PATCH(...) por script, en un arbol dado.

Owner: Worker (M4).  Mide los marcadores de los 5 scripts que nunca alcanzaban
el arbol v2 (B-1 del Critic) y el total del arbol.

    python tools/m4_marker_census.py --gen generated/neverdead_default_v2
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

TAG_RE = re.compile(r"PATCH\(([a-z0-9-]+)\)")
ROOT = Path(__file__).resolve().parent.parent

# Los 5 scripts de B-1 (Critic) + los que la cadena debe cubrir.
FIVE = [
    "neverdead-heap-alloc-fix",
    "neverdead-asura-init-fix",
    "neverdead-dec8-fix",
    "neverdead-a0cc-loop-fix",
    "neverdead-string-crt-fix",
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gen", default="generated/neverdead_default_v2")
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)

    gen = Path(args.gen)
    if not gen.is_absolute():
        gen = ROOT / gen
    files = sorted(list(gen.glob("neverdead_recomp.*.cpp")) + list(gen.glob("neverdead_funcs.*.h")))
    tags: dict[str, int] = {}
    per_file: dict[str, int] = {}
    total = 0
    for p in files:
        text = p.read_text(encoding="utf-8", errors="replace")
        hits = TAG_RE.findall(text)
        if hits:
            per_file[p.name] = len(hits)
        for t in hits:
            tags[t] = tags.get(t, 0) + 1
            total += 1

    print(f"# tree = {gen}   files = {len(files)}   total PATCH markers = {total}")
    print(f"{'tag':<44}{'occ':>5}")
    print("-" * 52)
    for t, n in sorted(tags.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"{t:<44}{n:>5}")
    print()
    five = {t: tags.get(t, 0) for t in FIVE}
    print("los 5 scripts de B-1 (Critic):")
    for t, n in five.items():
        print(f"  {t:<40} {n}")
    print(f"  {'TOTAL 5':<40} {sum(five.values())}")
    if args.json:
        Path(args.json).write_text(json.dumps(
            {"gen": str(gen), "total": total, "tags": tags, "five": five}, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
