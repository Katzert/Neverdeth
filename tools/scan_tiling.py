#!/usr/bin/env python3
"""Escáner forense de Predicated Tiling en el codigo recompilado.

Busca en generated/neverdead_default/*.cpp las firmas que delatarian
D3DDevice_BeginTiling / EndTiling y la configuracion de tiles:

  * Cabeceras PM4 type-3 con opcode 0x50/0x51 (SET_BIN_MASK / SET_BIN_SELECT)
    y sus variantes LO/HI 0x60..0x63. Cabecera = 0xC0000000 | (opcode << 8) | (n-1).
  * Escrituras a RB_SURFACE_INFO (indice de registro 0x2000, que lleva
    msaa_samples en los bits [17:16]) y RB_MODECONTROL (0x2208, edram_mode).
  * Llamadas al asignador del primary buffer sub_826B5548, que es el punto por
    el que pasa TODO comando que se escribe en el ring.

Uso:
    python tools/scan_tiling.py            # resumen
    python tools/scan_tiling.py --verbose  # funciones del rango D3D
"""
from __future__ import annotations

import argparse
import collections
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
GEN = ROOT / "generated" / "neverdead_default"

FUNC_RE = re.compile(r"^DEFINE_REX_FUNC\((sub_[0-9A-Fa-f]{8})\)", re.M)

# Cabeceras PM4 literales: 0xC0005000..0xC00050FF etc.
PM4_TILING_RE = re.compile(r"0xC000(50|51|60|61|62|63)[0-9A-Fa-f]{2}")
REG_LITERALS = {0x2000: "RB_SURFACE_INFO", 0x2208: "RB_MODECONTROL"}
RING_ALLOC = "sub_826B5548"


def index_functions():
    """Devuelve [(addr, nombre_archivo, linea_inicio, n_lineas, cuerpo)]."""
    out = []
    for path in sorted(GEN.glob("*.cpp")):
        text = path.read_text(encoding="utf-8", errors="replace")
        matches = list(FUNC_RE.finditer(text))
        for i, m in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            body = text[m.start():end]
            out.append((m.group(1), path.name, text.count("\n", 0, m.start()) + 1,
                        body.count("\n"), body))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    funcs = index_functions()
    print(f"Funciones indexadas en generated/neverdead_default: {len(funcs):,}")
    print()

    # --- 1. Cabeceras PM4 de tiling literales -------------------------------
    tiling_hits = []
    for name, fname, line, nlines, body in funcs:
        for m in PM4_TILING_RE.finditer(body):
            tiling_hits.append((name, fname, line, m.group(0)))
    print("1. Cabeceras PM4 de tiling (0xC00050xx / 0xC00051xx / 0x60-0x63)")
    if tiling_hits:
        for hit in tiling_hits[:20]:
            print(f"   {hit[0]}  {hit[3]}  {hit[1]}:{hit[2]}")
    else:
        print("   0 coincidencias. El driver no emite cabeceras de bin/tiling")
        print("   como constantes literales en el codigo recompilado.")
    print()

    # --- 2. Escrituras a RB_SURFACE_INFO / RB_MODECONTROL -------------------
    reg_hits = collections.defaultdict(list)
    for name, fname, line, nlines, body in funcs:
        for value, label in REG_LITERALS.items():
            # indice de registro como inmediato en rlwinm/andi/ori/cmpwi
            pat = re.compile(r"0x%04X\b" % value)
            n = len(pat.findall(body))
            if n:
                reg_hits[label].append((name, fname, line, n))
    print("2. Literales de registro Xenos (0x2000 RB_SURFACE_INFO / 0x2208 RB_MODECONTROL)")
    for label, hits in reg_hits.items():
        print(f"   {label}: {len(hits)} funciones, {sum(h[3] for h in hits)} literales")
        for h in hits[:4]:
            print(f"      {h[0]}  x{h[3]}  {h[1]}:{h[2]}")
    if not reg_hits:
        print("   0 coincidencias: los indices de registro llegan por tabla en .rdata,")
        print("   no como inmediatos (por eso el escaneo estatico no puede verlos).")
    print()

    # --- 3. Funciones que escriben en el primary buffer ---------------------
    alloc = [(n, f, l, c) for n, f, l, c in
             ((n, f, l, body.count(RING_ALLOC + "(ctx")) for n, f, l, _, body in funcs)
             if c]
    alloc.sort(key=lambda x: -x[3])
    print(f"3. Funciones que llaman al asignador del ring ({RING_ALLOC})")
    print(f"   {len(alloc)} funciones; top 10 por numero de llamadas:")
    for n, f, l, c in alloc[:10]:
        print(f"      {n}  x{c:<3}  {f}:{l}")
    print()

    # --- 4. Rango D3D del XDK ----------------------------------------------
    d3d = [x for x in funcs if re.match(r"sub_826B[0-9A-F]{4}$", x[0])]
    d3d.sort(key=lambda x: -x[3])
    print(f"4. Rango XDK D3D 0x826B0000-0x826BFFFF: {len(d3d)} funciones")
    if args.verbose:
        for n, f, l, c, _ in d3d:
            print(f"      {n}  {c:5d} lineas  {f}:{l}")
    else:
        print("   (--verbose para listarlas todas)")
        for n, f, l, c, _ in d3d[:6]:
            print(f"      {n}  {c:5d} lineas  {f}:{l}")
    print()

    # --- 5. Veredicto -------------------------------------------------------
    print("5. VEREDICTO")
    if not tiling_hits:
        print("   No hay ninguna cabecera PM4 de tiling literal. El codegen no")
        print("   permite decidir por si solo si el juego usa BeginTiling; la")
        print("   respuesta fiable es la sonda del ring en runtime (ND_RING=1):")
        print("   bin_mask=0 y bin_select=0 en todas las ventanas medidas =>")
        print("   el juego NO emite paquetes de predicated tiling.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
