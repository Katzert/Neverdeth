#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
m4_seed_audit - auditoria del seed v2 contra los bytes del invitado (Capstone).

Owner: Worker (M4).  Para cada entrada del seed:
  * size declarado
  * "real" = longitud del bloque recorrido con Capstone (terminador incondicional)
  * "pdata" = extent real de .pdata (si aplica)
  * clasificacion: OK | TRUNC (size < real) | OVER (size > real) | CASE_A
  * numero de sitios `b`/`bl` que alcanzan la etiqueta desde FUERA del .pdata
    que la contiene (criterio natural de "cola compartida").

Uso:
    python tools/m4_seed_audit.py --seed nd_seed_overrides_v2.toml [--json OUT]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "tools"))
import build_seed_from_pdata as B  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=Path, default=_ROOT / "nd_seed_overrides_v2.toml")
    ap.add_argument("--bin", type=Path, default=_ROOT.parent / "NeverDead_base.exe")
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args(argv)

    g = B.Guest(args.bin)
    pd = B.Pdata(B.parse_pdata(g))
    sw = B.Sweep(g)
    seed = B.read_seed(args.seed)

    rows = []
    stats = {"total": 0, "caseA": 0, "trunc": 0, "over": 0, "ok": 0, "equal": 0}
    for va in sorted(seed):
        size = seed[va]
        own = pd.containing(va)
        blk = B.walk_block(g, va, pd, sw)
        if own is not None and own[0] == va:
            klass = "CASE_A"
            stats["caseA"] += 1
        elif size < blk.size:
            klass = "TRUNC"
            stats["trunc"] += 1
        elif size > blk.size:
            klass = "OVER"
            stats["over"] += 1
        else:
            klass = "OK"
            stats["equal"] += 1
        # sitios externos (fuera del .pdata que contiene la etiqueta)
        b_out = 0
        bl_out = 0
        for src, mnem in sw.sites.get(va, ()):
            if own is not None and own[0] <= src < own[2]:
                continue
            if mnem == "b":
                b_out += 1
            elif mnem == "bl":
                bl_out += 1
        stats["total"] += 1
        rows.append({
            "va": f"0x{va:08X}", "declared": size, "walked": blk.size,
            "n_insn": blk.n_insn, "terminator": blk.terminator, "klass": klass,
            "b_out": b_out, "bl_out": bl_out,
            "in_pdata": own is not None,
            "pdata": None if own is None else [f"0x{own[0]:08X}", f"0x{own[2]:08X}"],
        })

    # sub-conjunto truncado y alcanzable desde fuera (el bucle)
    bad = [r for r in rows if r["klass"] == "TRUNC" and (r["b_out"] or r["bl_out"])]
    print(json.dumps(stats, indent=2))
    print(f"TRUNC y alcanzable desde fuera: {len(bad)}")
    for r in bad[:40]:
        print(f"  {r['va']} decl={r['declared']} walked={r['walked']} "
              f"n_insn={r['n_insn']} term={r['terminator']} b_out={r['b_out']} bl_out={r['bl_out']}")

    if args.json:
        Path(args.json).write_text(json.dumps({"stats": stats, "rows": rows}, indent=2), encoding="utf-8")
        print(f"json -> {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
