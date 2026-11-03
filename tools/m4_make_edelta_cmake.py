#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
m4_make_edelta_cmake - genera tools/rexglue_v2_edelta.cmake a partir de
tools/rexglue_v2.cmake (derivado MECANICAMENTE: una sola sustitucion de ruta).

Owner: Worker (M4).  El build del experimento E-Delta usa un arbol COPIA, asi
que el cmake que lo compila tiene que apuntar a esa copia.

    python tools/m4_make_edelta_cmake.py --tree <copia> [--out tools/rexglue_v2_edelta.cmake]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OLD = "generated/neverdead_default_v2"
OLD_ABS = '"${CMAKE_CURRENT_SOURCE_DIR}/' + OLD


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", required=True)
    ap.add_argument("--out", default=str(ROOT / "tools" / "rexglue_v2_edelta.cmake"))
    args = ap.parse_args(argv)

    tree = Path(args.tree)
    tree_posix = tree.as_posix()
    src = (ROOT / "tools" / "rexglue_v2.cmake").read_text(encoding="utf-8")
    out = src.replace(OLD_ABS, '"' + tree_posix)
    out = out.replace(OLD, tree_posix)
    out = out.replace(
        "# M3.3 (swe_native_3d_v2): el build de v2 NO re-ejecuta el codegen.",
        "# M4 (E-Delta, swe_native_3d_v2): build del ARBOL COPIA del experimento\n"
        "# E-Delta.  Derivado mecanicamente de tools/rexglue_v2.cmake (una sola\n"
        "# sustitucion de ruta).  El arbol de producto NO se toca.\n"
        "# El build de v2 NO re-ejecuta el codegen.",
    )
    Path(args.out).write_text(out, encoding="utf-8")
    print(f"escrito: {args.out}")
    for i, line in enumerate(out.splitlines(), 1):
        if "tree_edelta" in line or tree_posix in line:
            print(f"  {i}: {line.strip()[:120]}")
            if i > 70:
                break
    return 0


if __name__ == "__main__":
    sys.exit(main())
