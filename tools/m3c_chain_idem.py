#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
m3c_chain_idem - comprueba que la cadena de parches queda en punto fijo sobre
un arbol generado: ejecuta los apply_*.py de la cadena (en el orden de
CMakeLists.txt) y compara el hash del arbol antes/después.

Uso:
    python tools/m3c_chain_idem.py --tree DIR [--rounds 2] [--json-out PATH]
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CMAKE = ROOT / "CMakeLists.txt"


def tree_hash(d: Path) -> tuple[str, int, int]:
    entries, total = [], 0
    if not d.exists():
        return ("", 0, 0)
    for p in sorted(d.rglob("*")):
        if p.is_file():
            entries.append(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  "
                           f"{p.relative_to(d).as_posix()}")
            total += p.stat().st_size
    return (hashlib.sha256("\n".join(entries).encode()).hexdigest(), len(entries), total)


def chain_scripts() -> list[str]:
    """Scripts de la cadena en el ORDEN DE EJECUCION de CMake.

    CMakeLists.txt declara los scripts en `add_custom_target(...)` y las
    dependencias en `add_dependencies(A B)` (B se ejecuta antes que A).  El
    orden del TEXTO no es el orden de ejecucion: p.ej. neverdead_trap_include
    esta declarado a mitad de fichero pero depende de 824dc230_fmv_fix, asi
    que corre al final.  Se reconstruye el orden con un sort topologico.
    """
    txt = CMAKE.read_text(encoding="utf-8", errors="replace")
    target_script: dict[str, str] = {}
    for m in re.finditer(r"add_custom_target\(\s*(neverdead_[A-Za-z0-9_]+)(.*?)\n\s*\)",
                         txt, re.S):
        name, block = m.group(1), m.group(2)
        s = re.search(r'\$\{CMAKE_CURRENT_SOURCE_DIR\}/([A-Za-z0-9_./]+\.py)', block)
        if s:
            target_script[name] = s.group(1)
    # aristas: add_dependencies(A B)  =>  B antes que A
    edges: dict[str, set[str]] = {t: set() for t in target_script}
    for m in re.finditer(r"add_dependencies\(\s*(neverdead_[A-Za-z0-9_]+)\s+"
                         r"(neverdead_[A-Za-z0-9_]+)\s*\)", txt):
        a, b = m.group(1), m.group(2)
        if a in edges and b in target_script:
            edges[a].add(b)
    ordered: list[str] = []
    seen: set[str] = set()

    def visit(t: str) -> None:
        if t in seen:
            return
        seen.add(t)
        for dep in edges.get(t, ()):
            visit(dep)
        s = target_script.get(t)
        if s and s not in ordered:
            ordered.append(s)

    for t in target_script:
        visit(t)
    # los scripts referenciados fuera de un add_custom_target (p.ej. los
    # sub-scripts que invoca apply_bink_branch_fix.py) se anaden al final
    for s in re.findall(r'\$\{CMAKE_CURRENT_SOURCE_DIR\}/([A-Za-z0-9_./]+\.py)', txt):
        if s not in ordered:
            ordered.append(s)
    return ordered


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", type=Path, required=True)
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--json-out", type=Path, default=None)
    args = ap.parse_args(argv)

    tree = args.tree.resolve()
    scripts = chain_scripts()
    print(f"tree    : {tree}")
    print(f"scripts : {len(scripts)} de la cadena")
    env = dict(os.environ)
    env["NEVERDEAD_GENERATED_DIR"] = os.path.relpath(tree, ROOT) \
        if str(tree).startswith(str(ROOT)) else str(tree)
    history = []
    prev = None
    for r in range(1, args.rounds + 1):
        before = tree_hash(tree)
        results = []
        for s in scripts:
            p = ROOT / s
            if not p.is_file():
                continue
            res = subprocess.run([sys.executable, str(p)], cwd=str(ROOT),
                                 capture_output=True, text=True, errors="replace",
                                 env=env, timeout=1800)
            results.append({"script": s, "exit": res.returncode})
        after = tree_hash(tree)
        row = {"round": r, "before": before, "after": after,
               "changed": before[0] != after[0], "results": results}
        history.append(row)
        print(f"ronda {r}: hash {after[0][:16]}  ficheros {after[1]}  "
              f"bytes {after[2]}  cambiado={row['changed']}")
        bad = [x for x in results if x["exit"] != 0]
        if bad:
            print(f"  scripts con exit != 0: {[(x['script'], x['exit']) for x in bad]}")
        prev = after
    if args.json_out:
        args.json_out.write_text(__import__("json").dumps(history, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
