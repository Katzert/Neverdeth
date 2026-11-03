#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
m3c_seed_probe - utilidades de sondeo para la mision M3c (swe_native_3d_v2).

Sub-comandos
------------
seed      : resumen de una semilla TOML (entradas, solapes, rango, región)
mapping   : tabla de mapeos PPCFuncMapping de un árbol generado (init.cpp)
           -> detecta entradas con va == 0 (rompen el centinela {0,nullptr})
diffmap   : diferencia de las tablas de mapeo entre dos árboles
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SEED_RE = re.compile(r'\[functions\."(0x[0-9A-Fa-f]+)"\]\s*\nsize = (\d+)')
MAP_RE = re.compile(r"\{\s*(0x[0-9A-Fa-f]+)\s*,\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}")


def load_seed(path: Path) -> dict[int, int]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return {int(m, 16): int(s) for m, s in SEED_RE.findall(text)}


def seed_overlaps(seed: dict[int, int]) -> list[tuple[int, int]]:
    items = sorted(seed.items())
    out = []
    for (a, sa), (b, sb) in zip(items, items[1:]):
        if a + sa > b:
            out.append((a, b))
    return out


def load_mappings(init_cpp: Path) -> list[tuple[int, str]]:
    text = init_cpp.read_text(encoding="utf-8", errors="replace")
    i = text.find("PPCFuncMapping PPCFuncMappings[]")
    if i < 0:
        return []
    j = text.find("};", i)
    arr = text[i:j]
    return [(int(a, 16), n) for a, n in MAP_RE.findall(arr)]


def cmd_seed(args: argparse.Namespace) -> int:
    seed = load_seed(args.path)
    ov = seed_overlaps(seed)
    print(f"file           : {args.path}")
    print(f"entries        : {len(seed)}")
    print(f"overlaps       : {len(ov)}")
    for a, b in ov[:10]:
        print(f"   overlap 0x{a:08X} -> 0x{b:08X}")
    if seed:
        print(f"min/max        : 0x{min(seed):08X} .. 0x{max(seed):08X}")
    lo, hi = args.range
    inreg = [(a, s) for a, s in sorted(seed.items()) if lo <= a <= hi]
    print(f"in range       : {len(inreg)}")
    for a, s in inreg[: args.limit]:
        print(f"   0x{a:08X} size={s}")
    return 0


def cmd_mapping(args: argparse.Namespace) -> int:
    maps = load_mappings(args.path)
    zero = [(a, n) for a, n in maps if a == 0]
    print(f"file           : {args.path}")
    print(f"entries        : {len(maps)}")
    print(f"entries va==0  : {len(zero)}")
    for a, n in zero:
        print(f"   ZERO-VA -> {n}")
    sent = [i for i, (a, n) in enumerate(maps) if a == 0 and n == "nullptr"]
    print(f"sentinel index : {sent}")
    if maps:
        print(f"first entry    : 0x{maps[0][0]:08X} {maps[0][1]}")
        print(f"last entry     : 0x{maps[-1][0]:08X} {maps[-1][1]}")
    return 0


def cmd_diffmap(args: argparse.Namespace) -> int:
    a = load_mappings(args.a)
    b = load_mappings(args.b)
    da = {x: n for x, n in a}
    db = {x: n for x, n in b}
    only_a = sorted(set(da) - set(db))
    only_b = sorted(set(db) - set(da))
    diff = sorted(x for x in set(da) & set(db) if da[x] != db[x])
    print(f"A {args.a}: {len(a)} entries")
    print(f"B {args.b}: {len(b)} entries")
    print(f"only in A      : {len(only_a)}")
    print(f"only in B      : {len(only_b)}")
    print(f"name differs   : {len(diff)}")
    for x in only_a[: args.limit]:
        print(f"   A-only 0x{x:08X} {da[x]}")
    for x in only_b[: args.limit]:
        print(f"   B-only 0x{x:08X} {db[x]}")
    for x in diff[: args.limit]:
        print(f"   DIFF 0x{x:08X} {da[x]} -> {db[x]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="M3c seed/mapping probes")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("seed", help="resumen de una semilla TOML")
    p.add_argument("path", type=Path)
    p.add_argument("--range", nargs=2, type=lambda s: int(s, 0), default=(0, 0))
    p.add_argument("--limit", type=int, default=40)
    p.set_defaults(fn=cmd_seed)

    p = sub.add_parser("mapping", help="tabla de mapeos de un init.cpp")
    p.add_argument("path", type=Path)
    p.set_defaults(fn=cmd_mapping)

    p = sub.add_parser("diffmap", help="diff de tablas de mapeo")
    p.add_argument("a", type=Path)
    p.add_argument("b", type=Path)
    p.add_argument("--limit", type=int, default=40)
    p.set_defaults(fn=cmd_diffmap)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
