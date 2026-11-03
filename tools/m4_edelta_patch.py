#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
m4_edelta_patch - E-Delta: sustituye el cuerpo de sub_8278BD80 por el bloque
REAL del invitado (13 instrucciones, 0x8278BD80..0x8278BDB0) en una COPIA del
arbol generado.

Owner: Worker (M4).  Esto es un EXPERIMENTO DIAGNOSTICO, no un arreglo: el
arreglo definitivo va en la semilla (cola compartida solapada).  El script
nunca escribe en el arbol de producto salvo que se le pase explicitamente.

La composicion del cuerpo NO es inventada: se toma de las transcripciones
fieles que el propio codegen (o el script de la familia BD) ya escribieron
para sub_8278BD80 (2 primeras instrucciones), sub_8278BD84 (las 2 siguientes)
y sub_8278BD88 (las 11 ultimas, cola compartida), y el resultado se VERIFICA
con Capstone contra la imagen plana del invitado (tools/bd_truth.py):
el cuerpo final debe coincidir instruction a instruction con el invitado.

    python tools/m4_edelta_patch.py --tree <copia del arbol> [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "tools"))
sys.path.insert(0, str(_ROOT))

import bd_truth  # noqa: E402
import apply_bd_family_faithful as bdfam  # noqa: E402
from m4_body import extract  # noqa: E402

TARGET = "sub_8278BD80"
PARTS = ["sub_8278BD80", "sub_8278BD84", "sub_8278BD88"]


def find_tu(tree: Path, fn: str) -> Path | None:
    for cpp in sorted(tree.glob("neverdead_recomp.*.cpp")):
        txt = cpp.read_text(encoding="utf-8", errors="replace")
        if f"DEFINE_REX_FUNC({fn})" in txt:
            return cpp
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", required=True, help="arbol generado (copia)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    tree = Path(args.tree)
    if not tree.is_absolute():
        tree = _ROOT / tree
    if not tree.is_dir():
        print(f"arbol no encontrado: {tree}", file=sys.stderr)
        return 2

    tu = find_tu(tree, TARGET)
    if tu is None:
        print(f"{TARGET} no encontrado en {tree}", file=sys.stderr)
        return 2
    txt = tu.read_text(encoding="utf-8", errors="replace")
    eol = "\r\n" if "\r\n" in txt else "\n"

    # 1) composicion: las tres transcripciones fieles ya presentes en el arbol
    #    (el codegen las reparte en TUs distintos: se lee cada una donde este)
    content_parts = []
    for p in PARTS:
        ptu = find_tu(tree, p)
        if ptu is None:
            print(f"falta {p} en {tree}", file=sys.stderr)
            return 2
        ptext = ptu.read_text(encoding="utf-8", errors="replace")
        body = bdfam._extract_body(ptext, p)
        if body is None:
            print(f"falta el cuerpo de {p} en {ptu.name}", file=sys.stderr)
            return 2
        c = bdfam._guest_content(body, eol)
        content_parts.append(c)
    content = "\n".join(content_parts)

    # 2) verificacion con Capstone contra el invitado
    va = int(TARGET[4:], 16)
    ok, why = bd_truth.is_faithful(va, content)
    n_guest = len(bd_truth.guest_block(va))
    print(f"[E-DELTA] {TARGET} @ {tu.name}")
    print(f"[E-DELTA] bloque del invitado: {n_guest} instrucciones")
    print(f"[E-DELTA] verificacion Capstone: {'FIEL' if ok else 'NO FIEL'} ({why})")
    if not ok:
        return 2

    # 3) re-empaquetado con la misma sonda BDFAM-P que usa la cadena
    new_block = bdfam._build_block(TARGET, content, eol)
    old_block = bdfam._extract_body(txt, TARGET)
    if old_block == new_block:
        print("[E-DELTA] el cuerpo ya es el bloque real del invitado: no se toca")
        return 0
    if args.dry_run:
        print("[E-DELTA] --dry-run: no se escribe")
        return 0

    new_txt = bdfam._replace_body(txt, TARGET, new_block)
    new_txt = bdfam._normalize_separator(new_txt, TARGET, eol)
    tmp = tu.with_suffix(tu.suffix + ".edelta")
    tmp.write_text(new_txt, encoding="utf-8", newline="")
    os.replace(tmp, tu)
    print(f"[E-DELTA] escrito: {tu.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
