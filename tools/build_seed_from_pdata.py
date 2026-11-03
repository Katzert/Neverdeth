#!/usr/bin/env python3
"""
tools/build_seed_from_pdata.py - build the CORRECT ReXGlue function table.

================================================================================
GROUND TRUTH (every number below is read from the bytes, never invented)
================================================================================

GUEST IMAGE
    `NeverDead_base.exe` is the FLAT guest image: the guest virtual address
    maps to the file with

        file_offset = guest_va - 0x82000000

    The PE section table of that file is NOT usable (its `PointerToRawData`
    for `.text` is 0x2000 off), so the flat mapping is the only one used.

.pdata  (the function table of the guest)
    raw offset 0x16F800, length 0x238D0 bytes, records of **8 bytes**, big
    endian:

        struct pdata_record { uint32_t va; uint32_t flags; };

    0x238D0 / 8 = **18.202 records**.  The `va` field is the *entry point* of a
    guest function; `flags` is the unwind descriptor (0x4000_xxxx, never a
    virtual address).

    >>> The 12-byte `(begin, flags, end)` layout is WRONG. <<<
    With 12-byte records the parser reads the `flags` word of record N as the
    `begin` of record N+1 and the `va` of record N+1 as its `end`; the result
    is a table in which every third `begin` is a garbage flags word
    (e.g. `0x40008104`) and only 1/3 of the real entries survive.  That is the
    origin of the "5.895 extents" figure quoted by the earlier phases.

    Records whose `va` lands in `.text` (0x821B2200 .. 0x827C03E4):
    17.689 by range, of which **17.688 are 4-aligned real entries** (one
    misaligned value, 0x827452E9, is a false positive produced by the 514
    non-VA records that sit at indices 3328..3841 of the table - see
    "KNOWN ANOMALY" below).

    The extent of a function is the "next begin" rule:

        end(begin) = the next `.pdata` begin (exclusive)

    which is what the guest linker itself guarantees: the function table is
    sorted and the entries tile the code.

KNOWN ANOMALY (documented, not worked around)
    Indices 3328..3841 of the `.pdata` table (4112 bytes) hold 514 records
    whose `va` field is random-looking data (0x080BCC07, 0xD36D6623, ...).
    They are not virtual addresses.  Consequences, all measured:
      * exactly ONE of them (0x827452E9) falls inside the `.text` range and
        it is not 4-aligned, so the 4-alignment filter removes it;
      * the VA gap they hide is 0x822C5DC8 .. 0x822C6008 (0x240 bytes),
        i.e. one function's worth of code without its own table entry;
      * the rest of the table (17.688 entries) is a clean, sorted, 4-aligned
        partition of `.text`.

================================================================================
WHAT IS EMITTED  ->  config/functions.toml
================================================================================

`[functions."0xADDR"]` is a CONFIG entry of the ReXGlue codegen
(`include/rex/codegen/config.h`, upstream v0.10.0).  Its keys are:

    size             explicit size in bytes   (mutually exclusive with `end`)
    end              end address, EXCLUSIVE    (mutually exclusive with `size`)
    name             custom symbol name        (optional)
    parent           parent function address    (0 = standalone, != 0 = chunk)
    share_registers  keep the non-volatiles in ctx instead of localizing them

`getSize(address)` prefers `size`; `end` is used only when `size == 0`.

Three kinds of entry are produced, all of them derived from the bytes:

 1. STANDALONE - every `.pdata` begin inside `.text`.
        [functions."0x821CED90"]
        end = 2178057680          # == the next .pdata begin, exclusive
    The extent is the real `.pdata` extent, so the body can never be cut.

 2. CHUNK - an *interior label*: a `b`/`bl` target that falls strictly inside
    the extent of a `.pdata` function AND is reached by a branch whose source
    lies OUTSIDE that extent (a genuine second entry point - a shared tail).
        [functions."0x821CF190"]
        parent = 2178009576       # the .pdata function that owns the region
        end = 2178057728          # real end of the block that starts here
    A chunk is NEVER declared standalone: a standalone entry at an interior
    address would overlap the owner and would freeze the analyser (a CONFIG
    entry with exact boundaries cannot be corrected any more).
    Interior labels that are NOT reached from outside their owner are ordinary
    internal labels; they are left alone (declaring them would split the
    owner for no reason).

 3. GAP SEED - a `b`/`bl` target that lies OUTSIDE every `.pdata` extent
    (4-aligned, inside `.text`).  Such an address has no table entry at all,
    so it is declared as an empty CONFIG entry and the discovery finds its
    body:
        [functions."0x821B2410"]

`share_registers` is written ONLY when the live-in analysis proves that the
chunk reads a non-volatile register (r14..r31) that its owner had already
defined.  See `--liveins`.

================================================================================
MODES
================================================================================

    python tools/build_seed_from_pdata.py --emit      # write config/functions.toml
    python tools/build_seed_from_pdata.py --check      # verify the written file
    python tools/build_seed_from_pdata.py --census     # truncation census of a tree
    python tools/build_seed_from_pdata.py --liveins    # live-in report
    python tools/build_seed_from_pdata.py --json       # machine readable summary

Exit codes:
    0  success / no discrepancy
    1  discrepancy found (--check) or a failure
    2  missing prerequisite (.pdata / Capstone / guest image)
"""

from __future__ import annotations

import argparse
import bisect
import json
import re
import struct
import sys
import time
from pathlib import Path

try:
    from capstone import (CS_ARCH_PPC, CS_MODE_64, CS_MODE_BIG_ENDIAN, Cs,
                          CS_GRP_JUMP, CS_GRP_CALL)
except ImportError:  # pragma: no cover - prerequisite check
    Cs = None  # type: ignore[assignment]


# --------------------------------------------------------------------------
# Ground-truth constants (guest image layout)
# --------------------------------------------------------------------------
GUEST_BASE = 0x82000000          # flat image base

PDATA_RAW = 0x16F800             # file offset of .pdata inside the flat image
PDATA_LEN = 0x238D0              # length of .pdata in bytes
PDATA_REC = 8                    # bytes per record: {u32 va, u32 flags}

TEXT_LO = 0x821B2200             # first VA of .text
TEXT_HI = 0x827C03E4             # one past the last VA of .text

NONVOLATILE = tuple(range(14, 32))   # r14 .. r31 (Xbox 360 ABI)

SEH_MAGIC = 0x19930522           # scope-table magic (absent in this XEX)


class Missing(Exception):
    """A prerequisite of the ground truth is missing."""


# --------------------------------------------------------------------------
# Guest image
# --------------------------------------------------------------------------
class Guest:
    """The flat guest image: file_offset = guest_va - GUEST_BASE."""

    def __init__(self, path: Path):
        self.path = path
        if not path.is_file():
            raise Missing(f"guest image not found: {path}")
        self.data = path.read_bytes()
        self.md = Cs(CS_ARCH_PPC, CS_MODE_BIG_ENDIAN | CS_MODE_64)
        self.md.skipdata = True
        self.md.detail = False

    def off(self, va: int) -> int:
        return va - GUEST_BASE

    def in_image(self, va: int, n: int = 4) -> bool:
        o = self.off(va)
        return 0 <= o and o + n <= len(self.data)

    def word(self, va: int) -> bytes:
        o = self.off(va)
        return self.data[o:o + 4]

    def decode(self, va: int):
        """Decode ONE 32-bit word.  Returns None when the word is not code."""
        if not self.in_image(va):
            return None
        for ins in self.md.disasm(self.data[self.off(va):self.off(va) + 4], va, count=1):
            if ins.mnemonic == ".byte":
                return None
            return ins
        return None

    def decode_at(self, va: int, count: int = 1):
        """Decode `count` words.  Returns the list (possibly empty)."""
        if not self.in_image(va, 4 * count):
            return []
        out = []
        for ins in self.md.disasm(self.data[self.off(va):self.off(va) + 4 * count],
                                  va, count=count):
            if ins.mnemonic == ".byte":
                break
            out.append(ins)
        return out

    def branch_target(self, ins) -> int | None:
        """The direct target of a branch instruction, or None."""
        if not ins.mnemonic.startswith("b"):
            return None
        tok = ins.op_str.split(",")[0].strip()
        if not tok.startswith("0x"):
            return None
        try:
            return int(tok, 16)
        except ValueError:
            return None

    def is_terminator(self, ins, va: int, upper: int | None) -> bool:
        """True when `ins` ends the block that starts at/before `va`.

        The rules are the ones the codegen itself uses when it walks a body:
          * `blr` / `bctr`      -> the block ends (the instruction is emitted)
          * `b <outside>`       -> the block ends (the instruction is emitted)
          * a 4-byte zero hole  -> the block ends (the hole belongs to it)
        Conditional branches NEVER end a block.
        """
        m = ins.mnemonic
        if m in ("blr", "bctr"):
            return True
        if m == "b":
            tgt = self.branch_target(ins)
            if tgt is None or tgt < va or (upper is not None and tgt >= upper):
                return True
        return False


# --------------------------------------------------------------------------
# .pdata
# --------------------------------------------------------------------------
class Pdata:
    """The guest function table (8-byte records)."""

    def __init__(self, guest: Guest):
        raw = guest.data[PDATA_RAW:PDATA_RAW + PDATA_LEN]
        if len(raw) < PDATA_REC:
            raise Missing(".pdata is shorter than one record")
        n = len(raw) // PDATA_REC
        self.records = [struct.unpack_from(">II", raw, i * PDATA_REC) for i in range(n)]
        self.n_records = n
        self.n_range = sum(1 for va, _f in self.records if TEXT_LO <= va < TEXT_HI)
        # a real entry is 4-aligned: the single misaligned value of the
        # non-VA window is rejected here.
        self.begins = sorted(va for va, _f in self.records
                             if TEXT_LO <= va < TEXT_HI and va % 4 == 0)
        self.begin_set = set(self.begins)
        self._index = {b: i for i, b in enumerate(self.begins)}

    # -- lookups ---------------------------------------------------------
    def extent_end(self, begin: int) -> int:
        """Exclusive end of the function that begins at `begin`."""
        i = self._index[begin]
        if i + 1 < len(self.begins):
            return self.begins[i + 1]
        return TEXT_HI

    def owner(self, va: int) -> int | None:
        """The `.pdata` begin whose extent contains `va` (None when in a gap)."""
        i = bisect.bisect_right(self.begins, va) - 1
        if i < 0:
            return None
        b = self.begins[i]
        if b <= va < self.extent_end(b):
            return b
        return None

    def is_begin(self, va: int) -> bool:
        return va in self.begin_set

    def real_length(self, va: int) -> int | None:
        """Real body length of `va` (`.pdata` extent), or None when in a gap."""
        b = self.owner(va)
        if b is None:
            return None
        return self.extent_end(b) - va


# --------------------------------------------------------------------------
# Branch-target sweep (the ONLY oracle: Capstone)
# --------------------------------------------------------------------------
class Sweep:
    """One linear Capstone sweep of `.text`.

    Collects, for every direct `b`/`bl`, the (source, mnemonic) pairs, so
    that a target can be classified as an interior label or a gap address.
    """

    def __init__(self, guest: Guest, pdata: Pdata):
        self.guest = guest
        self.pdata = pdata
        self.n_instructions = 0
        self.sites: dict[int, list[tuple[int, str]]] = {}
        blob = guest.data[guest.off(TEXT_LO):guest.off(TEXT_LO) + (TEXT_HI - TEXT_LO)]
        for ins in guest.md.disasm(blob, TEXT_LO):
            self.n_instructions += 1
            m = ins.mnemonic
            if not m.startswith("b"):
                continue
            tgt = guest.branch_target(ins)
            if tgt is None:
                continue
            self.sites.setdefault(tgt, []).append((ins.address, m))

    def external_sources(self, target: int) -> list[tuple[int, str]]:
        """Branch sites that reach `target` from OUTSIDE its owning extent."""
        own = self.pdata.owner(target)
        if own is None:
            return list(self.sites.get(target, ()))
        end = self.pdata.extent_end(own)
        return [(s, m) for s, m in self.sites.get(target, ()) if not (own <= s < end)]


# --------------------------------------------------------------------------
# Block walk (the codegen's own body rules)
# --------------------------------------------------------------------------
def walk_block(guest: Guest, va: int, upper: int | None) -> tuple[int, str, int]:
    """Walk the guest body that starts at `va`.

    Returns (end, reason, n_instructions).  `end` is exclusive and always
    > va.  The terminator rules are the codegen's own (see Guest.is_terminator).
    """
    cur = va
    n = 0
    while True:
        if upper is not None and cur >= upper:
            return cur, "bound", n
        ins = guest.decode(cur)
        if ins is None:
            # a 4-byte hole (or a word Capstone cannot decode): it belongs to
            # the block and the block ends there.
            return cur + 4, "hole", n
        n += 1
        if guest.is_terminator(ins, va, upper):
            return cur + 4, f"term-{ins.mnemonic}", n
        cur += 4
        if n > 1 << 20:
            return cur, "budget", n


# --------------------------------------------------------------------------
# Entry model
# --------------------------------------------------------------------------
class Entry:
    """One `[functions."0x..."]` entry of the seed."""

    __slots__ = ("va", "kind", "end", "parent", "share_registers", "note")

    def __init__(self, va: int, kind: str, end: int | None = None,
                 parent: int | None = None, note: str = ""):
        self.va = va
        self.kind = kind          # standalone | chunk | gap
        self.end = end            # exclusive end (None for a gap seed)
        self.parent = parent      # parent VA (None for a standalone / gap)
        self.share_registers = False
        self.note = note

    @property
    def size(self) -> int | None:
        return None if self.end is None else self.end - self.va


# --------------------------------------------------------------------------
# Seed construction
# --------------------------------------------------------------------------
def build_entries(guest: Guest, pdata: Pdata, sweep: Sweep) -> dict[int, Entry]:
    """Build the whole function table from the ground truth."""
    entries: dict[int, Entry] = {}

    # --- 1. standalone: every real .pdata begin --------------------------
    for b in pdata.begins:
        entries[b] = Entry(b, "standalone", end=pdata.extent_end(b),
                           note="pdata begin")

    # --- 2. chunks: interior labels reached from outside their owner -----
    chunk_labels: dict[int, list[int]] = {}     # owner -> [label, ...]
    for target in sorted(sweep.sites):
        if pdata.is_begin(target):
            continue                       # already a standalone entry
        owner = pdata.owner(target)
        if owner is None:
            continue                       # a gap address, handled below
        if not sweep.external_sources(target):
            continue                       # ordinary internal label
        chunk_labels.setdefault(owner, []).append(target)

    for owner, labels in chunk_labels.items():
        parent_end = pdata.extent_end(owner)
        for lab in labels:
            blk_end, reason, _n = walk_block(guest, lab, parent_end)
            if blk_end <= lab:                  # degenerate, never emit
                continue
            # NOTE: the chunk keeps its OWN block end.  It is never clipped at
            # the next chunk label: clipping would cut the chunk before its
            # terminator (the block that starts at the label continues past the
            # next label), and a body without its terminator is exactly the
            # "dead tail" class of bug this table exists to remove.  Two chunks
            # of the same parent may therefore overlap; the ReXGlue codegen
            # exempts chunks from the boundary-overlap test, and both chunks
            # report the SAME parent, so the parent lookup stays unambiguous.
            entries[lab] = Entry(lab, "chunk", end=blk_end, parent=owner,
                                 note=f"interior label of 0x{owner:08X} "
                                      f"({reason})")

    # --- 3. gap seeds: branch targets outside every .pdata extent -------
    for target in sorted(sweep.sites):
        if pdata.is_begin(target):
            continue
        if pdata.owner(target) is not None:
            continue
        if target % 4 or not (TEXT_LO <= target < TEXT_HI):
            continue                       # not code, or not in .text
        entries[target] = Entry(target, "gap", end=None, parent=None,
                                note="branch target outside every .pdata extent")

    return entries


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------
def render_toml(entries: dict[int, Entry]) -> str:
    """Render the table in the exact ReXGlue config format.

    TOML has no hexadecimal integers, so every number is decimal.
    """
    n_standalone = sum(1 for e in entries.values() if e.kind == "standalone")
    n_chunk = sum(1 for e in entries.values() if e.kind == "chunk")
    n_gap = sum(1 for e in entries.values() if e.kind == "gap")
    n_share = sum(1 for e in entries.values() if e.share_registers)

    lines = [
        "# ReXGlue function table for NeverDead (Xbox 360 -> native).",
        "#",
        "# GENERATED by tools/build_seed_from_pdata.py - do not edit by hand.",
        "# Every value is derived from the guest bytes:",
        "#   * .pdata        raw 0x16F800, 0x238D0 B, 8-byte big-endian {va, flags}",
        "#                    (18202 records; 17688 of them are 4-aligned entries",
        "#                     inside .text)",
        "#   * the extent of a function is the NEXT .pdata begin (exclusive)",
        "#   * interior labels are CHUNKS (parent = the owning .pdata begin);",
        "#     they are never declared standalone",
        "#   * branch targets outside every .pdata extent are gap seeds ({})",
        "#   * Capstone 5.0.7 (CS_ARCH_PPC, CS_MODE_BIG_ENDIAN|CS_MODE_64) is the",
        "#     only decoder used",
        "#",
        f"# entries: {len(entries)}"
        f"  (standalone {n_standalone}, chunk {n_chunk}, gap {n_gap},"
        f" share_registers {n_share})",
        "",
    ]
    for va in sorted(entries):
        e = entries[va]
        lines.append(f'[functions."0x{va:08X}"]')
        if e.kind == "standalone":
            lines.append(f"end = {e.end}")
        elif e.kind == "chunk":
            lines.append(f"parent = {e.parent}")
            lines.append(f"end = {e.end}")
            if e.share_registers:
                lines.append("share_registers = true")
        else:                                    # gap seed
            pass                                   # empty table on purpose
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


# --------------------------------------------------------------------------
# Live-in analysis (flow sensitive)
# --------------------------------------------------------------------------
# Capstone 5.0.7 does NOT implement `regs_access()` for PowerPC
# (`CS_ERR_ARCH`), so the read/write sets of every guest instruction are
# derived from the *mnemonic + operand list* that Capstone prints.  The
# classification is a closed table: an instruction that is not in the table
# makes the analysis INCONCLUSIVE for the chunk that contains it, so an
# unknown instruction can never produce a wrong `share_registers` mark.
#
# Roles
#   load      dst = t0                      reads = base(t1)
#   loadu     dst = t0, base(t1)            reads = base(t1)
#   loadidx   dst = t0                      reads = t1, t2
#   loadidxu  dst = t0, t1                  reads = t1, t2
#   store     reads = t0, base(t1)
#   storeu    reads = t0, base(t1)          writes = base(t1)
#   storeidx  reads = t0, t1, t2
#   storeidxu reads = t0, t1, t2            writes = t1
#   fload     reads = base(t1)              (float load: the data is an FPR)
#   floadu    reads = base(t1)              writes = base(t1)
#   floadidx  reads = t1, t2
#   fstore    reads = base(t1)
#   fstoreu   reads = base(t1)              writes = base(t1)
#   fstoreidx reads = t1, t2
#   cmp       reads = every integer register of the operands
#   trap      reads = every integer register of the operands
#   read0     reads = every integer register of the operands (writes a SPR/LR)
#   alu       dst = t0                      reads = the other integer registers
#   none      no integer register
#   unknown   -> the analysis is inconclusive
# --------------------------------------------------------------------------
INT_REG_RE = re.compile(r"^r(\d+)$")
BASE_REG_RE = re.compile(r"\((r\d+)\)$")


def _toks(op_str: str) -> list[str]:
    return [t.strip() for t in op_str.split(",") if t.strip()]


def _base(tok: str) -> int | None:
    """The integer base register of a memory operand ("8(r2)" -> 2)."""
    m = BASE_REG_RE.search(tok)
    if m:
        return int(m.group(1)[1:])
    return None


def _ints(tokens: list[str]) -> set[int]:
    out = set()
    for t in tokens:
        m = INT_REG_RE.match(t)
        if m:
            out.add(int(m.group(1)))
    return out


def _dst(tok: str) -> int | None:
    m = INT_REG_RE.match(tok)
    return int(m.group(1)) if m else None


ROLE_LOAD = "load"
ROLE_LOADU = "loadu"
ROLE_LOADIDX = "loadidx"
ROLE_LOADIDXU = "loadidxu"
ROLE_STORE = "store"
ROLE_STOREU = "storeu"
ROLE_STOREIDX = "storeidx"
ROLE_STOREIDXU = "storeidxu"
ROLE_FLOAD = "fload"
ROLE_FLOADU = "floadu"
ROLE_FLOADIDX = "floadidx"
ROLE_FLOADIDXU = "floadidxu"
ROLE_FSTORE = "fstore"
ROLE_FSTOREU = "fstoreu"
ROLE_FSTOREIDX = "fstoreidx"
ROLE_CMP = "cmp"
ROLE_TRAP = "trap"
ROLE_READ0 = "read0"
ROLE_ALU = "alu"
ROLE_NONE = "none"
ROLE_UNKNOWN = "unknown"

ROLES: dict[str, str] = {
    # --- integer loads -------------------------------------------------
    "lwz": ROLE_LOAD, "lbz": ROLE_LOAD, "lhz": ROLE_LOAD, "lha": ROLE_LOAD,
    "lwa": ROLE_LOAD, "ld": ROLE_LOAD, "lmw": ROLE_LOAD, "lq": ROLE_LOAD,
    "lwzu": ROLE_LOADU, "lbzu": ROLE_LOADU, "lhzu": ROLE_LOADU,
    "lhau": ROLE_LOADU, "lwau": ROLE_LOADU, "ldu": ROLE_LOADU,
    "lwzx": ROLE_LOADIDX, "lbzx": ROLE_LOADIDX, "lhzx": ROLE_LOADIDX,
    "lhax": ROLE_LOADIDX, "ldzx": ROLE_LOADIDX, "lwarx": ROLE_LOADIDX,
    "ldarx": ROLE_LOADIDX, "lwax": ROLE_LOADIDX,
    "lwzux": ROLE_LOADIDXU, "lbzux": ROLE_LOADIDXU, "lhzux": ROLE_LOADIDXU,
    "ldux": ROLE_LOADIDXU,
    # --- integer stores ------------------------------------------------
    "stw": ROLE_STORE, "stb": ROLE_STORE, "sth": ROLE_STORE, "std": ROLE_STORE,
    "stmw": ROLE_STORE, "stq": ROLE_STORE,
    "stwu": ROLE_STOREU, "stbu": ROLE_STOREU, "sthu": ROLE_STOREU,
    "stdu": ROLE_STOREU,
    "stwx": ROLE_STOREIDX, "stbx": ROLE_STOREIDX, "sthx": ROLE_STOREIDX,
    "stdx": ROLE_STOREIDX, "stwcx.": ROLE_STOREIDX, "stdcx.": ROLE_STOREIDX,
    "stwux": ROLE_STOREIDXU, "stbux": ROLE_STOREIDXU, "sthux": ROLE_STOREIDXU,
    "stdux": ROLE_STOREIDXU,
    # --- float memory (only the base register is an integer one) -------
    "lfs": ROLE_FLOAD, "lfd": ROLE_FLOAD,
    "lfsu": ROLE_FLOADU, "lfdu": ROLE_FLOADU,
    "lfsx": ROLE_FLOADIDX, "lfdx": ROLE_FLOADIDX, "lfsux": ROLE_FLOADIDXU,
    "ldx": ROLE_LOADIDX, "lwbrx": ROLE_LOADIDX, "lhzx.": ROLE_LOADIDX,
    "stfs": ROLE_FSTORE, "stfd": ROLE_FSTORE, "stfiwx": ROLE_FSTORE,
    "stfsu": ROLE_FSTOREU, "stfdu": ROLE_FSTOREU,
    "stfsx": ROLE_FSTOREIDX, "stfdx": ROLE_FSTOREIDX,
    "stwbrx": ROLE_STOREIDX, "sthbrx": ROLE_STOREIDX, "lhzx.": ROLE_LOADIDX,
    # --- compares and traps -------------------------------------------
    "cmpw": ROLE_CMP, "cmpd": ROLE_CMP, "cmplw": ROLE_CMP, "cmpld": ROLE_CMP,
    "cmp": ROLE_CMP, "cmpl": ROLE_CMP, "cmpi": ROLE_CMP, "cmpli": ROLE_CMP,
    "cmpwi": ROLE_CMP, "cmplwi": ROLE_CMP, "cmpdi": ROLE_CMP, "cmpldi": ROLE_CMP,
    "tw": ROLE_TRAP, "twi": ROLE_TRAP, "td": ROLE_TRAP, "tdi": ROLE_TRAP,
    "twui": ROLE_TRAP, "twlti": ROLE_TRAP, "twgti": ROLE_TRAP, "twllti": ROLE_TRAP,
    "tweqi": ROLE_TRAP, "twnei": ROLE_TRAP, "tdui": ROLE_TRAP, "tdlti": ROLE_TRAP,
    "tdgti": ROLE_TRAP, "tdlgti": ROLE_TRAP, "tdeqi": ROLE_TRAP, "tdnei": ROLE_TRAP,
    # --- special registers --------------------------------------------
    "mtlr": ROLE_READ0, "mtctr": ROLE_READ0, "mtspr": ROLE_READ0,
    "mtcrf": ROLE_READ0, "mtsrin": ROLE_READ0, "mtmsrd": ROLE_READ0,
    "mtsr": ROLE_READ0,
    # --- integer ALU (destination = first operand) ---------------------
    "add": ROLE_ALU, "addc": ROLE_ALU, "adde": ROLE_ALU, "addme": ROLE_ALU,
    "addze": ROLE_ALU, "addi": ROLE_ALU, "addic": ROLE_ALU, "addis": ROLE_ALU,
    "and": ROLE_ALU, "andc": ROLE_ALU, "andi.": ROLE_ALU, "andis.": ROLE_ALU,
    "cntlzw": ROLE_ALU, "cntlzd": ROLE_ALU, "divw": ROLE_ALU, "divwu": ROLE_ALU,
    "divd": ROLE_ALU, "divdu": ROLE_ALU, "eqv": ROLE_ALU, "extsb": ROLE_ALU,
    "extsh": ROLE_ALU, "extsw": ROLE_ALU, "mulhw": ROLE_ALU, "mulhwu": ROLE_ALU,
    "mullw": ROLE_ALU, "mulld": ROLE_ALU, "mulli": ROLE_ALU, "nand": ROLE_ALU,
    "neg": ROLE_ALU, "nor": ROLE_ALU, "or": ROLE_ALU, "orc": ROLE_ALU,
    "ori": ROLE_ALU, "oris": ROLE_ALU, "rldicl": ROLE_ALU, "rldicr": ROLE_ALU,
    "rldimi": ROLE_ALU, "rldic": ROLE_ALU, "rldcl": ROLE_ALU, "rldcr": ROLE_ALU,
    "rlwinm": ROLE_ALU, "rlwnm": ROLE_ALU, "rlwimi": ROLE_ALU, "sraw": ROLE_ALU,
    "srawi": ROLE_ALU, "srad": ROLE_ALU, "sradi": ROLE_ALU, "slw": ROLE_ALU,
    "srw": ROLE_ALU, "sld": ROLE_ALU, "srd": ROLE_ALU, "sldi": ROLE_ALU,
    "subf": ROLE_ALU, "subfc": ROLE_ALU, "subfe": ROLE_ALU, "subfme": ROLE_ALU,
    "subfze": ROLE_ALU, "subfic": ROLE_ALU, "xor": ROLE_ALU, "xori": ROLE_ALU,
    "xoris": ROLE_ALU, "mr": ROLE_ALU, "not": ROLE_ALU, "li": ROLE_ALU,
    "lis": ROLE_ALU, "rotlwi": ROLE_ALU, "clrlwi": ROLE_ALU, "slwi": ROLE_ALU,
    "srwi": ROLE_ALU, "rotldi": ROLE_ALU, "clrldi": ROLE_ALU, "isel": ROLE_ALU,
    "addpcis": ROLE_ALU, "maddhd": ROLE_ALU, "maddhdu": ROLE_ALU,
    "maddhds": ROLE_ALU, "mflr": ROLE_ALU, "mfctr": ROLE_ALU, "mfcr": ROLE_ALU,
    "mfmsr": ROLE_ALU, "mftb": ROLE_ALU, "mftbu": ROLE_ALU, "mfspr": ROLE_ALU,
    "mfsrin": ROLE_ALU, "mffs": ROLE_ALU, "abs": ROLE_ALU, "nego": ROLE_ALU,
    "tlbie": ROLE_NONE, "eciwx": ROLE_NONE, "dcbz": ROLE_NONE,
    "dcbzl": ROLE_NONE, "dcbf": ROLE_NONE, "dcbst": ROLE_NONE,
    "dcbtst": ROLE_NONE, "dcbt": ROLE_NONE,
    "lvsl": ROLE_NONE, "lvsr": ROLE_NONE,
    "rotlw": ROLE_ALU, "sync": ROLE_NONE, "lwsync": ROLE_NONE,
    "isync": ROLE_NONE, "eieio": ROLE_NONE, "sc": ROLE_NONE, "nop": ROLE_NONE,
    "mtfsf": ROLE_NONE, "mtfsb0": ROLE_NONE, "mtfsb1": ROLE_NONE,
    "tabortdc.": ROLE_NONE,
}


def classify(mnem: str, op_str: str) -> tuple[str, set[int], set[int]]:
    """(role, reads, writes) of one guest instruction.

    `reads`/`writes` are sets of integer register numbers.  The role
    ROLE_UNKNOWN means "not classified" -> the caller must treat the
    analysis as inconclusive.
    """
    role = ROLES.get(mnem)
    if role is None and mnem.endswith(".") and not mnem.startswith("b"):
        role = ROLES.get(mnem[:-1])
    if role is None:
        if mnem.startswith("b"):
            role = ROLE_NONE              # every branch reads only the CR
        elif mnem.startswith("f"):
            role = ROLE_NONE              # scalar float: FPRs only
        elif mnem.startswith("v"):
            role = ROLE_NONE              # AltiVec: VRs only
        elif mnem.startswith("x"):
            # VSX: pure vector forms touch no integer register, the memory
            # forms (lx*/stx*) do -> unknown.
            role = ROLE_NONE if not (mnem.startswith("lx") or mnem.startswith("stx")) \
                else ROLE_UNKNOWN
        elif mnem.startswith("lv"):
            role = ROLE_UNKNOWN            # AltiVec load/store: base register
        else:
            role = ROLE_UNKNOWN
    if role == ROLE_UNKNOWN:
        return role, set(), set()

    tk = _toks(op_str)
    if role == ROLE_NONE:
        return role, set(), set()
    if role in (ROLE_CMP, ROLE_TRAP, ROLE_READ0):
        return role, _ints(tk), set()
    if role == ROLE_ALU:
        d = _dst(tk[0]) if tk else None
        reads = _ints(tk)
        if d is not None:
            reads.discard(d)
        writes = {d} if d is not None else set()
        return role, reads, writes
    # memory forms
    reads: set[int] = set()
    writes: set[int] = set()
    t0 = _dst(tk[0]) if tk else None
    b1 = _base(tk[1]) if len(tk) > 1 else None
    if role in (ROLE_LOAD, ROLE_LOADU):
        if t0 is not None:
            writes.add(t0)
        if b1 is not None:
            reads.add(b1)
        if role == ROLE_LOADU and b1 is not None:
            writes.add(b1)
    elif role in (ROLE_LOADIDX, ROLE_LOADIDXU):
        if t0 is not None:
            writes.add(t0)
        for t in tk[1:3]:
            m = INT_REG_RE.match(t)
            if m:
                reads.add(int(m.group(1)))
        if role == ROLE_LOADIDXU and len(tk) > 1:
            m = INT_REG_RE.match(tk[1])
            if m:
                writes.add(int(m.group(1)))
    elif role in (ROLE_STORE, ROLE_STOREU):
        if t0 is not None:
            reads.add(t0)
        if b1 is not None:
            reads.add(b1)
        if role == ROLE_STOREU and b1 is not None:
            writes.add(b1)
    elif role in (ROLE_STOREIDX, ROLE_STOREIDXU):
        if t0 is not None:
            reads.add(t0)
        for t in tk[1:3]:
            m = INT_REG_RE.match(t)
            if m:
                reads.add(int(m.group(1)))
        if role == ROLE_STOREIDXU and len(tk) > 1:
            m = INT_REG_RE.match(tk[1])
            if m:
                writes.add(int(m.group(1)))
    elif role in (ROLE_FLOAD, ROLE_FLOADU):
        if b1 is not None:
            reads.add(b1)
        if role == ROLE_FLOADU and b1 is not None:
            writes.add(b1)
    elif role == ROLE_FLOADIDX:
        for t in tk[1:3]:
            m = INT_REG_RE.match(t)
            if m:
                reads.add(int(m.group(1)))
    elif role == ROLE_FLOADIDXU:
        for t in tk[1:3]:
            m = INT_REG_RE.match(t)
            if m:
                reads.add(int(m.group(1)))
        if len(tk) > 1:
            m = INT_REG_RE.match(tk[1])
            if m:
                writes.add(int(m.group(1)))
    elif role in (ROLE_FSTORE, ROLE_FSTOREU):
        if b1 is not None:
            reads.add(b1)
        if role == ROLE_FSTOREU and b1 is not None:
            writes.add(b1)
    elif role == ROLE_FSTOREIDX:
        for t in tk[1:3]:
            m = INT_REG_RE.match(t)
            if m:
                reads.add(int(m.group(1)))
    return role, reads, writes


class Insn:
    __slots__ = ("addr", "mnem", "read", "write", "target", "fall", "ok")

    def __init__(self, addr, mnem, read, write, target, fall, ok):
        self.addr = addr
        self.mnem = mnem
        self.read = read
        self.write = write
        self.target = target
        self.fall = fall
        self.ok = ok


def _decode_one(guest: Guest, addr: int) -> Insn | None:
    ins = guest.decode(addr)
    if ins is None:
        return None
    role, reads, writes = classify(ins.mnemonic, ins.op_str)
    m = ins.mnemonic
    tgt = guest.branch_target(ins)
    fall = True
    if m in ("blr", "bctr"):
        fall = False
    elif m == "b":
        fall = False
    elif m.startswith("b"):
        fall = True
    return Insn(addr, m, reads, writes, tgt, fall, role != ROLE_UNKNOWN)


def discover_cfg(guest: Guest, entry: int, limit: int) -> tuple[dict[int, Insn],
                                                             dict[int, list[int]]]:
    """Block graph of the region [entry, limit).

    A block ends at a conditional branch, at an unconditional branch, at
    `blr`/`bctr` or at a word that is not code.  A branch that leaves the
    region is a legitimate exit (a tail call) and does NOT make the
    analysis inconclusive.
    """
    blocks: dict[int, list[Insn]] = {}
    succs: dict[int, list[int]] = {}
    work = [entry]
    while work:
        addr = work.pop()
        if addr in blocks:
            continue
        if addr < entry or addr >= limit:
            continue                       # exit
        cur = addr
        insns: list[Insn] = []
        targets: list[int] = []
        while cur < limit:
            i = _decode_one(guest, cur)
            if i is None:
                break                      # data hole: the block ends here
            insns.append(i)
            m = i.mnem
            if m in ("blr", "bctr"):
                break
            if m == "b":
                if i.target is not None:
                    targets.append(i.target)
                break
            if m.startswith("b"):
                if i.target is not None:
                    targets.append(i.target)
                targets.append(cur + 4)     # the fall-through
                break
            cur += 4
        blocks[addr] = insns
        s = []
        for t in targets:
            if entry <= t < limit and t not in s:
                s.append(t)
                work.append(t)
        succs[addr] = s
    return blocks, succs


def must_defined(blocks: dict[int, list[Insn]], succs: dict[int, list[int]],
                 entry: int) -> dict[int, set[int]]:
    """Forward `must-defined` analysis (path intersection) over the blocks.

    Returns block start -> the set of registers defined on EVERY path that
    reaches that block.
    """
    in_st: dict[int, set[int]] = {}
    work = [(entry, frozenset())]
    while work:
        start, val = work.pop()
        cur = in_st.get(start)
        if cur is None:
            in_st[start] = set(val)
        else:
            new = cur & val
            if new == cur:
                continue
            in_st[start] = new
        out = set(in_st[start])
        for i in blocks.get(start, ()):
            out -= i.write
            out |= i.write
        for s in succs.get(start, ()):
            work.append((s, frozenset(out)))
    return in_st


def region_liveins(guest: Guest, lo: int, hi: int) -> tuple[set[int], bool]:
    """Non-volatile registers READ before being defined inside [lo, hi).

    Returns (registers, conclusive).  `conclusive` is False when the region
    contains an instruction that the classifier does not know.
    """
    blocks, succs = discover_cfg(guest, lo, hi)
    if lo not in blocks:
        return set(), False
    st = must_defined(blocks, succs, lo)
    live: set[int] = set()
    for start, insns in blocks.items():
        defined = set(st.get(start, set()))
        for i in insns:
            if not i.ok:
                return set(), False
            live |= (i.read - defined) & set(NONVOLATILE)
            defined -= i.write
            defined |= i.write
    return live, True


def owner_state_at(guest: Guest, owner: int, limit: int,
                   wanted: list[int]) -> tuple[dict[int, set[int]], bool]:
    """Registers definitely defined by the owner at the given addresses."""
    blocks, succs = discover_cfg(guest, owner, limit)
    if owner not in blocks:
        return {}, False
    st = must_defined(blocks, succs, owner)
    out: dict[int, set[int]] = {}
    conclusive = True
    want = set(wanted)
    for start, insns in blocks.items():
        defined = set(st.get(start, set()))
        for i in insns:
            if not i.ok:
                conclusive = False
            if i.addr in want:
                out[i.addr] = set(defined)
            defined -= i.write
            defined |= i.write
    return out, conclusive


def analyse_liveins(guest: Guest, pdata: Pdata, entries: dict[int, Entry]) -> dict:
    """Decide `share_registers` for every chunk.

    A chunk needs `share_registers = true` when it READS a non-volatile
    register that it does not define itself.  The chunk runs with the
    register state of whoever branched into it, so the codegen must NOT
    localize the non-volatiles: it would make the chunk read a zero.

    The analysis is flow sensitive:
      * the chunk's live-ins are computed over the chunk's own CFG with a
        forward `must-defined` analysis (a register counts as a live-in
        when it is read before being defined on EVERY path);
      * the owner's `must-defined` state at the chunk entry says which
        non-volatiles the owner had already set up ("left live");
      * a chunk is marked only when the analysis is CONCLUSIVE for it.
    """
    chunks = [e for e in entries.values() if e.kind == "chunk"]
    by_owner: dict[int, list[Entry]] = {}
    for e in chunks:
        by_owner.setdefault(e.parent, []).append(e)

    n_marked = 0
    n_need = 0
    n_unknown = 0
    n_no_need = 0
    details: list[dict] = []
    for owner in sorted(by_owner):
        group = sorted(by_owner[owner], key=lambda e: e.va)
        state, owner_ok = owner_state_at(guest, owner, pdata.extent_end(owner),
                                         [e.va for e in group])
        for e in group:
            live, ok = region_liveins(guest, e.va, e.end)
            if not ok or not owner_ok:
                n_unknown += 1
                details.append({"va": f"0x{e.va:08X}", "parent": f"0x{owner:08X}",
                                "verdict": "unknown",
                                "reason": "an instruction of the region is not "
                                          "classified -> analysis inconclusive"})
                continue
            owner_live = state.get(e.va, set()) & set(NONVOLATILE)
            need = sorted(live & owner_live)
            if need:
                n_need += 1
                e.share_registers = True
                n_marked += 1
                details.append({"va": f"0x{e.va:08X}", "parent": f"0x{owner:08X}",
                                "verdict": "mark",
                                "regs": [f"r{r}" for r in need],
                                "reason": "reads the non-volatiles the owner left "
                                          "live before defining them"})
            else:
                n_no_need += 1
    return {
        "chunks": len(chunks),
        "chunks_with_nonvolatile_livein": n_need,
        "marked_share_registers": n_marked,
        "chunks_without_need": n_no_need,
        "chunks_unknown": n_unknown,
        "details": details,
    }


# --------------------------------------------------------------------------
# Seed file reading (for --check)
# --------------------------------------------------------------------------
ENTRY_RX = re.compile(
    r'\[functions\."(0x[0-9A-Fa-f]+)"\]\s*\n'
    r'(?:\s*size\s*=\s*(\d+)\s*\n)?'
    r'(?:\s*end\s*=\s*(\d+)\s*\n)?'
    r'(?:\s*parent\s*=\s*(\d+)\s*\n)?'
    r'(?:\s*share_registers\s*=\s*(true|false)\s*\n)?'
)


def read_seed_file(path: Path) -> dict[int, dict]:
    """Parse the subset of TOML that the seed uses (independent of tomllib)."""
    txt = path.read_text(encoding="utf-8", errors="replace")
    out: dict[int, dict] = {}
    for m in ENTRY_RX.finditer(txt):
        va = int(m.group(1), 16)
        rec = {
            "size": int(m.group(2)) if m.group(2) is not None else None,
            "end": int(m.group(3)) if m.group(3) is not None else None,
            "parent": int(m.group(4)) if m.group(4) is not None else None,
            "share_registers": (m.group(5) == "true") if m.group(5) else None,
        }
        if va in out:
            raise Missing(f"duplicate entry for 0x{va:08X} in {path}")
        out[va] = rec
    if not out:
        raise Missing(f"no [functions.\"0x...\"] entries in {path}")
    return out


# --------------------------------------------------------------------------
# Truncation census of a generated tree
# --------------------------------------------------------------------------
FUNC_RE = re.compile(r"^DEFINE_REX_FUNC\((\w+)\) \{$")
SUB_RE = re.compile(r"^sub_([0-9A-Fa-f]+)$")
INSN_COMMENT_RE = re.compile(r"^\t// (.+)$")
MNEMONIC_RE = re.compile(r"^[a-z][a-z0-9._]*(\s|$)")
TERMINATOR_MNEMONICS = {"b", "blr", "bctr", "bctrl"}
NOT_INSTRUCTION_PREFIXES = (
    "STUB:", "PATCH(", "REXLOG", "TODO", "NOTE", "UNIMPLEMENTED:",
    "DECLARE_", "DEFN_", "include", "END_",
)


class Tree:
    """The emitted body of every function of a generated tree."""

    def __init__(self, root: Path):
        self.root = root
        self.bodies: dict[int, dict] = {}
        self.files = 0
        self.parse()

    def parse(self) -> None:
        for cpp in sorted(self.root.glob("neverdead_recomp.*.cpp")):
            self.files += 1
            lines = cpp.read_text(encoding="utf-8", errors="replace").splitlines()
            starts = []
            for i, line in enumerate(lines):
                m = FUNC_RE.match(line)
                if m:
                    starts.append((i, m.group(1)))
            for k, (i, name) in enumerate(starts):
                end = starts[k + 1][0] if k + 1 < len(starts) else len(lines)
                m = SUB_RE.match(name)
                if not m:
                    continue
                va = int(m.group(1), 16)
                instrs = []
                for line in lines[i:end]:
                    cm = INSN_COMMENT_RE.match(line)
                    if not cm:
                        continue
                    t = cm.group(1)
                    if t.startswith(NOT_INSTRUCTION_PREFIXES):
                        continue
                    if not MNEMONIC_RE.match(t):
                        continue
                    instrs.append(t)
                self.bodies[va] = {
                    "file": cpp.name,
                    "n_insn": len(instrs),
                    "len": 4 * len(instrs),
                    "first": instrs[0] if instrs else None,
                    "last": instrs[-1] if instrs else None,
                }


def census(tree_root: Path, guest: Guest, pdata: Pdata, sweep: Sweep,
           top: int = 20) -> dict:
    """Truncation census of a generated tree against the ground truth.

    Three yardsticks are reported, because they answer different questions:

    * `body < .pdata extent`  - the emitted body is shorter than the real
      extent of the function that owns the address (the "next begin" rule).
      This is the literal "cuerpo < longitud real (según .pdata + extents)".

    * `body < linear walk`    - the emitted body is shorter than the body the
      codegen itself emits for that address when the extent is NOT limited:
      the fall-through path up to the first unconditional terminator
      (`blr` / `bctr` / a `b` that leaves the region / a 4-byte hole).
      The codegen ALWAYS emits at least that path, so a deficit here is a
      PROOF that the body was cut - this is the class the invented `size`
      produced.

    * `reachable`             - the address of the function is contained in
      the extent of ANOTHER function: it is an interior label / shared tail.

    The emitted length of a function is measured from the generated C++
    itself (`4 x` the number of guest instruction comments the codegen
    wrote inside `DEFINE_REX_FUNC(sub_<va>) { ... }`), never from the seed,
    so the measurement is independent of the seed that produced the tree.
    """
    tree = Tree(tree_root)
    rows: list[dict] = []
    n_extent_trunc = 0
    n_walk_trunc = 0
    n_reachable = 0
    n_reachable_trunc = 0
    n_begin = 0
    n_begin_trunc = 0
    n_begin_walk_trunc = 0
    n_zero = 0
    n_truncated = 0
    n_truncated_begin = 0
    n_truncated_reachable = 0
    n_split = 0

    for va, b in tree.bodies.items():
        owner = pdata.owner(va)
        is_begin = pdata.is_begin(va)
        if is_begin:
            n_begin += 1
        real = (pdata.extent_end(owner) - va) if owner is not None else None
        walk = None
        walk_reason = None
        if owner is not None:
            walk_end, walk_reason, _n = walk_block(guest, va, pdata.extent_end(owner))
            walk = walk_end - va
        reachable = owner is not None and not is_begin
        if reachable:
            n_reachable += 1
        if b["n_insn"] == 0:
            n_zero += 1
        trunc_extent = real is not None and b["len"] < real
        trunc_walk = walk is not None and b["len"] < walk
        # The decisive test: a body that was CUT stops in the middle of the
        # fall-through path, so its LAST emitted instruction is not an
        # unconditional terminator.  A body that merely ends EARLY because the
        # function was SPLIT (the tail became another function) still ends
        # with its `b`/`blr`/`bctr` and is not a truncation.
        last = (b["last"] or "").split(" ")[0].split(",")[0]
        ends_with_terminator = last in TERMINATOR_MNEMONICS
        truncated = trunc_walk and not ends_with_terminator
        if trunc_extent:
            n_extent_trunc += 1
            if is_begin:
                n_begin_trunc += 1
            if reachable:
                n_reachable_trunc += 1
        if trunc_walk:
            n_walk_trunc += 1
            if is_begin:
                n_begin_walk_trunc += 1
            if ends_with_terminator:
                n_split += 1
        if truncated:
            n_truncated += 1
            if is_begin:
                n_truncated_begin += 1
            if reachable:
                n_truncated_reachable += 1
        rows.append({
            "va": va,
            "emitted": b["len"],
            "n_insn": b["n_insn"],
            "real_extent": real,
            "walk_len": walk,
            "walk_reason": walk_reason,
            "owner": owner,
            "is_begin": is_begin,
            "reachable": reachable,
            "trunc_extent": trunc_extent,
            "trunc_walk": trunc_walk,
            "ends_with_terminator": ends_with_terminator,
            "truncated": truncated,
            "first": b["first"],
            "last": b["last"],
            "file": b["file"],
        })

    def _row(r: dict) -> dict:
        return {
            "va": f"0x{r['va']:08X}",
            "emitted": r["emitted"],
            "n_insn": r["n_insn"],
            "real_extent": r["real_extent"],
            "deficit": (r["real_extent"] - r["emitted"]) if r["real_extent"] else None,
            "walk_len": r["walk_len"],
            "walk_reason": r["walk_reason"],
            "owner": (f"0x{r['owner']:08X}" if r["owner"] else None),
            "is_pdata_begin": r["is_begin"],
            "reachable": r["reachable"],
            "truncated_vs_extent": r["trunc_extent"],
            "truncated_vs_walk": r["trunc_walk"],
            "ends_with_terminator": r["ends_with_terminator"],
            "TRUNCATED": r["truncated"],
            "first_instr": r["first"],
            "last_instr": r["last"],
            "file": r["file"],
        }

    by_extent = sorted([r for r in rows if r["trunc_extent"]],
                        key=lambda r: r["emitted"] - r["real_extent"])
    by_walk = sorted([r for r in rows if r["trunc_walk"]],
                     key=lambda r: r["emitted"] - r["walk_len"])
    by_trunc = sorted([r for r in rows if r["truncated"]],
                      key=lambda r: r["emitted"] - r["walk_len"])
    return {
        "tree": str(tree_root),
        "files": tree.files,
        "functions": len(tree.bodies),
        "functions_with_zero_instructions": n_zero,
        "functions_at_pdata_begins": n_begin,
        "reachable_functions": n_reachable,
        "body_shorter_than_pdata_extent": n_extent_trunc,
        "of_which_at_a_pdata_begin": n_begin_trunc,
        "of_which_reachable": n_reachable_trunc,
        "body_shorter_than_linear_walk": n_walk_trunc,
        "of_which_at_a_pdata_begin_walk": n_begin_walk_trunc,
        "of_which_ended_with_a_terminator_split_not_cut": n_split,
        "TRUNCATED_BODIES": n_truncated,
        "truncated_at_a_pdata_begin": n_truncated_begin,
        "truncated_reachable": n_truncated_reachable,
        "examples_worst_by_extent_deficit": [_row(r) for r in by_extent[:top]],
        "examples_worst_by_walk_deficit": [_row(r) for r in by_walk[:top]],
        "examples_truncated": [_row(r) for r in by_trunc[:top]],
    }


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def build_context(root: Path, bin_path: Path):
    if Cs is None:
        raise Missing("Capstone is not installed (pip install capstone==5.0.7)")
    guest = Guest(bin_path)
    pdata = Pdata(guest)
    if pdata.n_records != PDATA_LEN // PDATA_REC:
        raise Missing(f"unexpected .pdata record count {pdata.n_records}")
    sweep = Sweep(guest, pdata)
    return guest, pdata, sweep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Build the correct ReXGlue function table from the guest .pdata.")
    ap.add_argument("--project-root", type=Path, default=None)
    ap.add_argument("--bin", type=Path, default=None,
                    help="guest image (default: ../NeverDead_base.exe)")
    ap.add_argument("--out", type=Path, default=None,
                    help="output TOML (default: config/functions.toml)")
    ap.add_argument("--emit", action="store_true", help="write the output TOML")
    ap.add_argument("--check", action="store_true",
                    help="verify the written file against a fresh recomputation")
    ap.add_argument("--census", action="store_true",
                    help="truncation census of a generated tree")
    ap.add_argument("--tree", type=Path, default=None,
                    help="generated tree for --census "
                         "(default: generated/neverdead_default_v2)")
    ap.add_argument("--liveins", action="store_true",
                    help="live-in report (share_registers decision)")
    ap.add_argument("--json", action="store_true", help="machine readable output")
    args = ap.parse_args(argv)

    root = (args.project_root or Path(__file__).resolve().parents[1]).resolve()
    bin_path = args.bin or (root.parent / "NeverDead_base.exe")
    out_path = args.out or (root / "config" / "functions.toml")
    tree_path = args.tree or (root / "generated" / "neverdead_default_v2")

    t0 = time.time()
    try:
        guest, pdata, sweep = build_context(root, bin_path)
        entries = build_entries(guest, pdata, sweep)
    except Missing as exc:
        print(f"build_seed_from_pdata: MISSING PREREQUISITE: {exc}", file=sys.stderr)
        return 2

    summary = {
        "guest_image": str(bin_path),
        "pdata_raw": PDATA_RAW,
        "pdata_len": PDATA_LEN,
        "pdata_record_bytes": PDATA_REC,
        "pdata_records": pdata.n_records,
        "pdata_records_in_text_range": pdata.n_range,
        "pdata_entries_aligned": len(pdata.begins),
        "sweep_instructions": sweep.n_instructions,
        "distinct_branch_targets": len(sweep.sites),
        "entries": len(entries),
        "entries_standalone": sum(1 for e in entries.values() if e.kind == "standalone"),
        "entries_chunk": sum(1 for e in entries.values() if e.kind == "chunk"),
        "entries_gap": sum(1 for e in entries.values() if e.kind == "gap"),
        "out_file": str(out_path),
    }

    # ---- live-ins -------------------------------------------------------
    if args.liveins or args.emit or args.check:
        li = analyse_liveins(guest, pdata, entries)
        summary["liveins"] = {k: v for k, v in li.items() if k != "details"}
        summary["liveins_examples"] = li["details"][:40]
        summary["entries_share_registers"] = sum(1 for e in entries.values()
                                                 if e.share_registers)

    # ---- census ---------------------------------------------------------
    if args.census:
        if not tree_path.is_dir():
            print(f"build_seed_from_pdata: tree not found: {tree_path}", file=sys.stderr)
            return 2
        summary["census"] = census(tree_path, guest, pdata, sweep)

    # ---- emit -----------------------------------------------------------
    if args.emit:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(render_toml(entries), encoding="utf-8")
        summary["out_written"] = True

    # ---- check ----------------------------------------------------------
    if args.check:
        if not out_path.is_file():
            print(f"build_seed_from_pdata: output file not found: {out_path}",
                  file=sys.stderr)
            return 1
        want = render_toml(entries)
        have = out_path.read_text(encoding="utf-8", errors="replace")
        summary["out_matches_recomputation"] = (want == have)
        if want != have:
            wl = want.splitlines()
            hl = have.splitlines()
            diff = []
            for i in range(max(len(wl), len(hl))):
                a = wl[i] if i < len(wl) else "<EOF>"
                b = hl[i] if i < len(hl) else "<EOF>"
                if a != b:
                    diff.append(f"line {i+1}: recomputed={a!r} on_disk={b!r}")
                    if len(diff) >= 20:
                        break
            summary["out_diff"] = diff
    else:
        summary["out_written"] = False

    summary["seconds"] = round(time.time() - t0, 1)

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    else:
        for k in ("pdata_records", "pdata_records_in_text_range",
                  "pdata_entries_aligned", "sweep_instructions",
                  "distinct_branch_targets", "entries", "entries_standalone",
                  "entries_chunk", "entries_gap", "entries_share_registers",
                  "out_written", "out_matches_recomputation", "seconds"):
            if k in summary:
                print(f"  {k:<32} {summary[k]}")
        if "liveins" in summary:
            print("  liveins: " + json.dumps(summary["liveins"], sort_keys=True))
        if "census" in summary:
            c = summary["census"]
            print(f"  census.tree                          {c['tree']}")
            print(f"  census.functions                     {c['functions']}")
            print(f"  census.body_shorter_than_pdata_extent {c['body_shorter_than_pdata_extent']}")
            print(f"  census.body_shorter_than_linear_walk {c['body_shorter_than_linear_walk']}")
            print(f"  census.reachable_functions           {c['reachable_functions']}")
            print(f"  census.reachable_and_shorter_extent  {c['reachable_and_shorter_than_extent']}")

    if args.check and not summary.get("out_matches_recomputation", False):
        for d in summary.get("out_diff", [])[:20]:
            print("  DIFF " + d)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
