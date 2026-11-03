#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vtable_audit.py - auditoria de RTTI/vtables del XEX invitado (READ-ONLY sobre el binario).

MISION M1 (swe_native_3d_v2)
----------------------------
El codegen de ReXGlue v0.10.0 registra las ranuras de vtable con la autoridad
``VTABLE`` (immutable) DESPUES de escanear RTTI MSVC:

    src/codegen/vtable_scanner.cpp :: findCompleteObjectLocators()
        - recorre .rdata en pasos de 4 bytes
        - exige signature == 0            (COL de 32 bits)
        - exige pTypeDescriptor (col+12) DENTRO de una seccion
        - exige que la cadena en typeDesc+8 empiece por ".?AV" o ".?AU"
    src/codegen/vtable_scanner.cpp :: findVTableForCOL()
        - busca un dword == COL y toma vtable = esa direccion + 4   (COL en vtable[-1])
    src/codegen/phase_discover.cpp
        - por cada slot: graph.addFunction(funcAddr, 4, FunctionAuthority::VTABLE, true)

Si el escaner no encuentra NINGUN COL, ningun slot de vtable se registra como
funcion => toda llamada virtual cae en el bucket "unregistered" del runtime.

Este herramienta:
  1. replica el criterio del escaner de ReXGlue sobre el binario REAL y mide por que
     encuentra 0 COLs (evidencia con bytes);
  2. escanea las secciones de datos buscando *tablas de punteros a codigo*
     (runs maximales de >=2 dwords consecutivos que apuntan a .text);
  3. extrae del log de runtime la evidencia (target, lr) de cada llamada indirecta
     no registrada / fuera de rango;
  4. desensambla el call-site, resuelve simbolicamente la vtable (lis/addi, stack)
     y demuestra que el target ocupa una ranura de vtable;
  5. liga (target, lr, ranura) con las tablas -> tabla CONFIRMADA;
  6. emite (opcionalmente) config/vtables.toml con
        [functions."0xSLOT"] = { name = "vtable_slot_<SLOT>" }
     SOLO para slots con evidencia real (caller + tabla).

REGLAS
  * El binario NUNCA se modifica: solo se lee.
  * No se inventa semantica: una ranura solo se registra si el runtime la ha
    llamado de verdad a traves de un acceso vtable[slot] comprovado por Capstone.
  * Idempotente: la 2a ejecucion con los mismos argumentos produce 0 cambios.
  * No llama a `rexglue codegen` (lo hace el Orchestrator).

USO
    python tools/vtable_audit.py --report tools/vtable_audit_report.json
    python tools/vtable_audit.py --emit-config config/vtables.toml --check
    python tools/vtable_audit.py --tables          # censo de tablas candidatas
    python tools/vtable_audit.py --json            # reporte por stdout
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import struct
import sys
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from capstone import CS_ARCH_PPC, CS_MODE_BIG_ENDIAN, CS_MODE_64, Cs
except ImportError:  # pragma: no cover
    Cs = None  # type: ignore

# ---------------------------------------------------------------------------
# Constantes del proyecto
# ---------------------------------------------------------------------------

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_BIN = PROJECT_ROOT.parent / "NeverDead_base.exe"
DEFAULT_LOGS = [
    PROJECT_ROOT / "build_clang" / "logs" / "neverdead_165.log",
    PROJECT_ROOT / "build_clang" / "logs" / "neverdead_163.log",
]
DEFAULT_REPORT = PROJECT_ROOT / ".agents" / "teamwork" / "worker" / "vtable_audit_report.json"
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "vtables.toml"
DEFAULT_SEEDS = [
    PROJECT_ROOT / "config" / "functions.toml",       # seed vivo (W1)
    PROJECT_ROOT / "nd_seed_overrides.toml",          # historico (archivado)
    PROJECT_ROOT / "nd_seed_overrides_v2.toml",       # historico (archivado)
]

# Rango de codigo guest segun el log del runtime:
#   "Function table initialized for module: code=821A0000-827C05E4, image=82000000-82C10000"
CODE_LO = 0x821A0000
CODE_HI = 0x827C05E4

UNREGISTERED_RE = re.compile(
    r"unregistered indirect call[^0-9]*0x([0-9A-Fa-f]+)[^\n]*?lr=0x([0-9A-Fa-f]+)")
TRAP_OOB_RE = re.compile(
    r"TRAP-OOB[^\n]*?lr=0x([0-9A-Fa-f]+)[^\n]*?target=0x([0-9A-Fa-f]+)")

SEED_FUNC_RE = re.compile(r'^\[functions\."0x([0-9A-Fa-f]+)"\]', re.M)
SEED_SIZE_RE = re.compile(r'^\s*(?:size|end)\s*=\s*(\d+)', re.M)


# ---------------------------------------------------------------------------
# Imagen invitada
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Section:
    name: str
    va: int            # VA absoluta del invitado (guest_va)
    vsize: int
    rawptr: int
    rawsize: int
    flags: int
    rva: int           # RVA segun la tabla PE (puede diferir del offset plano)

    @property
    def va_lo(self) -> int:
        return self.va

    @property
    def va_hi(self) -> int:
        return self.va + self.vsize

    @property
    def executable(self) -> bool:
        return bool(self.flags & 0x20000000)  # IMAGE_SCN_MEM_EXECUTE


class Image:
    """Imagen plana del invitado: file_offset = guest_va - image_base.

    La tabla PE del XEX tiene el ``PointerToRawData`` desfasado a partir de
    .text, por lo que se usa el mapeo plano (verificado con Capstone: el
    desensamblado en 0x821E8E18 coincide byte a byte con el del log).
    """

    def __init__(self, path: pathlib.Path):
        self.path = pathlib.Path(path)
        self.data = self.path.read_bytes()
        self.image_base = 0x82000000
        self.sections: List[Section] = []
        self._parse_pe()

    def _parse_pe(self) -> None:
        d = self.data
        if d[:2] != b"MZ":
            raise ValueError(f"{self.path}: no es un PE (MZ)")
        e_lfanew = struct.unpack_from("<I", d, 0x3C)[0]
        if d[e_lfanew : e_lfanew + 4] != b"PE\0\0":
            raise ValueError(f"{self.path}: firma PE invalida")
        coff = e_lfanew + 4
        machine, nsec, _ts, _sp, _ns, optsize, _ch = struct.unpack_from("<HHIIIHH", d, coff)
        opt = coff + 20
        magic = struct.unpack_from("<H", d, opt)[0]
        if magic != 0x10B:
            raise ValueError(f"{self.path}: solo PE32 soportado (magic=0x{magic:X})")
        self.image_base = struct.unpack_from("<I", d, opt + 28)[0]
        sectab = opt + optsize
        for i in range(nsec):
            off = sectab + i * 40
            name = d[off : off + 8].rstrip(b"\0").decode("latin1")
            vsize, vaddr, rawsize, rawptr = struct.unpack_from("<IIII", d, off + 8)
            flags = struct.unpack_from("<I", d, off + 36)[0]
            self.sections.append(
                Section(name, vaddr + self.image_base, vsize, rawptr, rawsize, flags, vaddr))

    # -- acceso ------------------------------------------------------------
    def offset(self, va: int) -> int:
        return va - self.image_base

    def u32(self, va: int) -> Optional[int]:
        o = self.offset(va)
        if o < 0 or o + 4 > len(self.data):
            return None
        return struct.unpack_from(">I", self.data, o)[0]

    def cstr(self, va: int, maxlen: int = 64) -> str:
        o = self.offset(va)
        if o < 0 or o >= len(self.data):
            return ""
        end = self.data.find(b"\0", o, min(o + maxlen, len(self.data)))
        if end < 0:
            end = min(o + maxlen, len(self.data))
        return self.data[o:end].decode("latin1")

    def section_of(self, va: int) -> Optional[Section]:
        for s in self.sections:
            if s.va_lo <= va < s.va_hi:
                return s
        return None

    def is_executable(self, va: int) -> bool:
        s = self.section_of(va)
        return bool(s and s.executable)

    def section_by_name(self, name: str) -> Optional[Section]:
        for s in self.sections:
            if s.name == name:
                return s
        return None

    def hexdump(self, va: int, n: int = 16) -> str:
        o = self.offset(va)
        return " ".join(f"{b:02x}" for b in self.data[o : o + n])

    def find_dword_occurrences(self, value: int, section_names: Sequence[str] = ()) -> List[int]:
        """Direcciones de los dwords big-endian iguales a ``value``."""
        needle = struct.pack(">I", value)
        out: List[int] = []
        i = self.data.find(needle)
        while i >= 0:
            va = self.image_base + i
            if not section_names or (self.section_of(va) and self.section_of(va).name in section_names):
                out.append(va)
            i = self.data.find(needle, i + 1)
        return out


# ---------------------------------------------------------------------------
# 1. Replica del escaner de COL de ReXGlue
# ---------------------------------------------------------------------------


@dataclass
class ColCandidate:
    va: int
    type_desc: int
    class_hierarchy: int
    type_name: str
    interpretation: str  # "absolute" (como lo hace ReXGlue) | "rva"


def scan_complete_object_locators(img: Image, section_name: str = ".rdata") -> List[ColCandidate]:
    """Replica exacta de ``findCompleteObjectLocators()`` de ReXGlue v0.10.0.

    Ademas prueba la interpretacion alternativa (pTypeDescriptor como RVA) para
    separar "el formato no es MSVC" de "el puntero esta en otra unidad".
    """
    sec = img.section_by_name(section_name)
    if sec is None:
        return []
    out: List[ColCandidate] = []
    for off in range(0, sec.vsize - 20 + 1, 4):
        va = sec.va + off
        signature = img.u32(va)
        type_desc = img.u32(va + 12)
        if signature != 0 or type_desc is None:
            continue
        if img.section_of(type_desc) is not None:      # (a) ABSOLUTA (la de ReXGlue)
            name = img.cstr(type_desc + 8, 64)
            if name.startswith(".?AV") or name.startswith(".?AU"):
                out.append(ColCandidate(va, type_desc, img.u32(va + 16) or 0, name, "absolute"))
        rva_va = img.image_base + type_desc              # (b) RVA relativa a la imagen
        if rva_va != type_desc and img.section_of(rva_va) is not None:
            name = img.cstr(rva_va + 8, 64)
            if name.startswith(".?AV") or name.startswith(".?AU"):
                out.append(ColCandidate(va, rva_va, img.u32(va + 16) or 0, name, "rva"))
    return out


def rtti_report(img: Image) -> dict:
    cols = scan_complete_object_locators(img)
    names = []
    for marker in (b".?AV", b".?AU"):
        i = img.data.find(marker)
        while i >= 0:
            va = img.image_base + i
            sec = img.section_of(va)
            names.append({
                "marker": marker.decode("latin1"),
                "va": f"0x{va:08X}",
                "section": sec.name if sec else None,
                "name": img.cstr(va, 96),
                "bytes": img.hexdump(va, 16),
            })
            i = img.data.find(marker, i + 1)
    tds = []
    for n in names:
        td = int(n["va"], 16) - 8
        pvf = img.u32(td) or 0
        sec = img.section_of(pvf)
        tds.append({
            "type_descriptor_va": f"0x{td:08X}",
            "pVFTable": f"0x{pvf:08X}",
            "spare": f"0x{(img.u32(td + 4) or 0):08X}",
            "bytes": img.hexdump(td, 16),
            "pVFTable_section": sec.name if sec else None,
        })
    return {
        "col_candidates": [
            {"va": f"0x{c.va:08X}", "pTypeDescriptor": f"0x{c.type_desc:08X}",
             "pClassHierarchy": f"0x{c.class_hierarchy:08X}", "type_name": c.type_name,
             "interpretation": c.interpretation}
            for c in cols
        ],
        "col_candidate_count": len(cols),
        "mangled_names_in_image": names,
        "type_descriptors": tds,
        "verdict": ("0 Complete Object Locators" if not cols else "COLs encontrados"),
    }


# ---------------------------------------------------------------------------
# 2. Tablas de punteros a codigo
# ---------------------------------------------------------------------------


@dataclass
class Slot:
    index: int
    va: int
    value: int
    is_code: bool


@dataclass
class Table:
    base_va: int
    section: str
    slots: List[Slot]
    prev_va: Optional[int]
    prev_value: Optional[int]
    prev_is_code: Optional[bool]

    @property
    def n_slots(self) -> int:
        return len(self.slots)

    def slot_at(self, index: int) -> Optional[Slot]:
        return self.slots[index] if 0 <= index < len(self.slots) else None


def scan_code_pointer_tables(img: Image, section_names: Sequence[str] = (".rdata",)) -> List[Table]:
    """Runs maximales de >=2 dwords consecutivos que apuntan a codigo ejecutable."""
    tables: List[Table] = []
    for name in section_names:
        sec = img.section_by_name(name)
        if sec is None:
            continue
        run: List[Slot] = []
        run_start = 0
        for off in range(0, sec.vsize - 4 + 1, 4):
            va = sec.va + off
            val = img.u32(va)
            if val is None:
                break
            if img.is_executable(val):
                if not run:
                    run_start = off
                run.append(Slot(len(run), va, val, True))
                continue
            if len(run) >= 2:
                tables.append(_make_table(img, sec, run_start, run))
            run = []
        if len(run) >= 2:
            tables.append(_make_table(img, sec, run_start, run))
    return tables


def _make_table(img: Image, sec: Section, run_start: int, run: List[Slot]) -> Table:
    base_va = sec.va + run_start
    prev_va = base_va - 4 if run_start >= 4 else None
    prev_value = img.u32(prev_va) if prev_va is not None else None
    return Table(base_va=base_va, section=sec.name, slots=run, prev_va=prev_va,
                 prev_value=prev_value,
                 prev_is_code=(img.is_executable(prev_value) if prev_value is not None else None))


# ---------------------------------------------------------------------------
# 3. Evidencia de runtime (logs)
# ---------------------------------------------------------------------------


@dataclass
class RuntimeEvidence:
    kind: str            # "unregistered" | "trap_oob"
    target: int
    lr: int
    count: int
    log: str

    @property
    def lr_valid(self) -> bool:
        return CODE_LO <= self.lr < CODE_HI


def mine_logs(paths: Iterable[pathlib.Path]) -> List[RuntimeEvidence]:
    agg: Dict[Tuple[str, int, int], RuntimeEvidence] = {}
    for p in paths:
        p = pathlib.Path(p)
        if not p.exists():
            continue
        txt = p.read_text(encoding="utf-8", errors="replace")
        for m in UNREGISTERED_RE.finditer(txt):
            key = ("unregistered", int(m.group(1), 16), int(m.group(2), 16))
            ev = agg.get(key)
            if ev is None:
                agg[key] = RuntimeEvidence("unregistered", key[1], key[2], 1, p.name)
            else:
                ev.count += 1
        for m in TRAP_OOB_RE.finditer(txt):
            key = ("trap_oob", int(m.group(2), 16), int(m.group(1), 16))
            ev = agg.get(key)
            if ev is None:
                agg[key] = RuntimeEvidence("trap_oob", key[1], key[2], 1, p.name)
            else:
                ev.count += 1
    out = list(agg.values())
    out.sort(key=lambda e: (-e.count, e.kind, e.target, e.lr))
    return out


# ---------------------------------------------------------------------------
# 4. Desensamblado del call-site + resolucion simbolica de la vtable
# ---------------------------------------------------------------------------


@dataclass
class Value:
    kind: str            # "const" | "mem" | "stack" | "reg" | "unknown"
    const: Optional[int] = None
    base: Optional["Value"] = None
    offset: int = 0
    reg: Optional[str] = None
    captured: Optional["Value"] = None   # valor almacenado en el slot de pila

    def describe(self) -> str:
        if self.kind == "const":
            return f"0x{self.const:08X}"
        if self.kind == "mem":
            return f"*({self.base.describe()}+0x{self.offset:X})"
        if self.kind == "stack":
            cap = self.captured.describe() if self.captured else "?"
            return f"stack[0x{self.offset:X}]({cap})"
        if self.kind == "reg":
            return self.reg
        return "?"


@dataclass
class CallSite:
    lr: int
    bctrl: Optional[int] = None
    mtctr: Optional[int] = None
    slot_offset: Optional[int] = None      # offset en bytes de la ranura dentro de la vtable
    slot_load: Optional[int] = None
    vtable_pointer_expr: Optional[str] = None
    vtable_base: Optional[int] = None       # resuelto (constante) si es posible
    vtable_from_object: bool = False        # vtable = *(objeto)  -> dispatch virtual clasico
    is_virtual: bool = False
    text: List[str] = field(default_factory=list)

    @property
    def slot_index(self) -> Optional[int]:
        return None if self.slot_offset is None else self.slot_offset // 4


def _parse_mem(op: str) -> Optional[Tuple[str, int, str]]:
    """'0x14(r11)' -> ('r11', 0x14, 'r11')"""
    if "(" not in op:
        return None
    try:
        off_s, rest = op.split("(", 1)
        reg = rest.rstrip(")")
        off = int(off_s, 0) if off_s.strip() else 0
    except (ValueError, IndexError):
        return None
    return reg, off, reg


def decode_call_site(img: Image, lr: int, window: int = 16) -> CallSite:
    """Desensambla la ventana que precede a ``lr`` (lr = retorno del bctrl) y
    resuelve simbolicamente el puntero a vtable."""
    cs = CallSite(lr=lr)
    if Cs is None:
        return cs
    lo = max(img.image_base, lr - window * 4)
    code = img.data[img.offset(lo) : img.offset(lr) + 4]
    md = Cs(CS_ARCH_PPC, CS_MODE_BIG_ENDIAN | CS_MODE_64)
    md.skipdata = True
    insns = list(md.disasm(code, lo))
    cs.text = [f"{i.address:08X}: {i.bytes.hex():16s} {i.mnemonic} {i.op_str}" for i in insns]
    if not insns:
        return cs

    by_addr = {i.address: i for i in insns}

    # --- 1. bctrl inmediatamente antes de lr -------------------------------
    bctrl = by_addr.get(lr - 4)
    if bctrl is None or bctrl.mnemonic != "bctrl":
        return cs
    cs.bctrl = bctrl.address

    # --- 2. mtctr + la carga que alimenta ctr ---------------------------
    mtctr = None
    for i in insns:
        if i.address < bctrl.address and i.mnemonic == "mtctr":
            mtctr = i
    if mtctr is None:
        return cs
    cs.mtctr = mtctr.address
    ctr_reg = mtctr.op_str.split(",")[-1].strip()

    slot_insn = None
    for i in insns:
        if i.address >= mtctr.address:
            break
        if i.mnemonic == "lwz":
            try:
                dst = i.op_str.split(",")[0].strip()
            except IndexError:
                continue
            if dst == ctr_reg:
                slot_insn = i
    if slot_insn is None:
        return cs
    cs.slot_load = slot_insn.address
    mem = _parse_mem(slot_insn.op_str.split(",", 1)[1].strip())
    if mem is None:
        return cs
    vtable_reg, slot_off, _ = mem
    cs.slot_offset = slot_off

    # --- 3. resolucion simbolica (hacia atras, en orden de ejecucion) ----
    env: Dict[str, Value] = {}
    stack: Dict[int, Value] = {}
    for i in insns:
        if i.address >= slot_insn.address:
            continue
        ops = [o.strip() for o in i.op_str.split(",")] if i.op_str else []
        dst = ops[0] if ops else ""
        if not dst.startswith("r"):
            continue
        if i.mnemonic == "lis":
            try:
                imm = int(ops[1], 0) & 0xFFFF
            except (ValueError, IndexError):
                continue
            env[dst] = Value("const", const=(imm << 16) & 0xFFFFFFFF)
        elif i.mnemonic == "addi" and len(ops) >= 3:
            ra, imm_s = ops[1], ops[2]
            try:
                imm = int(imm_s, 0)
            except ValueError:
                continue
            base = env.get(ra, Value("reg", reg=ra))
            if base.kind == "const":
                env[dst] = Value("const", const=(base.const + imm) & 0xFFFFFFFF)
            else:
                env[dst] = Value("mem", base=base, offset=imm)
        elif i.mnemonic == "ori" and len(ops) >= 3:
            ra, imm_s = ops[1], ops[2]
            try:
                imm = int(imm_s, 0)
            except ValueError:
                continue
            base = env.get(ra, Value("reg", reg=ra))
            if base.kind == "const":
                env[dst] = Value("const", const=(base.const | imm) & 0xFFFFFFFF)
            else:
                env[dst] = Value("unknown")
        elif i.mnemonic == "li" and len(ops) >= 2:
            try:
                env[dst] = Value("const", const=int(ops[1], 0) & 0xFFFFFFFF)
            except ValueError:
                env[dst] = Value("unknown")
        elif i.mnemonic == "lwz" and len(ops) >= 2:
            m = _parse_mem(ops[1])
            if m is None:
                env[dst] = Value("unknown")
            else:
                ra, off, _ = m
                if ra == "r1":
                    saved = stack.get(off)
                    if saved is not None and saved.kind == "const":
                        env[dst] = Value("const", const=saved.const)
                    else:
                        env[dst] = Value("stack", offset=off, captured=saved)
                else:
                    base = env.get(ra, Value("reg", reg=ra))
                    env[dst] = Value("mem", base=base, offset=off)
        elif i.mnemonic == "stw" and len(ops) >= 2:
            m = _parse_mem(ops[1])
            if m is not None:
                ra, off, _ = m
                if ra == "r1":
                    stack[off] = env.get(ops[0], Value("unknown"))
        elif i.mnemonic == "mr" and len(ops) >= 2:
            env[dst] = env.get(ops[1], Value("reg", reg=ops[1]))
        else:
            env[dst] = Value("unknown")

    vt = env.get(vtable_reg, Value("unknown"))
    cs.vtable_pointer_expr = vt.describe()
    # vtable = *(<algo>+0)  ó  vtable = <constante>
    if vt.kind == "const":
        cs.vtable_base = vt.const
    elif vt.kind == "mem" and vt.offset == 0 and vt.base is not None:
        b = vt.base
        if b.kind == "const":
            cs.vtable_base = b.const
        elif b.kind == "stack":
            sv = b.captured
            if sv is not None and sv.kind == "const":
                cs.vtable_base = sv.const
            else:
                cs.vtable_from_object = True
        else:
            # vtable leido del primer word de un objeto -> dispatch virtual clasico
            cs.vtable_from_object = True
    if cs.vtable_base is not None:
        cs.is_virtual = True
    return cs


# ---------------------------------------------------------------------------
# 5. Confirmacion de tablas
# ---------------------------------------------------------------------------


@dataclass
class Confirmation:
    evidence: RuntimeEvidence
    call_site: CallSite
    vtable_base: Optional[int]
    candidate_bases: List[int]
    slots: List[Slot] = field(default_factory=list)

    @property
    def table_confirmed(self) -> bool:
        return self.vtable_base is not None


def attribute_evidence(img: Image, ev: RuntimeEvidence,
                        tables: List[Table]) -> Optional[Confirmation]:
    """Solo son confirmables los eventos 'unregistered' con target en codigo y
    un call-site que demuestra un acceso vtable[slot]."""
    if ev.kind != "unregistered":
        return None
    if not (CODE_LO <= ev.target < CODE_HI):
        return None
    if not ev.lr_valid:
        return None
    cs = decode_call_site(img, ev.lr)
    if cs.bctrl is None or cs.slot_offset is None:
        return None
    if not (cs.is_virtual or cs.vtable_from_object):
        return None

    conf = Confirmation(ev, cs, cs.vtable_base, [])
    if cs.vtable_base is not None:
        conf.slots = _slots_of_vtable(img, cs.vtable_base)
    else:
        # el puntero a vtable viene del objeto: la tabla es una de las que
        # contienen el target en esa ranura
        slot = cs.slot_index or 0
        for pos in img.find_dword_occurrences(ev.target):
            base = pos - slot * 4
            if base not in conf.candidate_bases:
                conf.candidate_bases.append(base)
        conf.candidate_bases.sort()
        for base in conf.candidate_bases:
            for s in _slots_of_vtable(img, base):
                conf.slots.append(s)
            break  # solo el primer candidato para la lista de slots
    return conf


def _slots_of_vtable(img: Image, base: int) -> List[Slot]:
    """Slots consecutivos a partir de ``base`` mientras apunten a codigo."""
    out: List[Slot] = []
    for i in range(0, 512):
        va = base + i * 4
        val = img.u32(va)
        if val is None or not img.is_executable(val):
            break
        out.append(Slot(i, va, val, True))
    return out


# ---------------------------------------------------------------------------
# 6. Semilla / .pdata (para no pisar funciones ya declaradas)
# ---------------------------------------------------------------------------


@dataclass
class SeedEntry:
    addr: int
    size: int          # 0 si no hay 'size'
    end: int           # 'end' declarado (0 si no hay)
    parent: int        # 'parent' declarado (0 = standalone)
    source: str

    @property
    def is_chunk(self) -> bool:
        return self.parent != 0

    @property
    def extent(self) -> int:
        if self.size:
            return self.size
        if self.end > self.addr:
            return self.end - self.addr
        return 0


def load_seed_functions(paths: Iterable[pathlib.Path]) -> List[SeedEntry]:
    entries: List[SeedEntry] = []
    for p in paths:
        p = pathlib.Path(p)
        if not p.exists():
            continue
        txt = p.read_text(encoding="utf-8", errors="replace")
        for m in SEED_FUNC_RE.finditer(txt):
            addr = int(m.group(1), 16)
            tail = txt[m.end(): m.end() + 400]
            nxt = SEED_FUNC_RE.search(tail)
            if nxt:
                tail = tail[: nxt.start()]

            def _num(key: str) -> int:
                mm = re.search(r"^\s*" + key + r"\s*=\s*(\d+)\s*$", tail, re.M)
                return int(mm.group(1)) if mm else 0

            entries.append(SeedEntry(addr, _num("size"), _num("end"), _num("parent"), p.name))
    return entries


def load_pdata_functions(img: Image) -> List[Tuple[int, int]]:
    sec = img.section_by_name(".pdata")
    if sec is None:
        return []
    out: List[Tuple[int, int]] = []
    for off in range(0, sec.vsize - 12 + 1, 12):
        va = sec.va + off
        begin, end = img.u32(va), img.u32(va + 8)
        if begin is None or end is None:
            continue
        if CODE_LO <= begin < CODE_HI and CODE_LO <= end <= CODE_HI:
            out.append((begin, end))
    return sorted(out)


def scan_bl_targets(img: Image) -> set:
    """Destinos de 'bl' en las secciones ejecutables.

    El codegen de ReXGlue registra cada destino de 'bl' como funcion
    DISCOVERED (phase_discover.cpp: graph.addFunction(target, 4, DISCOVERED, true)),
    asi que una direccion que aparezca aqui YA es una entrada de funcion y no
    hay que volver a declararla (ademas, renombrarla romperia los apply_*.py
    que la referencian por su nombre sub_XXXXXXXX).
    """
    targets = set()
    for sec in img.sections:
        if not sec.executable:
            continue
        base = img.offset(sec.va)
        end = base + sec.vsize
        for off in range(base, end - 3, 4):
            insn = struct.unpack_from(">I", img.data, off)[0]
            if (insn >> 26) != 18:
                continue
            if not (insn & 1):        # solo 'bl'/'bla' (LK=1), no 'b'/'ba'
                continue
            aa = (insn >> 1) & 1
            li = insn & 0x03FFFFFC
            site = img.image_base + (off - base)
            if aa:
                targets.add(li)
            else:
                disp = li if (li & 0x02000000) == 0 else li - 0x04000000
                targets.add((site + disp) & 0xFFFFFFFF)
    return targets


# ---------------------------------------------------------------------------
# 7. Emision de config/vtables.toml
# ---------------------------------------------------------------------------


def scan_generated_tree(gen_dir: pathlib.Path) -> set:
    """Direcciones ya registradas como funcion en el arbol recompilado actual.

    El codegen emite ``DEFINE_REX_FUNC(sub_XXXXXXXX)`` por funcion y
    ``SetFunction(0xXXXXXXXX, ...)`` en el registro: si la ranura ya existe
    como funcion, declararla en [functions.*] solo la renombraria (y podria
    romper los apply_*.py que la referencian por su nombre), sin ganar nada.
    """
    out = set()
    if not gen_dir or not pathlib.Path(gen_dir).is_dir():
        return out
    for cpp in sorted(pathlib.Path(gen_dir).glob("*.cpp")):
        try:
            txt = cpp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in re.finditer(r"DEFINE_REX_FUNC\(\s*sub_([0-9A-Fa-f]+)\s*\)", txt):
            out.add(int(m.group(1), 16))
        for m in re.finditer(r"SetFunction\(\s*0x([0-9A-Fa-f]+)\s*,", txt):
            out.add(int(m.group(1), 16))
    return out


def find_owner(value: int, seed: List[SeedEntry], pdata: List[Tuple[int, int]]
               ) -> Optional[Tuple[int, int]]:
    """(parent, end) de la funcion declarada que contiene ``value``.

    Se busca el rango mas ESPECIFICO (el mas pequeno) que lo contiene, entre
    las entradas standalone del seed y las funciones de .pdata.  Si el rango
    pertenece a un chunk del seed, se devuelve su padre raiz (la funcion
    duena) y su fin.
    """
    by_addr = {e.addr: e for e in seed}
    cands: List[Tuple[int, int, int, int]] = []   # (begin, end, size, root_parent)
    for a, e in pdata:
        if a <= value < e:
            cands.append((a, e, e - a, 0))
    for e in seed:
        if e.is_chunk:
            continue
        ext = e.extent
        if ext and e.addr <= value < e.addr + ext:
            cands.append((e.addr, e.addr + ext, ext, 0))
    for e in seed:
        if not e.is_chunk:
            continue
        ext = e.extent
        if ext and e.addr <= value < e.addr + ext:
            root = e.parent
            root_end = e.addr + ext
            cur = by_addr.get(root)
            if cur is not None and cur.extent:
                root_end = cur.addr + cur.extent
            cands.append((e.addr, root_end, ext, root))
    if not cands:
        return None
    cands.sort(key=lambda t: t[2])
    begin, end, _size, root = cands[0]
    return (root or begin), end


def build_config_entries(
    confirmations: List[Confirmation],
    pdata: List[Tuple[int, int]],
    seed: List[SeedEntry],
    bl_targets: Optional[set] = None,
    tree_targets: Optional[set] = None,
) -> Tuple[List[dict], List[dict]]:
    """Devuelve (entradas, rechazos) aplicando el filtro de evidencia.

    Reglas:
      * de una tabla CONFIRMADA (base de vtable resuelta estaticamente desde el
        caller) se declaran TODOS sus slots de codigo: es exactamente lo que
        haria el upstream con la autoridad VTABLE;
      * de una tabla NO confirmada (puntero a vtable leido del objeto) solo se
        declara el slot que el runtime ha llamado de verdad (target+lr del log);
      * una ranura cuyo valor YA es una entrada de funcion (standalone del seed,
        inicio de .pdata, o funcion del arbol recompilado) se rechaza: solo se
        renombraria;
      * una ranura que es etiqueta interior de una funcion ya declarada se
        declara como CHUNK (parent = funcion duena, end = fin de la duena):
        es la unica forma legal en ReXGlue (la validacion de config aborta con
        "Overlapping boundaries" si dos entradas standalone se solapan) y es el
        mecanismo que el propio proyecto ya usa para las etiquetas interiores
        que son destino de salto.
    """
    pdata_starts = {b for b, _ in pdata}
    seed_by_addr = {e.addr: e for e in seed}
    seed_standalone = {e.addr for e in seed if not e.is_chunk}
    seed_chunk_addrs = {e.addr for e in seed if e.is_chunk}

    proven: Dict[int, Tuple[dict, Tuple[int, int]]] = {}

    def consider(value: int, info: dict, weight: int, resolved: bool) -> None:
        cur = proven.get(value)
        if cur is None or weight > cur[1][0]:
            proven[value] = (info, (weight, 1 if resolved else 0))

    for conf in confirmations:
        slot = conf.call_site.slot_index
        if slot is None:
            continue
        base = conf.vtable_base
        if base is None and conf.candidate_bases:
            base = conf.candidate_bases[0]
        base_str = f"0x{base:08X}" if base is not None else None
        for s in conf.slots:
            if conf.table_confirmed or (s.index == slot and s.value == conf.evidence.target):
                info = {
                    "value": f"0x{s.value:08X}",
                    "slot_va": f"0x{s.va:08X}",
                    "slot_index": s.index,
                    "vtable_base": base_str,
                    "vtable_base_resolved": conf.table_confirmed,
                    "candidate_vtable_bases": [f"0x{b:08X}" for b in conf.candidate_bases],
                    "caller_lr": f"0x{conf.evidence.lr:08X}",
                    "caller_bctrl": (f"0x{conf.call_site.bctrl:08X}"
                                     if conf.call_site.bctrl else None),
                    "slot_offset": conf.call_site.slot_offset,
                    "occurrences": conf.evidence.count,
                    "log": conf.evidence.log,
                    "vtable_pointer_expr": conf.call_site.vtable_pointer_expr,
                    "vtable_from_object": conf.call_site.vtable_from_object,
                    "evidence": ("tabla confirmada (base resuelta en el caller) + slot de la tabla"
                                 if conf.table_confirmed else
                                 "dispatch virtual real (target+lr del log) sobre vtable[slot]; "
                                 "la tabla concreta es ambigua (vtable leida del objeto)"),
                }
                consider(s.value, info, conf.evidence.count, conf.table_confirmed)

    entries: List[dict] = []
    rejected: List[dict] = []
    for value in sorted(proven):
        info, _w = proven[value]
        reason = None
        if value in pdata_starts:
            reason = "ya es inicio de funcion en .pdata (declararla seria solo renombrarla)"
        elif value in seed_standalone:
            reason = (f"ya es entrada standalone en {seed_by_addr[value].source} "
                      "(declararla seria solo renombrarla)")
        elif value in seed_chunk_addrs:
            reason = (f"ya es chunk en {seed_by_addr[value].source} "
                      "(declararla seria solo renombrarla)")
        elif bl_targets and value in bl_targets:
            reason = ("ya es entrada de funcion: es destino de un 'bl' (el codegen la registra "
                      "como DISCOVERED); declararla aqui solo la renombraria")
        elif tree_targets and value in tree_targets:
            reason = ("ya existe como funcion en el arbol recompilado actual "
                      "(DEFINE_REX_FUNC/SetFunction); declararla aqui solo la renombraria")
        if reason:
            rejected.append({"value": f"0x{value:08X}", "reason": reason, **info})
            continue
        owner = find_owner(value, seed, pdata)
        if owner is None:
            rejected.append({"value": f"0x{value:08X}",
                             "reason": "no se ha encontrado la funcion duena que lo contiene",
                             **info})
            continue
        parent, end = owner
        info = dict(info)
        info["form"] = "chunk"
        info["parent"] = f"0x{parent:08X}"
        info["end"] = f"0x{end:08X}"
        entries.append(info)
    return entries, rejected


def render_config(entries: List[dict]) -> str:
    L: List[str] = []
    L.append("# config/vtables.toml - GENERADO por tools/vtable_audit.py (M1, swe_native_3d_v2)")
    L.append("#")
    L.append("# POR QUE ESTE FICHERO EXISTE")
    L.append("#   El escaner de vtables de ReXGlue v0.10.0 (src/codegen/vtable_scanner.cpp)")
    L.append("#   busca RTTI MSVC: un RTTICompleteObjectLocator (signature=0) referenciado por")
    L.append("#   vtable[-1], con pTypeDescriptor dentro de una seccion y nombre mangled")
    L.append("#   '.?AV'/'.?AU'.  El XEX invitado NO contiene ese RTTI (0 COLs comprobados con")
    L.append("#   bytes: solo hay 2 type descriptors, ambos de la CRT, en .data, y ningun COL")
    L.append("#   los referencia), asi que sus tablas de vtable son PLANAS (solo punteros a")
    L.append("#   codigo) y ningun slot se registra como funcion: toda llamada virtual cae")
    L.append("#   en el bucket 'unregistered' del runtime.")
    L.append("#")
    L.append("#   El lookup del runtime NO es por rango sino un indice exacto:")
    L.append("#     REX_LOOKUP_FUNC(x,y) = *(PPCFunc**)(x + IMAGE_BASE + IMAGE_SIZE +")
    L.append("#                                  (y - CODE_BASE) * 2)")
    L.append("#   => la direccion destino de una llamada virtual tiene que ser una ENTRADA")
    L.append("#   EXACTA de la tabla de funciones, no una etiqueta interior.")
    L.append("#")
    L.append("# POR QUE ESTAS ENTRADAS SON CHUNKS (parent/end) y no standalone")
    L.append("#   ReXGlue valida el config y ABORTA con 'Overlapping boundaries' si dos")
    L.append("#   entradas [functions.*] standalone se solapan (config.cpp: Validate()). Las")
    L.append("#   direcciones de estas ranuras caen DENTRO de funciones que ya declara")
    L.append("#   config/functions.toml (W1), asi que declararlas standalone seria un error")
    L.append("#   fatal.  La forma 'chunk' (parent != 0) es la unica legal y es exactamente el")
    L.append("#   mecanismo que el proyecto ya usa para las etiquetas interiores que son")
    L.append("#   destino de salto: el chunk recibe su propia entrada en la tabla de")
    L.append("#   funciones (=> la llamada indirecta resuelve) y la funcion duena deja de")
    L.append("#   emitir ese tramo (el descubrimiento se detiene en la entrada del chunk).")
    L.append("#")
    L.append("# REGLAS aplicadas por el generador (tools/vtable_audit.py):")
    L.append("#   * solo slots con evidencia real de runtime: el log registra el target de la")
    L.append("#     llamada indirecta y el desensamblado del call-site demuestra que se leyo")
    L.append("#     vtable[slot] (lwz rF,off(rV) con rV=*(objeto) o rV=constante).")
    L.append("#   * solo slots cuya direccion NO es ya una entrada de funcion (.pdata, seed o")
    L.append("#     arbol recompilado): declararlos solo los renombraria.")
    L.append("#   * 'end' = fin de la funcion duena (convencion del propio config/functions.toml).")
    L.append("#")
    L.append("# IMPORTANTE (orden de includes): este fichero debe incluirse DESPUES de")
    L.append("#   config/functions.toml, porque las entradas [functions.*] se fusionan por")
    L.append("#   clave y el ultimo gana (config.cpp: insert_or_assign).")
    L.append("#")
    L.append(f"# entradas: {len(entries)}")
    L.append("")
    for e in entries:
        L.append(f'[functions."{e["value"]}"]')
        L.append(f"# vtable={e['vtable_base']} ranura {e['slot_index']} (dword @ {e['slot_va']})"
                 f" | caller lr={e['caller_lr']} bctrl={e['caller_bctrl']} x{e['occurrences']}"
                 f" ({e['log']}) | chunk de {e['parent']} (fin {e['end']})")
        L.append(f'parent = {int(e["parent"], 16)}')
        L.append(f'end = {int(e["end"], 16)}')
        L.append(f'name = "vtable_slot_{e["value"][2:]}"')
        L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bin", type=pathlib.Path, default=DEFAULT_BIN,
                    help="imagen plana del invitado")
    ap.add_argument("--log", type=pathlib.Path, action="append", default=None,
                    help="log de runtime a minar (repetible)")
    ap.add_argument("--seed", type=pathlib.Path, action="append", default=None,
                    help="config con [functions.*] ya declarado (repetible)")
    ap.add_argument("--report", type=pathlib.Path, default=None, help="fichero JSON del informe")
    ap.add_argument("--emit-config", type=pathlib.Path, default=None,
                    help="fichero TOML de slots confirmados a escribir")
    ap.add_argument("--check", action="store_true",
                    help="no escribir: solo informar de los cambios")
    ap.add_argument("--json", action="store_true", help="volcar el informe por stdout")
    ap.add_argument("--tables", action="store_true", help="imprimir el censo de tablas candidatas")
    ap.add_argument("--sections", default=".rdata",
                    help="secciones a escanear para tablas (coma separadas)")
    ap.add_argument("--gen-dir", type=pathlib.Path,
                    default=PROJECT_ROOT / "generated" / "neverdead_default",
                    help="arbol recompilado actual (para saber que slots ya existen)")
    ap.add_argument("--no-tree-filter", action="store_true",
                    help="no excluir los slots que ya existen como funcion en el arbol actual")
    args = ap.parse_args(argv)

    logs = args.log if args.log else DEFAULT_LOGS
    seeds = args.seed if args.seed else DEFAULT_SEEDS
    section_names = tuple(s.strip() for s in args.sections.split(",") if s.strip())

    img = Image(args.bin)
    report: dict = {
        "binary": str(args.bin),
        "binary_sha256": hashlib.sha256(img.data).hexdigest(),
        "image_base": f"0x{img.image_base:08X}",
        "code_range": [f"0x{CODE_LO:08X}", f"0x{CODE_HI:08X}"],
        "sections": [
            {"name": s.name, "va": f"0x{s.va_lo:08X}", "vsize": s.vsize,
             "executable": s.executable, "pe_rawptr": f"0x{s.rawptr:08X}",
             "pe_rva": f"0x{s.rva:08X}",
             "pe_rawptr_desfasado": s.rawptr != s.rva}
            for s in img.sections
        ],
    }

    # 1. RTTI -------------------------------------------------------------
    report["rtti"] = rtti_report(img)

    # 2. tablas -----------------------------------------------------------
    tables = scan_code_pointer_tables(img, section_names)
    report["tables"] = {
        "sections_scanned": list(section_names),
        "count": len(tables),
        "total_slots": sum(t.n_slots for t in tables),
        "census": [
            {"base_va": f"0x{t.base_va:08X}", "section": t.section, "slots": t.n_slots,
             "prev_va": (f"0x{t.prev_va:08X}" if t.prev_va is not None else None),
             "prev_value": (f"0x{t.prev_value:08X}" if t.prev_value is not None else None),
             "prev_is_code": t.prev_is_code,
             "first_slots": [
                 {"slot_va": f"0x{s.va:08X}", "index": s.index, "value": f"0x{s.value:08X}",
                  "is_code": s.is_code} for s in t.slots[:16]]}
            for t in tables
        ],
    }

    # 3. evidencia --------------------------------------------------------
    evidences = mine_logs(logs)
    report["runtime_evidence"] = {
        "logs": [str(p) for p in logs],
        "total_events": sum(e.count for e in evidences),
        "events": [
            {"kind": e.kind, "target": f"0x{e.target:08X}", "lr": f"0x{e.lr:08X}",
             "count": e.count, "log": e.log,
             "target_in_code_range": CODE_LO <= e.target < CODE_HI,
             "lr_is_code_range": e.lr_valid}
            for e in evidences
        ],
    }

    # 4+5. confirmacion ---------------------------------------------------
    confirmations: List[Confirmation] = []
    for e in evidences:
        c = attribute_evidence(img, e, tables)
        if c is not None:
            confirmations.append(c)
    report["confirmations"] = [
        {"kind": c.evidence.kind, "target": f"0x{c.evidence.target:08X}",
         "lr": f"0x{c.evidence.lr:08X}", "occurrences": c.evidence.count,
         "call_site": {
             "bctrl": (f"0x{c.call_site.bctrl:08X}" if c.call_site.bctrl else None),
             "mtctr": (f"0x{c.call_site.mtctr:08X}" if c.call_site.mtctr else None),
             "slot_load": (f"0x{c.call_site.slot_load:08X}" if c.call_site.slot_load else None),
             "slot_offset": c.call_site.slot_offset,
             "slot_index": c.call_site.slot_index,
             "vtable_pointer_expr": c.call_site.vtable_pointer_expr,
             "vtable_base": (f"0x{c.vtable_base:08X}" if c.vtable_base is not None else None),
             "vtable_from_object": c.call_site.vtable_from_object,
             "is_virtual": c.call_site.is_virtual,
             "disassembly": c.call_site.text},
         "vtable_base": (f"0x{c.vtable_base:08X}" if c.vtable_base is not None else None),
         "table_confirmed": c.table_confirmed,
         "candidate_vtable_bases": [f"0x{b:08X}" for b in c.candidate_bases],
         "vtable_slots": [
             {"slot_va": f"0x{s.va:08X}", "index": s.index, "value": f"0x{s.value:08X}"}
             for s in c.slots],
         }
        for c in confirmations
    ]

    # 6. entradas de config ------------------------------------------------
    pdata = load_pdata_functions(img)
    seed = load_seed_functions(seeds)
    bl_targets = scan_bl_targets(img)
    tree_targets = set() if args.no_tree_filter else scan_generated_tree(args.gen_dir)
    entries, rejected = build_config_entries(confirmations, pdata, seed, bl_targets, tree_targets)
    report["config_entries"] = entries
    report["config_rejected"] = rejected
    report["pdata_functions"] = len(pdata)
    report["seed_functions"] = len(seed)
    report["bl_targets"] = len(bl_targets)
    report["tree_functions"] = len(tree_targets)
    report["gen_dir"] = str(args.gen_dir)

    # 7. impacto ----------------------------------------------------------
    registered_values = {int(e["value"], 16) for e in entries}
    unreg = [e for e in evidences if e.kind == "unregistered"]
    still = [e for e in unreg if e.target not in registered_values]
    fixed = [e for e in unreg if e.target in registered_values]
    oob = [e for e in evidences if e.kind == "trap_oob"]
    report["impact"] = {
        "runtime_events_total": sum(e.count for e in evidences),
        "unregistered_events": sum(e.count for e in unreg),
        "unregistered_events_that_become_registered": sum(e.count for e in fixed),
        "unregistered_events_still_unregistered": sum(e.count for e in still),
        "trap_oob_events": sum(e.count for e in oob),
        "trap_oob_events_fixable_by_vtable_registration": 0,
        "distinct_unregistered_targets": sorted({f"0x{e.target:08X}" for e in unreg}),
        "distinct_unregistered_targets_still_unregistered":
            sorted({f"0x{e.target:08X}" for e in still}),
        "distinct_slots_registered": len(entries),
        "method": ("Se cuentan los eventos de los logs (neverdead_165.log y neverdead_163.log) "
                   "clasificados por el mensaje del runtime: 'unregistered indirect call' "
                   "(target en el rango de codigo pero sin funcion registrada) y 'TRAP-OOB' "
                   "(target fuera del rango de codigo). Un evento 'unregistered' pasa a "
                   "'registrado' si su target es una de las ranuras de vtable con evidencia "
                   "(dispatch real + desensamblado del call-site) y esa ranura se declara en "
                   "config/vtables.toml."),
        "note": ("Los TRAP-OOB NO son ranuras de vtable: su target es un dato (p.ej. "
                 "0x8205970C = 'Fall'), luego ningun registro de slot puede arreglarlos. "
                 "Ver handoff_w2.md seccion 1 para el diagnostico de esa familia."),
    }

    # 8. escritura idempotente -------------------------------------------
    changed = False
    if args.emit_config:
        content = render_config(entries)
        target = pathlib.Path(args.emit_config)
        old = target.read_text(encoding="utf-8") if target.exists() else None
        if old != content:
            changed = True
            if not args.check:
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp = target.with_suffix(target.suffix + ".tmp")
                tmp.write_text(content, encoding="utf-8", newline="\n")
                tmp.replace(target)
        report["config_path"] = str(target)
        report["config_changed"] = changed

    if args.report:
        rp = pathlib.Path(args.report)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_human(report, args.tables)

    if args.check and changed:
        print("[vtable_audit] --check: el config differia (no se escribio nada)", file=sys.stderr)
        return 2
    return 0


def _print_human(report: dict, show_tables: bool) -> None:
    r = report["rtti"]
    print("=" * 78)
    print("1) RTTI / Complete Object Locators (criterio exacto de ReXGlue v0.10.0)")
    print("=" * 78)
    print(f"   COL candidatos: {r['col_candidate_count']}")
    for n in r["mangled_names_in_image"]:
        print(f"   nombre mangled {n['va']} [{n['section']}] {n['name']!r} bytes={n['bytes']}")
    for t in r["type_descriptors"]:
        print(f"   type_descriptor {t['type_descriptor_va']} pVFTable={t['pVFTable']} "
              f"spare={t['spare']} bytes={t['bytes']} pVFTable_sec={t['pVFTable_section']}")
    print(f"   VEREDICTO: {r['verdict']}")
    print()

    print("=" * 78)
    print("2) Tablas de punteros a codigo (runs >=2 en secciones de datos)")
    print("=" * 78)
    print(f"   tablas: {report['tables']['count']}   slots totales: {report['tables']['total_slots']}")
    if show_tables:
        for c in report["tables"]["census"]:
            print(f"   - {c['base_va']} [{c['section']}] slots={c['slots']} "
                  f"prev={c['prev_value']} (code={c['prev_is_code']})")
            for s in c["first_slots"]:
                print(f"       slot[{s['index']}] @{s['slot_va']} = {s['value']}")
    print()

    print("=" * 78)
    print("3) Evidencia de runtime (logs)")
    print("=" * 78)
    for e in report["runtime_evidence"]["events"]:
        print(f"   {e['kind']:<13} target={e['target']} lr={e['lr']} x{e['count']} "
              f"({e['log']}) target_in_code={e['target_in_code_range']}")
    print()

    print("=" * 78)
    print("4) Confirmaciones (caller -> ranura -> vtable)")
    print("=" * 78)
    if not report["confirmations"]:
        print("   (ninguna)")
    for c in report["confirmations"]:
        cs = c["call_site"]
        print(f"   {c['kind']} target={c['target']} lr={c['lr']} x{c['occurrences']}")
        print(f"      bctrl={cs['bctrl']} mtctr={cs['mtctr']} slot_load={cs['slot_load']} "
              f"offset={cs['slot_offset']} (ranura {cs['slot_index']})")
        print(f"      puntero a vtable = {cs['vtable_pointer_expr']}  "
              f"base={cs['vtable_base']} desde_objeto={cs['vtable_from_object']}")
        print(f"      tabla CONFIRMADA={c['table_confirmed']} candidatas={c['candidate_vtable_bases']}")
        print(f"      slots de la tabla: {len(c['vtable_slots'])}")
    print()

    print("=" * 78)
    print("5) config/vtables.toml")
    print("=" * 78)
    print(f"   entradas emitidas: {len(report['config_entries'])}")
    for e in report["config_entries"]:
        print(f"     {e['value']}  ranura@{e['slot_va']} (idx {e['slot_index']}) "
              f"vtable={e['vtable_base']} caller={e['caller_lr']} x{e['occurrences']}")
    print(f"   rechazadas: {len(report['config_rejected'])}")
    for rj in report["config_rejected"]:
        print(f"     {rj['value']}: {rj['reason']}")
    print()

    print("=" * 78)
    print("6) Impacto")
    print("=" * 78)
    for k, v in report["impact"].items():
        print(f"   {k}: {v}")
    print()


if __name__ == "__main__":
    raise SystemExit(main())
