#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
M3 - Indice de un arbol recompilado: funcion (VA) -> TU, y TU -> funciones.

Uso:
  python tools/m3_tu_index.py --tree generated/neverdead_default_v2 --json out.json
  python tools/m3_tu_index.py --compare generated/neverdead_default generated/neverdead_default_v2

Produce el mapa VA->TU de cada arbol y el cuadro de correspondencia
v1_TU / v2_TU por funcion, que es lo que permite reconciliar la cadena de
parches cuando el codegen cambia el reparto de TUs.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

FUNC_RE = re.compile(r"DEFINE_REX_FUNC\((sub_[0-9A-Fa-f]+)\)")
LABEL_RE = re.compile(r"^\s*(loc_[0-9A-Fa-f]+):", re.M)
VA_RE = re.compile(r"sub_([0-9A-Fa-f]{8})\b")


def index_tree(tree: Path) -> dict:
    va2tu: dict[str, str] = {}
    tu2va: dict[str, list[str]] = {}
    labels: dict[str, list[str]] = {}
    nfunc = 0
    for cpp in sorted(tree.glob("neverdead_recomp.*.cpp")):
        txt = cpp.read_text(encoding="utf-8", errors="replace")
        for m in FUNC_RE.finditer(txt):
            name = m.group(1)
            if name in va2tu:
                continue
            va2tu[name] = cpp.name
            tu2va.setdefault(cpp.name, []).append(name)
            nfunc += 1
        for m in LABEL_RE.finditer(txt):
            labels.setdefault(m.group(1), []).append(cpp.name)
    return {
        "tree": str(tree),
        "n_funcs": nfunc,
        "n_tus": len(tu2va),
        "va2tu": va2tu,
        "tu2va": tu2va,
        "labels": labels,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", type=Path)
    ap.add_argument("--compare", type=Path, nargs=2)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()

    if a.compare:
        t1, t2 = a.compare
        i1, i2 = index_tree(t1), index_tree(t2)
        moved, same, only1, only2 = [], 0, [], []
        for name, tu in i1["va2tu"].items():
            if name not in i2["va2tu"]:
                only1.append((name, tu))
                continue
            if i2["va2tu"][name] == tu:
                same += 1
            else:
                moved.append((name, tu, i2["va2tu"][name]))
        for name in i2["va2tu"]:
            if name not in i1["va2tu"]:
                only2.append((name, i2["va2tu"][name]))
        print(f"v1: {i1['n_funcs']} funciones en {i1['n_tus']} TUs")
        print(f"v2: {i2['n_funcs']} funciones en {i2['n_tus']} TUs")
        print(f"misma TU     : {same}")
        print(f"TU DISTINTA  : {len(moved)}")
        print(f"solo en v1   : {len(only1)}")
        print(f"solo en v2   : {len(only2)}")
        if a.json:
            a.json.write_text(
                json.dumps(
                    {
                        "v1": i1,
                        "v2": i2,
                        "moved": moved,
                        "same": same,
                        "only_v1": only1,
                        "only_v2": only2,
                    },
                    indent=1,
                ),
                encoding="utf-8",
            )
        return 0

    if not a.tree:
        ap.error("--tree or --compare required")
    idx = index_tree(a.tree)
    print(
        f"{idx['tree']}: {idx['n_funcs']} funciones en {idx['n_tus']} TUs, "
        f"{len(idx['labels'])} labels"
    )
    if a.json:
        a.json.write_text(json.dumps(idx, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
