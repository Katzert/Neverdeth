#!/usr/bin/env python3
"""
audit_share_registers - cierra la clase de bug "chunk emitido como funcion".

POR QUE
-------
`config/functions.toml` declara 15.457 bloques con `parent` y 897 con
`share_registers = true`. Un chunk con `share_registers` NO es una funcion: es
un tramo interior del `parent` que depende del banco de registros que el padre
dejo vivo (r24..r31, r29 como `this`, etc.). Si el codegen lo emite como
`DEFINE_REX_FUNC` independiente y alguien lo invoca con `bl`, el callee lee
registros que no son suyos.

Este script NO parchea: clasifica. Produce la lista cerrada de candidatos con
evidencia de runtime, que es el criterio de aceptacion de F2 del plan.

SALIDA
------
Para cada chunk `share_registers` presente en el arbol:

  * SIN_PROLOGO   la primera instruccion no es `stwu r1,-N(r1)` ni `mflr r12`
                  -> no puede ser entrada de funcion.
  * CON_PROLOGO   empieza con prologo -> el seed probablemente lo declaro bien.
  * INVOCADO      aparece como `sub_XXXX(ctx, base);` en OTRA funcion.
  * LLAMADO_DESDE_FUERA
                  INVOCADO y el llamante no es el `parent` -> roto por
                  definicion (el callee necesita el banco del padre).

Uso:
  python tools/audit_share_registers.py            # resumen
  python tools/audit_share_registers.py --list 40  # lista de candidatos
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GEN = ROOT / "generated" / "neverdead_default"
CONFIG = ROOT / "config" / "functions.toml"

PROLOGUE = ("stwu r1,-", "mflr r12")


def load_share_registers(path: Path) -> dict[int, int]:
    """{chunk_va: parent_va} para las entradas con share_registers = true."""
    out: dict[int, int] = {}
    cur: int | None = None
    parent: int | None = None
    shared = False

    def flush() -> None:
        if cur is not None and shared and parent is not None:
            out[cur] = parent

    for line in path.read_text(errors="replace").splitlines():
        s = line.strip()
        m = re.fullmatch(r'\[functions\."0x([0-9A-Fa-f]{8})"\]', s)
        if m:
            flush()
            cur = int(m.group(1), 16)
            parent = None
            shared = False
            continue
        if s.startswith("["):
            flush()
            cur = None
            continue
        if cur is None:
            continue
        if s.startswith("parent"):
            parent = int(s.split("=")[1].strip(), 0)
        elif s.startswith("share_registers"):
            shared = s.split("=")[1].strip().lower() == "true"
    flush()
    return out


def scan_tree() -> tuple[dict[str, list[str]], dict[str, set[str]]]:
    """Devuelve ({func: [lineas]}, {callee: {callers}})."""
    bodies: dict[str, list[str]] = {}
    callers: dict[str, set[str]] = defaultdict(set)
    func_re = re.compile(r"^DEFINE_REX_FUNC\((\w+)\) \{")
    call_re = re.compile(r"\t(sub_[0-9A-F]{8})\(ctx, base\);")
    for cpp in sorted(GEN.glob("neverdead_recomp.*.cpp")):
        cur: str | None = None
        buf: list[str] = []
        for line in cpp.read_text(errors="replace").splitlines():
            m = func_re.match(line)
            if m:
                if cur:
                    bodies[cur] = buf
                cur = m.group(1)
                buf = []
                continue
            if cur is None:
                continue
            buf.append(line)
            c = call_re.search(line)
            if c and c.group(1) != cur:
                callers[c.group(1)].add(cur)
        if cur:
            bodies[cur] = buf
    return bodies, callers


def first_instr(body: list[str]) -> str:
    for line in body:
        s = line.strip()
        if not s.startswith("//"):
            continue
        t = s[2:].strip()
        if t.startswith(("PATCH", "neverdead")):
            continue
        return t
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", type=int, default=0,
                    help="muestra N candidatos con su evidencia")
    ap.add_argument("--config", type=Path, default=CONFIG)
    ap.add_argument("--gen", type=Path, default=GEN)
    args = ap.parse_args()

    shared = load_share_registers(args.config)
    bodies, callers = scan_tree()

    stats = Counter()
    broken: list[tuple[int, int, str, list[str]]] = []
    for va, parent in sorted(shared.items()):
        name = f"sub_{va:08X}"
        body = bodies.get(name)
        if body is None:
            stats["ausente_en_arbol"] += 1
            continue
        fi = first_instr(body)
        has_pro = fi.startswith(PROLOGUE)
        cs = callers.get(name, set())
        outside = sorted(c for c in cs if c != f"sub_{parent:08X}")
        stats["con_prologo" if has_pro else "sin_prologo"] += 1
        if outside:
            stats["llamado_desde_fuera"] += 1
            if not has_pro:
                stats["ROTO(chunk_sin_prologo_llamado_de_fuera)"] += 1
                broken.append((va, parent, fi, outside[:4]))

    total = sum(1 for _ in shared)
    print(f"[audit] chunks share_registers en config: {total}")
    print(f"[audit] presentes en el arbol: {total - stats['ausente_en_arbol']}"
          f"  (ausentes: {stats['ausente_en_arbol']})")
    print(f"[audit] con prologo propio: {stats['con_prologo']}")
    print(f"[audit] SIN prologo:        {stats['sin_prologo']}")
    print(f"[audit] invocados desde otra funcion: {stats['llamado_desde_fuera']}")
    print(f"[audit] ROTOS (sin prologo + llamados de fuera): "
          f"{stats['ROTO(chunk_sin_prologo_llamado_de_fuera)']}")
    if args.list:
        print()
        print("candidatos (chunk, parent, 1a instruccion, llamantes):")
        for va, parent, fi, cs in broken[: args.list]:
            print(f"  0x{va:08X}  parent=0x{parent:08X}  {fi!r:28} "
                  f"callers={', '.join(cs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
