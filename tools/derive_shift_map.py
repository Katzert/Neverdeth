#!/usr/bin/env python3
"""
derive_shift_map - deriva el mapa de desplazamientos entre NeverDead_base.exe y la
imagen guest, usando .pdata como unica autoridad.

POR QUE EXISTE
--------------
`NeverDead_base.exe` NO es un volcado fiel de la imagen guest. Su contenido esta
desplazado respecto a ella de forma DEPENDIENTE DE REGION. Medido con anclas de
contenido (volcados `dump` del runtime comparados contra el .exe):

    .data   rva 0x7DBFA8 / 0x7EA280 / 0x7EA2B0  -> shift 0x0000   (3/3 anclas)
    .rdata  rva 0x04AFC0 / 0x04D350 / 0x04D360  -> shift 0x0000   (3/3 anclas)
    .rdata  rva 0x144930                         -> shift 0x1000   (5/5 palabras)
    .rdata  rva 0x49EF98 / 0x49EFA8              -> shift 0x2000   (2/2 anclas)

Dentro de la MISMA seccion .rdata el desplazamiento toma tres valores distintos.
No existe offset fijo por seccion que sea correcto.

METODO
------
`.pdata` es la tabla de funciones: da, para cada funcion, su direccion guest de
entrada (`BeginAddress`) y su extension. Es la unica fuente independiente del
arbol generado.

Para cada entrada `B` de `.pdata` se busca el desplazamiento `s` de entre
{0, 0x1000, 0x2000} que hace que los 4 bytes en offset `B - 0x82000000 - s` del
.exe sean un prologo valido (`mflr r0` = 0x7D8802A6 o `mflr r12` = 0x7D8803A6).
Ese `s` es el desplazamiento de la region.

Con el mapa se puede:
  * traducir una direccion plana del arbol a su direccion guest real;
  * detectar destinos de `bl`/`b` mal etiquetados (el objetivo plano apunta a un
    interior de funcion que no es el que el codigo guest realmente alcanza).

SALIDA
------
  * `--stats`      distribucion de desplazamientos y cobertura.
  * `--resolve A`  resuelve una direccion plana: a que funcion .pdata pertenece,
                   su entrada guest real y su desplazamiento.
  * `--branch SITE DEST`
                   dado un sitio de rama plano y su destino plano decodificado,
                   calcula el destino guest REAL teniendo en cuenta que el
                   desplazamiento de rama es invariante.
  * `--dump-map F` escribe el mapa {entrada_pdata: shift} en JSON.

Uso:
  python tools/derive_shift_map.py --stats
  python tools/derive_shift_map.py --resolve 0x8278BD4C
  python tools/derive_shift_map.py --branch 0x822CB914 0x821E1098
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXE = ROOT / "NeverDead_base.exe"

GUEST_BASE = 0x82000000
PDATA_RVA = 0x16F800
PDATA_SIZE = 0x238D0
CODE_LO, CODE_HI = 0x821B2200, 0x827C05E4

SHIFTS = (0x0000, 0x1000, 0x2000)
MFLR = (0x7D8802A6, 0x7D8803A6)


def is_prologue(data: bytes, off: int) -> bool:
    """True si en `off` empieza una funcion.

    Criterios, en orden de fiabilidad:
      1. `mflr r0/r12`            (0x7D8802A6 / 0x7D8803A6) - prologo normal.
      2. `stwu r1,-N(r1)`         opcode 37, rA == 1          - funcion sin LR.
      3. `std rN,-off(r1)`        opcode 62, rA == 1          - helper __savegprlr_*.
      4. `stfd fN,-off(r1)`       opcode 54, rA == 1          - helper __savefpr_*.

    El criterio 3/4 es imprescindible: los helpers del CRT NO llevan `mflr`
    (empiezan directamente guardando registros), asi que un mapa que solo acepte
    `mflr` los marca todos como "sin determinar".  `__savegprlr_14` en
    0x8278CD10 es exactamente ese caso.
    """
    if off < 0 or off + 4 > len(data):
        return False
    w = struct.unpack(">I", data[off : off + 4])[0]
    if w in MFLR:
        return True
    if (w >> 26) == 37 and ((w >> 16) & 0x1F) == 1:  # stwu r1,-N(r1)
        return True
    if (w >> 26) == 62 and ((w >> 16) & 0x1F) == 1:  # std rN,-off(r1)
        return True
    if (w >> 26) == 54 and ((w >> 16) & 0x1F) == 1:  # stfd fN,-off(r1)
        return True
    return False


def load_pdata(data: bytes) -> list[int]:
    raw = data[PDATA_RVA : PDATA_RVA + PDATA_SIZE]
    begins = sorted(
        {struct.unpack(">I", raw[i : i + 4])[0] for i in range(0, len(raw), 8)} - {0}
    )
    return [b for b in begins if CODE_LO <= b < CODE_HI]


def word(data: bytes, off: int) -> int | None:
    if off < 0 or off + 4 > len(data):
        return None
    return struct.unpack(">I", data[off : off + 4])[0]


def derive(data: bytes, begins: list[int]) -> tuple[dict[int, int], list[int]]:
    """{entrada_pdata: shift} y lista de entradas sin determinar."""
    shift_of: dict[int, int] = {}
    ambiguous: list[int] = []
    for b in begins:
        cands = [s for s in SHIFTS if is_prologue(data, b - GUEST_BASE - s)]
        if len(cands) == 1:
            shift_of[b] = cands[0]
        elif cands:
            ambiguous.append(b)
    return shift_of, ambiguous


def flat_label_map(shift_of: dict[int, int]) -> dict[int, tuple[int, int]]:
    """{etiqueta_plana: (entrada_pdata, shift)} - donde el codegen con mapa plano
    habria puesto cada funcion."""
    return {b - s: (b, s) for b, s in shift_of.items()}


def owner_of(flat: dict[int, tuple[int, int]], addr: int) -> tuple[int, int, int] | None:
    """(etiqueta_plana, entrada_pdata, shift) de la funcion que contiene addr."""
    best = None
    for fl, (b, s) in flat.items():
        if fl <= addr and (best is None or fl > best[0]):
            best = (fl, b, s)
    return best


def next_flat(flat: dict[int, tuple[int, int]], addr: int) -> int | None:
    nxt = [f for f in flat if f > addr]
    return min(nxt) if nxt else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", type=Path, default=EXE)
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--resolve", type=lambda x: int(x, 0), default=None,
                    metavar="FLAT_ADDR")
    ap.add_argument("--branch", nargs=2, type=lambda x: int(x, 0),
                    metavar=("SITE", "DEST"))
    ap.add_argument("--dump-map", type=Path, default=None)
    args = ap.parse_args()

    if not args.exe.is_file():
        print(f"[shift-map] no existe {args.exe}", file=sys.stderr)
        return 1
    data = args.exe.read_bytes()
    begins = load_pdata(data)
    shift_of, ambiguous = derive(data, begins)
    flat = flat_label_map(shift_of)

    if args.dump_map:
        args.dump_map.write_text(json.dumps(
            {f"0x{b:08X}": s for b, s in sorted(shift_of.items())}, indent=1))
        print(f"[shift-map] mapa escrito en {args.dump_map} ({len(shift_of)} entradas)")

    if args.stats:
        print(f"[shift-map] entradas .pdata validas : {len(begins)}")
        print(f"[shift-map] desplazamiento determinado: {len(shift_of)}")
        print(f"[shift-map] ambiguas (varios shifts validos): {len(ambiguous)}")
        print(f"[shift-map] sin determinar: {len(begins) - len(shift_of) - len(ambiguous)}")
        print(f"[shift-map] distribucion: "
              f"{ {hex(k): v for k, v in sorted(Counter(shift_of.values()).items())} }")
        zonas = Counter((b & ~0xFFFFF, s) for b, s in shift_of.items())
        print("[shift-map] por bloque de 1 MB:")
        cur = None
        for b in sorted(shift_of):
            z = b & ~0xFFFFF
            if z != cur:
                c = Counter(v for k, v in shift_of.items() if (k & ~0xFFFFF) == z)
                print(f"    0x{z:08X}: { {hex(k): v for k, v in sorted(c.items())} }")
                cur = z

    if args.resolve is not None:
        a = args.resolve
        hit = flat.get(a)
        if hit:
            b, s = hit
            print(f"[shift-map] flat 0x{a:08X} ES la entrada de funcion: "
                  f"guest 0x{b:08X}, shift 0x{s:X}")
        else:
            own = owner_of(flat, a)
            if own is None:
                print(f"[shift-map] flat 0x{a:08X}: sin funcion contenedora")
            else:
                fl, b, s = own
                nxt = next_flat(flat, a)
                print(f"[shift-map] flat 0x{a:08X} NO es entrada de funcion.")
                print(f"           cae dentro de la funcion plana 0x{fl:08X} "
                      f"(entrada .pdata guest 0x{b:08X}, shift 0x{s:X}), "
                      f"offset 0x{a - fl:X}")
                if nxt is not None:
                    print(f"           siguiente entrada plana: 0x{nxt:08X} "
                          f"(extension 0x{nxt - fl:X})")

    if args.branch is not None:
        site, dest = args.branch
        own = owner_of(flat, site)
        if own is None:
            print("[shift-map] sitio sin funcion contenedora")
            return 1
        fl, b, s = own
        disp = dest - site
        real = site + s + disp
        print(f"[shift-map] sitio plano 0x{site:08X}")
        print(f"           funcion plana 0x{fl:08X} -> entrada .pdata guest 0x{b:08X}, "
              f"shift 0x{s:X}")
        print(f"           sitio guest real      : 0x{site + s:08X}")
        print(f"           destino plano decod.  : 0x{dest:08X}  (disp {disp:+#x})")
        print(f"           destino GUEST REAL    : 0x{real:08X}")
        own_d = owner_of(flat, real)
        if own_d is not None:
            fld, bd, sd = own_d
            nxt = next_flat(flat, real)
            print(f"           ese destino cae dentro de la funcion plana 0x{fld:08X} "
                  f"(entrada .pdata guest 0x{bd:08X}, shift 0x{sd:X})")
            if nxt is not None:
                print(f"           siguiente entrada plana: 0x{nxt:08X} "
                      f"(extension 0x{nxt - fld:X})")
        if dest != real:
            print(f"           >>> DESAJUSTE: el arbol usa 0x{dest:08X} pero el "
                  f"codigo guest alcanza 0x{real:08X} (delta {real - dest:+#x})")
        else:
            print("           >>> el destino plano coincide con el real.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
