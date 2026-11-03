#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bd_truth - verdad de campo del invitado para la familia 0x8278BDxx (Capstone-64).

Owner: Worker (M4).  Este modulo es el ORACULO que permite al script
`apply_bd_family_faithful.py` distinguir tres casos:

  1. el codegen emite el cuerpo REAL del invitado  -> no se toca (M4/O2);
  2. el codegen emite un cuerpo TRUNCADO            -> el script escribe su
     transcripcion (que debe coincidir con el invitado);
  3. la transcripcion del script no coincide con el invitado -> ERROR (exit 2).

Nada de esto inventa semantica: la unica fuente es Capstone sobre la imagen
plana del invitado (file_offset = va - 0x82000000).

Convenciones:
  * X-form: el destino es `rA` (bits 11-15).  B-form: (ins>>2)&0xFFFFFF.
  * el bloque recorrido termina en el terminador INCONDICIONAL (b / blr / bctr)
    INCLUIDO, o en un hueco de datos de 4 B, o en el limite del bloque padre.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "tools"))

from capstone import CS_ARCH_PPC, CS_MODE_64, CS_MODE_BIG_ENDIAN, Cs  # noqa: E402

GUEST_BASE = 0x82000000
# La imagen plana del invitado se busca primero junto al proyecto (arbol
# autocontenido) y despues en el layout historico (_ROOT.parent), porque el
# proyecto puede vivir fuera de la carpeta que la contiene en origen.
DEFAULT_BIN = _ROOT / "NeverDead_base.exe"
if not DEFAULT_BIN.is_file():
    DEFAULT_BIN = _ROOT.parent / "NeverDead_base.exe"

_MD: Cs | None = None
_DATA: bytes | None = None


def _load(bin_path: Path | None = None) -> tuple[Cs, bytes]:
    global _MD, _DATA
    if _MD is None or _DATA is None:
        p = Path(bin_path) if bin_path else DEFAULT_BIN
        _MD = Cs(CS_ARCH_PPC, CS_MODE_BIG_ENDIAN | CS_MODE_64)
        _DATA = p.read_bytes()
    return _MD, _DATA


def guest_bytes(va: int, nbytes: int, bin_path: Path | None = None) -> bytes:
    _md, data = _load(bin_path)
    off = va - GUEST_BASE
    return data[off:off + nbytes]


def decode(va: int, bin_path: Path | None = None):
    """(mnem, op_str) de la palabra en `va`; None si no decodifica."""
    md, _data = _load(bin_path)
    raw = guest_bytes(va, 4, bin_path)
    if len(raw) < 4:
        return None
    for ins in md.disasm(raw, va):
        return ins.mnemonic, ins.op_str
    return None


def guest_block(va: int, limit: int = 64, bin_path: Path | None = None) -> list[tuple[str, str]]:
    """Instrucciones del invitado desde `va` hasta el terminador INCLUIDO.

    Terminadores: `b` (incondicional), `blr`, `bctr`.  Un hueco de datos
    (4 bytes a cero) corta el bloque.  Las ramas condicionales NO cortan.
    """
    out: list[tuple[str, str]] = []
    cur = va
    for _ in range(limit):
        d = decode(cur, bin_path)
        if d is None:
            break
        mnem, ops = d
        out.append((mnem, ops))
        if mnem in ("b", "blr", "bctr"):
            break
        if mnem == "b" or (mnem.startswith("b") and mnem not in ("bl", "bne", "beq", "blt", "bgt",
                                                                "ble", "bge", "bns", "bso", "bnu",
                                                                "bsl", "bsr")):
            break
        cur += 4
    return out


# --------------------------------------------------------------------------
# canonicalizacion para comparar el invitado con los comentarios del codegen
# --------------------------------------------------------------------------
_NUM_RE = re.compile(r"(?<![\w.])(\d+)(?![\w.])")


def _norm_num(part: str) -> str:
    """Convierte los decimales sueltos en hex (el codegen escribe 96, Capstone 0x60)."""
    def repl(m: re.Match) -> str:
        v = int(m.group(1))
        return f"0x{v:x}"
    return _NUM_RE.sub(repl, part.replace(" ", ""))


def canon(mnem: str, ops: str) -> tuple[str, str]:
    """Forma canonica de una instruccion (mnemonico, operandos normalizados)."""
    parts = [p.strip() for p in ops.split(",")]
    if mnem in ("or.", "or") and len(parts) == 3 and parts[1] == parts[2]:
        # `or. rX,rY,rY` es un `mr. rX,rY`: el codegen lo etiqueta "mr.".
        return ("mr.", f"{_norm_num(parts[0])},{_norm_num(parts[1])}")
    return (mnem, ",".join(_norm_num(p) for p in parts))


def canon_list(instrs) -> list[tuple[str, str]]:
    return [canon(m, o) for (m, o) in instrs]


# --------------------------------------------------------------------------
# lectura del cuerpo que el codegen escribio en el arbol
# --------------------------------------------------------------------------
_INSTR_COMMENT = re.compile(r"^\s*//\s*([a-z][a-z0-9.]*)\s*(.*?)\s*$")
_SKIP_MARKERS = ("PATCH(", "REX_FUNC_PROLOGUE", "neverdead:", "transcripcion literal")


def codegen_body_instrs(body: str) -> list[tuple[str, str]]:
    """Instrucciones que el codegen dejo en un cuerpo, segun sus comentarios.

    Se ignoran los comentarios de los marcadores del propio script y las
    sondas (BDFAM-P / STKP), que no son instrucciones del invitado.
    """
    out: list[tuple[str, str]] = []
    for line in body.splitlines():
        s = line.strip()
        if not s.startswith("//"):
            continue
        rest = s[2:].strip()
        if any(k in rest for k in _SKIP_MARKERS):
            continue
        if rest.startswith("BDFAM-P") or rest.startswith("STKP"):
            continue
        m = _INSTR_COMMENT.match(s)
        if not m:
            continue
        mnem, ops = m.group(1), m.group(2)
        if mnem in ("mr", "mr.", "li", "lis", "addi", "addic", "subf", "subfic", "subfe",
                    "and", "or", "or.", "xor", "nop", "std", "stw", "stwu", "stdu", "lwz",
                    "lbz", "lhz", "ld", "lwa", "mtlr", "mtctr", "blr", "b", "bctr", "bne",
                    "beq", "blt", "bgt", "ble", "bge", "bl", "cmp", "cmpl", "cmplwi",
                    "cmpw", "cmpwi", "rlwinm", "mflr", "stvx", "lwsx", "stfsx", "lfsx"):
            out.append((mnem, ops))
    return out


def is_faithful(va: int, body: str, bin_path: Path | None = None) -> tuple[bool, str]:
    """El cuerpo `body` (del arbol) contiene las mismas instrucciones que el invitado?

    Devuelve (ok, motivo).
    """
    g = canon_list(guest_block(va, bin_path=bin_path))
    c = canon_list(codegen_body_instrs(body))
    if not g:
        return False, f"el invitado no decodifica en 0x{va:08X}"
    if c == g:
        return True, f"cuerpo fiel al invitado ({len(g)} instr)"
    if len(c) < len(g):
        return False, f"cuerpo truncado: {len(c)} instr < {len(g)} del invitado"
    return False, f"cuerpo divergente: {len(c)} instr vs {len(g)} del invitado"


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("vas", nargs="+")
    ap.add_argument("--bin", type=Path, default=DEFAULT_BIN)
    args = ap.parse_args()
    for v in args.vas:
        va = int(v, 0)
        print(f"0x{va:08X}:")
        for m, o in guest_block(va, bin_path=args.bin):
            print(f"    {m} {o}")
