#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
m3c_case_c - procedencia (regla A/B/C + terminador) de unas direcciones en la
semilla v2, con los bytes del invitado verificados por Capstone.

Uso:
    python tools/m3c_case_c.py 0x8221DEA4 0x8221DED0 0x8221DEE4 [--seed nd_seed_overrides_v2.toml]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from build_seed_from_pdata import (GUEST_BASE, Guest, Pdata, parse_pdata,  # noqa: E402
                                   read_seed, walk_block, Sweep)

DEFAULT_BIN = Path(r"C:\Users\katze\.gemini\antigravity-ide\scratch\vibe-recomp"
                   r"\NeverDead_base.exe")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("vas", nargs="+")
    ap.add_argument("--seed", type=Path, default=Path("nd_seed_overrides_v2.toml"))
    ap.add_argument("--bin", type=Path, default=DEFAULT_BIN)
    args = ap.parse_args(argv)

    g = Guest(args.bin)
    pd = Pdata(parse_pdata(g))
    sw = Sweep(g)
    seed = read_seed(args.seed) if args.seed.is_file() else {}
    for a in args.vas:
        va = int(a, 16)
        own = pd.containing(va)
        print(f"0x{va:08X}:")
        if own and own[0] == va:
            print(f"   regla A: inicio de .pdata 0x{own[0]:08X}..0x{own[2]:08X} "
                  f"(size real {own[2]-own[0]})")
        elif own:
            print(f"   regla B: etiqueta interior de .pdata "
                  f"0x{own[0]:08X}..0x{own[2]:08X}")
        else:
            blk = walk_block(g, va, pd, sw)
            print(f"   regla C: fuera de todo .pdata; bloque hasta 0x{blk.end:08X} "
                  f"(size {blk.size}), terminador '{blk.reason}' "
                  f"({blk.n_insn} instr){'  [acotado]' if blk.marked else ''}")
            print(f"            bytes invitado: {g.data[g.off(va):g.off(va)+min(blk.size,32)].hex(' ')}")
        print(f"   semilla v2: size={seed.get(va)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
