#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
m3c_runtime_scan - extrae de un log del motor las direcciones de invitado
implicadas en un fallo, para cerrar el bucle de semilla de M3c (O2).

Patrones cubiertos (con sus variantes):
  * "No function registered at X"            -> X  (falta la entrada en el registro)
  * "XThread::Execute - No function ..."     -> X
  * "[FATAL] Unresolved branch from X to Y"  -> X y Y (la rama y su destino)
  * "[FATAL] Unresolved bl from X to Y"      -> X y Y
  * "TRAP-OOB caller lr=X target=Y"          -> Y (destino fuera de rango)
  * "Unresolved conditional branch to X"     -> X
  * "function not in function table" / "not registered" -> X

Uso:
    python tools/m3c_runtime_scan.py <log> [--json PATH] [--known FILE]
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

PATTERNS: list[tuple[str, re.Pattern]] = [
    ("no_function_registered",
     re.compile(r"No function registered at\s+(0x[0-9A-Fa-f]+|[0-9A-Fa-f]{8})")),
    ("unresolved_branch",
     re.compile(r"Unresolved branch from\s+(0x[0-9A-Fa-f]+|[0-9A-Fa-f]{8})\s+to\s+"
                r"(0x[0-9A-Fa-f]+|[0-9A-Fa-f]{8})")),
    ("unresolved_bl",
     re.compile(r"Unresolved bl from\s+(0x[0-9A-Fa-f]+|[0-9A-Fa-f]{8})\s+to\s+"
                r"(0x[0-9A-Fa-f]+|[0-9A-Fa-f]{8})")),
    ("unresolved_conditional",
     re.compile(r"Unresolved conditional branch to\s+(0x[0-9A-Fa-f]+|[0-9A-Fa-f]{8})")),
    ("trap_oob",
     re.compile(r"TRAP-OOB caller lr=(0x[0-9A-Fa-f]+|[0-9A-Fa-f]{8})\s+target="
                r"(0x[0-9A-Fa-f]+|[0-9A-Fa-f]{8})")),
    ("not_in_table",
     re.compile(r"function not in function table[:\s]*(0x[0-9A-Fa-f]+|[0-9A-Fa-f]{8})", re.I)),
    ("not_registered",
     re.compile(r"not registered at\s+(0x[0-9A-Fa-f]+|[0-9A-Fa-f]{8})", re.I)),
]

# El destino de un TRAP-OOB suele ser un valor (no una direccion de codigo):
# no se propaga a la semilla.  Solo se propaga el lr cuando es plausible.
CODE_BASE = 0x821A0000
CODE_END = 0x827C05E4


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("log", type=Path)
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--known", type=Path, default=None,
                    help="fichero con direcciones ya conocidas (0x... por linea)")
    args = ap.parse_args(argv)

    lines = args.log.read_text(encoding="utf-8", errors="replace").splitlines()
    findings: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for i, ln in enumerate(lines, 1):
        for kind, rx in PATTERNS:
            for m in rx.finditer(ln):
                vals = [int(g, 16) for g in m.groups()]
                for v in vals:
                    if not (CODE_BASE <= v < CODE_END):
                        continue
                    key = (kind, v)
                    if key in seen:
                        continue
                    seen.add(key)
                    findings.append({
                        "kind": kind, "va": "0x%08X" % v,
                        "line": i, "text": ln.strip()[:220],
                    })

    known: set[int] = set()
    if args.known and args.known.is_file():
        known = {int(m, 16) for m in
                 re.findall(r"0x([0-9A-Fa-f]{8})", args.known.read_text(encoding="utf-8"))}

    targets = sorted({int(f["va"], 16) for f in findings} - known)
    print(f"log        : {args.log}")
    print(f"lines      : {len(lines)}")
    print(f"findings   : {len(findings)}")
    for f in findings:
        print(f"   {f['kind']:24s} {f['va']}  (line {f['line']})")
    print(f"new targets (not in known): {len(targets)}")
    for v in targets:
        print(f"   0x{v:08X}")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(
            {"log": str(args.log), "lines": len(lines), "findings": findings,
             "new_targets": ["0x%08X" % v for v in targets]}, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
