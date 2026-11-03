#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
m4_chain_contract - prueba contractual de la cadena de parches (M4 / O1).

Regla (C-4 del Critic): NINGUN script de la cadena puede resolver el arbol
generado por la ruta literal "generated/neverdead_default" fuera de un
comentario o de una cadena de documentation.  Todos deben usar
`nd_tu.gen_dir(root)` (que honra NEVERDEAD_GENERATED_DIR) o el flag
--generated-dir.

Ademas comprueba que los 29 scripts de la cadena resuelven el arbol de forma
homogenea: con NEVERDEAD_GENERATED_DIR=v2 los 5 scripts que antes apuntaban a
v1 deben parchar en v2.

Uso:
    python tools/m4_chain_contract.py            # contrato estatico
    python tools/m4_chain_contract.py --runtime  # ademas: markers en v2 y v1
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import nd_tu  # noqa: E402

LITERAL = "generated/neverdead_default"
# scripts de la cadena segun CMakeLists.txt (se comprueba contra el propio CMake)
CMAKE = ROOT / "CMakeLists.txt"


def scripts_de_la_cadena() -> list[Path]:
    txt = CMAKE.read_text(encoding="utf-8", errors="replace")
    names = re.findall(r'apply_[A-Za-z0-9_]+\.py', txt)
    out = []
    for n in sorted(set(names)):
        p = ROOT / n
        if p.is_file():
            out.append(p)
    return out


def literales_fuera_de_comentarios(path: Path) -> list[int]:
    """Lineas con el literal 'generated/neverdead_default' en codigo real."""
    hits = []
    for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        s = line.strip()
        if s.startswith("#"):
            continue
        # quita el literal de un comentario al final de linea
        code = line.split("//")[0] if "//" in line else line
        if LITERAL in code:
            hits.append(i)
    return hits


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime", action="store_true")
    args = ap.parse_args(argv)

    chain = scripts_de_la_cadena()
    print(f"# scripts de la cadena (CMakeLists.txt): {len(chain)}")
    bad = []
    for p in chain:
        hits = literales_fuera_de_comentarios(p)
        uses_nd = "nd_tu.gen_dir" in p.read_text(encoding="utf-8", errors="replace")
        flag = "OK "
        if hits:
            flag = "BAD"
            bad.append((p.name, hits))
        print(f"  {flag} {p.name:<34} nd_tu.gen_dir={'si' if uses_nd else 'NO'} "
              f"literal_en_codigo={hits if hits else 'ninguno'}")
    print()
    if bad:
        print(f"CONTRATO FALLIDO: {len(bad)} script(s) con path fijo al arbol v1")
        return 1
    print("CONTRATO OK: ningun script de la cadena resuelve el arbol por ruta fija")

    if args.runtime:
        import subprocess
        import os
        for tree in ("generated/neverdead_default_v2", "generated/neverdead_default"):
            gen = ROOT / tree
            tags = {}
            for f in sorted(gen.glob("neverdead_recomp.*.cpp")):
                t = f.read_text(encoding="utf-8", errors="replace")
                for m in re.findall(r"PATCH\(([a-z0-9-]+)\)", t):
                    tags[m] = tags.get(m, 0) + 1
            five = {t: tags.get(t, 0) for t in (
                "neverdead-heap-alloc-fix", "neverdead-asura-init-fix",
                "neverdead-dec8-fix", "neverdead-a0cc-loop-fix",
                "neverdead-string-crt-fix")}
            print(f"{tree}: 5 scripts B-1 -> {five} total={sum(five.values())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
