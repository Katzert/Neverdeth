#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
m4_body - extrae y describe el cuerpo de una DEFINE_REX_FUNC de un arbol generado.

Owner: Worker (M4).  Uso:
    python tools/m4_body.py --tree generated/neverdead_default_v2 sub_8278BD60
    python tools/m4_body.py --tree generated/neverdead_default_v2 --all sub_8278BD6*

El cuerpo se imprime con los comentarios de instruccion del propio codegen
(``// <mnemonic> <operands>``) que son la transcripcion del invitado.
"""
from __future__ import annotations

import argparse
import fnmatch
import re
import sys
from pathlib import Path

FUNC_RE = re.compile(r"DEFINE_REX_FUNC\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)")

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "tools"))
import nd_tu  # noqa: E402


def extract(text: str, fn: str) -> str | None:
    """Cuerpo (texto entre llaves) de DEFINE_REX_FUNC(fn)."""
    m = re.search(rf"DEFINE_REX_FUNC\(\s*{re.escape(fn)}\s*\)\s*\{{", text)
    if not m:
        return None
    i = m.end() - 1  # posicion de '{'
    depth = 0
    j = i
    n = len(text)
    while j < n:
        c = text[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[i + 1 : j]
        j += 1
    return None


def instr_comments(body: str) -> list[str]:
    """Comentarios de instruccion del cuerpo (lineas '// <mnem> ...')."""
    out = []
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("//") and not s.startswith("//----") and not s.startswith("// PATCH"):
            out.append(s[2:].strip())
    return out


def find_tree(tree: Path) -> Path:
    p = Path(tree)
    if p.is_absolute() and p.is_dir():
        return p
    cand = _ROOT / p
    if cand.is_dir():
        return cand
    return nd_tu.gen_dir(_ROOT)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", default="generated/neverdead_default_v2")
    ap.add_argument("--all", action="store_true", help="lista todos los sub_*/loc_* que coincidan")
    ap.add_argument("names", nargs="+")
    args = ap.parse_args(argv)

    gen = find_tree(args.tree)
    files = sorted(gen.glob("neverdead_recomp.*.cpp"))
    if not files:
        print(f"no TUs under {gen}", file=sys.stderr)
        return 2

    wanted: list[str] = []
    for n in args.names:
        if any(ch in n for ch in "*?["):
            wanted.extend(
                fn
                for fn in _all_func_names(files)
                if fnmatch.fnmatch(fn, n)
            )
        else:
            wanted.append(n)

    rc = 0
    for fn in dict.fromkeys(wanted):
        found = False
        for cpp in files:
            txt = cpp.read_text(encoding="utf-8", errors="replace")
            if f"DEFINE_REX_FUNC({fn})" not in txt:
                continue
            body = extract(txt, fn)
            if body is None:
                print(f"{fn}: <no body>")
                found = True
                rc = 1
                continue
            instrs = instr_comments(body)
            print(f"=== {fn}  [{cpp.name}]  instr={len(instrs)} chars={len(body)}")
            for ins in instrs:
                print(f"    {ins}")
            found = True
        if not found:
            print(f"{fn}: NOT FOUND in {gen}")
            rc = 1
    return rc


def _all_func_names(files) -> list[str]:
    names: list[str] = []
    for cpp in files:
        txt = cpp.read_text(encoding="utf-8", errors="replace")
        names.extend(FUNC_RE.findall(txt))
    return names


if __name__ == "__main__":
    sys.exit(main())
