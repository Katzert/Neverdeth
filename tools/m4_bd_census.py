#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
m4_bd_census - censo de la familia 0x8278BDxx en un arbol generado.

Owner: Worker (M4).  Para cada `sub_8278BDxx` presente en el arbol imprime:
  TU, nº de instrucciones (comentarios de instruccion), y las instrucciones.

Uso:
    python tools/m4_bd_census.py --tree generated/neverdead_default_v2
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "tools"))
import nd_tu  # noqa: E402
from m4_body import extract, instr_comments  # noqa: E402

FUNC_RE = re.compile(r"DEFINE_REX_FUNC\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)")


def census(gen: Path, pattern: str) -> dict[str, tuple[str, list[str]]]:
    out: dict[str, tuple[str, list[str]]] = {}
    for cpp in sorted(gen.glob("neverdead_recomp.*.cpp")):
        txt = cpp.read_text(encoding="utf-8", errors="replace")
        for fn in FUNC_RE.findall(txt):
            if not re.fullmatch(pattern, fn):
                continue
            body = extract(txt, fn)
            if body is None:
                continue
            out[fn] = (cpp.name, instr_comments(body))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", default="generated/neverdead_default_v2")
    ap.add_argument("--pattern", default=r"sub_8278BD[0-9A-Fa-f]+")
    ap.add_argument("--show", action="store_true", help="imprime las instrucciones")
    args = ap.parse_args(argv)

    gen = Path(args.tree)
    if not gen.is_absolute():
        gen = _ROOT / gen
    if not gen.is_dir():
        gen = nd_tu.gen_dir(_ROOT)
    c = census(gen, args.pattern)
    print(f"# tree={gen}  n_funcs={len(c)}")
    for fn in sorted(c, key=lambda s: int(s[8:], 16)):
        tu, instrs = c[fn]
        print(f"{fn}  tu={tu}  n_instr={len(instrs)}")
        if args.show:
            for i in instrs:
                print(f"      {i}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
