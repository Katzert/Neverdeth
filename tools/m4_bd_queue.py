#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
m4_bd_queue - analiza la cola compartida 0x8278BDxx del invitado (oráculo Capstone).

Owner: Worker (M4).  NO inventa semántica: sólo lee bytes con Capstone y los
metadatos de `.pdata`.

Salida:
  * desensamblado lineal de la región pedida;
  * para cada etiqueta interior: cuántos sitios `b` / `bl` la alcanzan y si esos
    sitios están DENTRO o FUERA del `.pdata` que la contiene;
  * qué entradas del seed actual existen y con qué `size` declarado.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "tools"))
import build_seed_from_pdata as B  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", type=Path, default=_ROOT.parent / "NeverDead_base.exe")
    ap.add_argument("--begin", default="0x8278BD24")
    ap.add_argument("--end", default="0x8278BDB4")
    ap.add_argument("--seed", type=Path, default=_ROOT / "nd_seed_overrides_v2.toml")
    args = ap.parse_args(argv)

    g = B.Guest(args.bin)
    pd = B.Pdata(B.parse_pdata(g))
    sw = B.Sweep(g)

    begin, end = int(args.begin, 0), int(args.end, 0)
    n = (end - begin) // 4
    print(f"--- desensamblado {begin:#x}..{end:#x} ({n} palabras) ---")
    for ins in g.md.disasm(g.data[g.off(begin):g.off(end)], begin):
        iva, mnem, ops = ins.address, ins.mnemonic, ins.op_str
        tgt = ""
        tok = ops.split(",")[0].strip()
        if mnem.startswith("b") and tok.startswith("0x"):
            t = int(tok, 16)
            own = pd.containing(iva)
            inside = own is not None and own[0] <= t < own[2]
            tgt = f"   ; target_in_same_pdata={inside} target_pdata={pd.containing(t)}"
        print(f"0x{iva:08X}: {mnem} {ops}{tgt}")

    print()
    print("--- sitios que alcanzan las etiquetas de la region ---")
    seed = B.read_seed(args.seed) if args.seed.is_file() else {}
    labels = [va for va in sorted(set(list(seed.keys()) + list(range(begin, end, 4))))]
    for va in labels:
        if not (begin <= va < end):
            continue
        sites = sw.sites.get(va, [])
        if not sites and va not in seed:
            continue
        own = pd.containing(va)
        b_out = b_in = bl_out = bl_in = 0
        for src, mnem in sites:
            host = pd.containing(src)
            inside = host is not None and own is not None and host[0] <= src < host[2]
            if mnem == "b":
                if inside:
                    b_in += 1
                else:
                    b_out += 1
            else:
                if inside:
                    bl_in += 1
                else:
                    bl_out += 1
        # "reachable from outside its own interval" criterion
        reach = b_out + bl_out
        print(
            f"0x{va:08X}: seed_size={seed.get(va)}  "
            f"b_in={b_in} b_out={b_out} bl_in={bl_in} bl_out={bl_out}  "
            f"REACH_FROM_OUTSIDE={'YES' if reach else 'no'}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
