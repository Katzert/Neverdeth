#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
M3.1 - Comparacion de cuerpos (secuencia de instrucciones del invitado) entre
dos arboles recompilados, funcion a funcion.

Orculo: Capstone-64 sobre la imagen plana del invitado; aqui se usa el
commentario de instruccion que el propio codegen emite (`// <mnemonic> <ops>`),
que es la MISMA transcripcion que verifico el codegen.

Uso:
  python tools/m3_compare_bodies.py --a DIR_A --b DIR_B [--vas 0x8278BD60,...]
  python tools/m3_compare_bodies.py --a DIR_A --b DIR_B --diff-all
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

FUNC_RE = re.compile(r"^DEFINE_REX_FUNC\((\w+)\) \{$", re.M)
INSN_RE = re.compile(r"^\t// (.+)$")
NOT_PREFIX = (
    "Error", "Warning", "TODO", "NOTE", "PATCH", "FATAL", "unresolved",
    "Unresolved", "codegen", "Codegen", "guest", "Guest", "REX",
)


def tree_bodies(root: Path) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for cpp in sorted(root.glob("neverdead_recomp.*.cpp")):
        lines = cpp.read_text(encoding="utf-8", errors="replace").splitlines()
        starts = []
        for i, line in enumerate(lines):
            m = FUNC_RE.match(line)
            if m:
                starts.append((i, m.group(1)))
        for idx, (i, name) in enumerate(starts):
            end = starts[idx + 1][0] if idx + 1 < len(starts) else len(lines)
            m = re.match(r"sub_([0-9A-Fa-f]{8})", name)
            if not m:
                continue
            va = int(m.group(1), 16)
            instrs = []
            for line in lines[i:end]:
                cm = INSN_RE.match(line)
                if not cm:
                    continue
                t = cm.group(1)
                if t.startswith(NOT_PREFIX):
                    continue
                if not re.match(r"^[a-z][a-z0-9.]*\s", t + " "):
                    continue
                instrs.append(t)
            out[va] = {"file": cpp.name, "instrs": instrs, "n": len(instrs)}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", type=Path, required=True)
    ap.add_argument("--b", type=Path, required=True)
    ap.add_argument("--vas", default="")
    ap.add_argument("--diff-all", action="store_true")
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()

    A = tree_bodies(a.a)
    B = tree_bodies(a.b)
    res = {"a": str(a.a), "b": str(a.b), "n_a": len(A), "n_b": len(B)}

    if a.vas:
        vas = [int(x, 16) for x in a.vas.replace(",", " ").split()]
        rows = []
        for va in vas:
            fa, fb = A.get(va), B.get(va)
            rows.append(
                {
                    "va": f"0x{va:08X}",
                    "a_file": fa["file"] if fa else None,
                    "b_file": fb["file"] if fb else None,
                    "a_n": fa["n"] if fa else None,
                    "b_n": fb["n"] if fb else None,
                    "equal": (fa is not None and fb is not None and fa["instrs"] == fb["instrs"]),
                    "a_instrs": fa["instrs"] if fa else None,
                    "b_instrs": fb["instrs"] if fb else None,
                }
            )
        res["rows"] = rows
        for r in rows:
            mark = "IGUAL" if r["equal"] else "DISTINTO"
            print(
                f"{r['va']}  {mark:9} A={r['a_file']} n={r['a_n']}   "
                f"B={r['b_file']} n={r['b_n']}"
            )
    if a.diff_all:
        diff = []
        for va in sorted(set(A) | set(B)):
            ia = A.get(va, {}).get("instrs")
            ib = B.get(va, {}).get("instrs")
            if ia != ib:
                diff.append(
                    {
                        "va": f"0x{va:08X}",
                        "a_n": len(ia) if ia is not None else None,
                        "b_n": len(ib) if ib is not None else None,
                        "a_file": A.get(va, {}).get("file"),
                        "b_file": B.get(va, {}).get("file"),
                    }
                )
        res["n_diff"] = len(diff)
        res["diff"] = diff
        print(f"funciones con cuerpos distintos: {len(diff)} / {len(set(A) | set(B))}")
    if a.json:
        a.json.write_text(json.dumps(res, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
