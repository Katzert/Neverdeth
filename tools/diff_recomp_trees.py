#!/usr/bin/env python3
"""
tools/diff_recomp_trees.py - structural diff between two recompiled trees.

WHAT IS MEASURED
----------------
For every guest function address `va` the tool reports how many *guest bytes*
of body each side actually contains:

    len_X(va) = 4 * (number of guest instruction comments emitted inside
                     `DEFINE_REX_FUNC(sub_<va>) { ... }` of side X)

The guest instruction comments are exactly the `// <mnemonic> <operands>` lines
the ReXGlue codegen writes above every translated guest instruction, so the
count is the number of guest instructions the codegen emitted for that
function.  The measurement is taken from the generated C++ itself, never from
the seed, so it is independent of the seed that produced the tree.

The *real* length of a guest body comes from the ground truth (`.pdata` +
Capstone over the flat guest image, see tools/build_seed_from_pdata.py).

TRUNCATION TAXONOMY (reported separately, never mixed)
------------------------------------------------------
`truncada_X` (the headline aggregate) is the **false-cut class**: the seed
entries of the ORIGINAL seed that the corrected seed eliminated (case B:
interior labels of a real `.pdata` function with no external tail-call site).
In v1 each of them exists as a separate function whose body is a fragment of
the enclosing real function; in v2 they must not exist as separate functions.

`cuerpo_incompleto_X` is the broader metric: a *configured* function whose
emitted body is shorter than the real body length.

MODES
-----
1. tree mode (the contract):
       python tools/diff_recomp_trees.py --v1 <tree1> --v2 <tree2> [--json]
   both sides are generated trees; everything is observed.

2. ground-truth mode (used when the codegen cannot produce the second tree):
       python tools/diff_recomp_trees.py --v1 <tree1> --v2-seed <seed.toml> [--json]
   the v2 side is *modelled* from the corrected seed exactly the way the
   codegen resolves it:
       'Analyze: 0x{:08X} is CONFIG, using declared size={}'   (seed entry)
       'Analyze: 0x{:08X} using PDATA size={}'                 (pdata begin)
   The v2 column is then a MODEL, not an observation, and the report says so.

Capstone (the only decoder used) provides the instruction text; the guest
image is read with the FLAT mapping `file_offset = guest_va - 0x82000000`.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import re
import struct
import sys
from pathlib import Path

try:
    from capstone import CS_ARCH_PPC, CS_MODE_64, CS_MODE_BIG_ENDIAN, Cs
except ImportError:  # pragma: no cover
    Cs = None

# --------------------------------------------------------------------------
# ground truth constants (flat guest image)
# --------------------------------------------------------------------------
GUEST_BASE = 0x82000000
PDATA_RAW = 0x16F800
PDATA_LEN = 0x238D0
PDATA_LO = 0x821A0000
PDATA_HI = 0x827C05E4

# --------------------------------------------------------------------------
# parsing helpers
# --------------------------------------------------------------------------
FUNC_RE = re.compile(r"^DEFINE_REX_FUNC\((\w+)\) \{$", re.M)
SUB_RE = re.compile(r"^sub_([0-9A-Fa-f]+)$")
INSN_COMMENT_RE = re.compile(r"^\t// (.+)$")
SEED_RE = re.compile(r'\[functions\."(0x[0-9A-Fa-f]+)"\]\s*\n\s*size\s*=\s*(\d+)')

MNEMONIC_RE = re.compile(r"^[a-z][a-z0-9._]*(\s|$)")
NOT_INSTRUCTION_PREFIXES = (
    "STUB:", "PATCH(", "REXLOG", "TODO", "NOTE", "UNIMPLEMENTED:",
    "DECLARE_", "DEFN_", "include", "END_",
)

# The BD family and the 648-byte block the brief asks for explicitly.
BD_FAMILY = [
    0x8278BD34, 0x8278BD38, 0x8278BD40, 0x8278BD44, 0x8278BD48, 0x8278BD4C,
    0x8278BD60, 0x8278BD78, 0x8278BD88, 0x8278BD9C, 0x8278BD98, 0x8278BBE8,
]
BD60_INVITEE = [
    ("mr r3,r29", 0x8278BD60),
    ("bl 0x826d0798", 0x8278BD64),
    ("mr r3,r31", 0x8278BD68),
    ("addi r1,r1,0x70", 0x8278BD6C),
    ("b 0x8278ad9c", 0x8278BD70),
]
BIG_BLOCK_START = 0x823C4BB8
BIG_BLOCK_END = 0x823C4E40


# --------------------------------------------------------------------------
# guest image
# --------------------------------------------------------------------------
class Guest:
    def __init__(self, path: Path):
        self.path = path
        self.data = path.read_bytes()
        self.md = Cs(CS_ARCH_PPC, CS_MODE_BIG_ENDIAN | CS_MODE_64)
        self.md.skipdata = True

    def off(self, va: int) -> int:
        return va - GUEST_BASE

    def decode(self, va: int, count: int = 1):
        o = self.off(va)
        if o < 0 or o + 4 > len(self.data):
            return []
        return list(self.md.disasm(self.data[o:o + 4 * count], va, count=count))

    def text(self, va: int, count: int = 1) -> list[str]:
        return [f"{i.mnemonic} {i.op_str}".strip() for i in self.decode(va, count)]


class Pdata:
    def __init__(self, extents):
        self.extents = extents
        self.begins = [e[0] for e in extents]
        self.ends = [e[2] for e in extents]

    def containing(self, va: int):
        i = bisect.bisect_right(self.begins, va) - 1
        if i >= 0 and self.begins[i] <= va < self.ends[i]:
            return self.extents[i]
        return None

    def next_begin_after(self, va: int):
        i = bisect.bisect_right(self.begins, va)
        return self.begins[i] if i < len(self.extents) else None


def pdata_extents(guest: Guest) -> list[tuple[int, int, int]]:
    out = []
    for i in range(PDATA_LEN // 12):
        b, u, e = struct.unpack_from(">III", guest.data, PDATA_RAW + i * 12)
        if PDATA_LO <= b < PDATA_HI and PDATA_LO <= e <= PDATA_HI:
            out.append((b, u, e))
    out.sort()
    return out


# --------------------------------------------------------------------------
# what the codegen emits for a configured body
# --------------------------------------------------------------------------
def codegen_walk(guest: Guest, pd: Pdata, upper_hints: list[int],
                 va: int, declared: int) -> list[str]:
    """Reproduce the codegen's body walk for a configured function.

    The codegen emits guest instructions from `va` until
      * the DECLARED size is exhausted, or
      * an unconditional terminator is hit:
            blr / bctr            -> terminate (the instruction IS emitted)
            b <target outside>    -> terminate (the instruction IS emitted)
            zero padding (4x00)   -> terminate (the hole IS part of the body)
    Conditional branches NEVER terminate the body.

    Returns the list of emitted instruction texts.
    """
    upper_candidates = []
    nb = pd.next_begin_after(va)
    if nb is not None:
        upper_candidates.append(nb)
    for h in upper_hints:
        if h > va:
            upper_candidates.append(h)
    upper = min(upper_candidates) if upper_candidates else None

    out: list[str] = []
    cur = va
    end = va + declared
    has_call = False
    while cur < end:
        o = guest.off(cur)
        if o < 0 or o + 4 > len(guest.data):
            break
        word = guest.data[o:o + 4]
        if word == b"\x00\x00\x00\x00":          # zero padding -> terminator
            break
        ins = guest.decode(cur, 1)
        if not ins:
            break
        i = ins[0]
        mnem = i.mnemonic
        out.append(f"{mnem} {i.op_str}".strip())
        if mnem == "bl":
            has_call = True
        if mnem in ("blr", "bctr"):
            break
        if mnem == "b":
            tok = i.op_str.split(",")[0].strip()
            tgt = None
            if tok.startswith("0x"):
                try:
                    tgt = int(tok, 16)
                except ValueError:
                    tgt = None
            outside = tgt is None or tgt < va or (upper is not None and tgt >= upper)
            if outside:
                break
        cur += 4
    return out


# --------------------------------------------------------------------------
# tree parsing
# --------------------------------------------------------------------------
class Tree:
    def __init__(self, root: Path):
        self.root = root
        self.bodies: dict[int, dict] = {}
        self.files = 0
        self.functions = 0
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
            for idx, (i, name) in enumerate(starts):
                end = starts[idx + 1][0] if idx + 1 < len(starts) else len(lines)
                body = lines[i:end]
                m = SUB_RE.match(name)
                if not m:
                    continue
                va = int(m.group(1), 16)
                instrs = []
                for line in body:
                    cm = INSN_COMMENT_RE.match(line)
                    if not cm:
                        continue
                    t = cm.group(1)
                    if t.startswith(NOT_INSTRUCTION_PREFIXES):
                        continue
                    if not MNEMONIC_RE.match(t):
                        continue
                    instrs.append(t)
                self.functions += 1
                self.bodies[va] = {
                    "file": cpp.name,
                    "n_insn": len(instrs),
                    "len": 4 * len(instrs),
                    "first": instrs[0] if instrs else None,
                    "last": instrs[-1] if instrs else None,
                    "instrs": instrs,
                }


def read_seed(path: Path) -> dict[int, int]:
    txt = path.read_text(encoding="utf-8", errors="replace")
    out = {}
    for hx, size in SEED_RE.findall(txt):
        out[int(hx, 16)] = int(size)
    if not out:
        raise SystemExit(f"seed file has no [functions.\"0x...\"] entries: {path}")
    return out


def _norm(text: str) -> str:
    """Normalise a Capstone rendering: lower case, no space after commas."""
    t = (text or "").strip().lower()
    t = re.sub(r"\s*,\s*", ",", t)
    t = re.sub(r"\s+", " ", t)
    return t


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------
def build_report(v1: Tree, v2_tree: Tree | None, v2_seed: dict[int, int] | None,
                 v1_seed: dict[int, int], guest: Guest, pd: Pdata,
                 seed_v2_class: dict[int, int] | None = None) -> dict:
    seed_v2 = v2_seed if v2_seed is not None else (seed_v2_class or {})
    upper_hints = sorted(seed_v2)

    # ---- the real body length of every address -------------------------
    def real_len_of(va: int) -> tuple[int, str]:
        own = pd.containing(va)
        if own is not None and own[0] == va:
            return own[2] - own[0], "pdata"
        if va in seed_v2:
            return seed_v2[va], "seed"
        if va in v1_seed:
            return v1_seed[va], "seed_v1"
        return 0, "unknown"

    # ---- universe ------------------------------------------------------
    universe = set(v1.bodies)
    universe |= set(v2_seed or {})
    universe |= set(v2_tree.bodies if v2_tree else ())
    universe |= set(v1_seed)
    universe |= set(BD_FAMILY) | {BIG_BLOCK_START}

    rows = []
    for va in sorted(universe):
        b1 = v1.bodies.get(va)
        len1 = b1["len"] if b1 else 0
        real, real_src = real_len_of(va)

        if v2_tree is not None:
            b2 = v2_tree.bodies.get(va)
            len2 = b2["len"] if b2 else 0
            len2_known = True
            len2_src = "observed"
            first2 = b2["first"] if b2 else None
            last2 = b2["last"] if b2 else None
            instr2 = b2["instrs"] if b2 else []
        else:
            # model the codegen's own resolution
            if va in seed_v2:
                len2 = seed_v2[va]
                len2_known = True
                len2_src = "seed-declared"
            else:
                own = pd.containing(va)
                if own is not None and own[0] == va:
                    len2 = own[2] - own[0]
                    len2_known = True
                    len2_src = "pdata-extent"
                else:
                    len2 = 0
                    len2_known = False
                    len2_src = "unknown"
            first2 = None
            last2 = None
            instr2 = []

        gt_first = guest.text(va, 1)
        gt_first = gt_first[0] if gt_first else None
        gt_last = guest.text(va + max(real, 4) - 4, 1) if real else []
        gt_last = gt_last[0] if gt_last else None

        rows.append({
            "va": f"0x{va:08X}",
            "va_int": va,
            "len_v1": len1,
            "len_v2": len2,
            "len_v2_known": len2_known,
            "len_v2_source": len2_src,
            "delta": len2 - len1,
            "real_len": real,
            "real_source": real_src,
            "in_seed_v1": va in v1_seed,
            "in_seed_v2": va in seed_v2,
            "present_v1": b1 is not None,
            "present_v2": (va in v2_tree.bodies) if v2_tree is not None
                else (va in seed_v2),
            "n_insn_v1": b1["n_insn"] if b1 else 0,
            "n_insn_v2": (len2 // 4) if len2 else 0,
            "truncada_en_v1": bool(real and len1 < real),
            "truncada_en_v2": bool(real and len2_known and len2 < real),
            # the "incomplete body" metric is evaluated over the SAME universe on
            # both sides (the addresses the corrected seed configures), so that
            # v1 and v2 are directly comparable.
            "cuerpo_incompleto_v1": bool(real and va in seed_v2 and len1 < real),
            "cuerpo_incompleto_v2": bool(real and va in seed_v2 and len2_known and len2 < real),
            "primera_instr_v1": b1["first"] if b1 else None,
            "ultima_instr_v1": b1["last"] if b1 else None,
            "primera_instr_v2": first2,
            "ultima_instr_v2": last2,
            "primera_instr_ground_truth": gt_first,
            "ultima_instr_ground_truth": gt_last,
            "file_v1": b1["file"] if b1 else None,
        })

    byva = {r["va_int"]: r for r in rows}

    # ---- the false-cut class (the 405) ----------------------------------
    removed = sorted(set(v1_seed) - set(seed_v2))
    removed_rows = []
    for va in removed:
        b1 = v1.bodies.get(va)
        own = pd.containing(va)
        removed_rows.append({
            "va": f"0x{va:08X}",
            "va_int": va,
            "len_v1": b1["len"] if b1 else 0,
            "n_insn_v1": b1["n_insn"] if b1 else 0,
            "len_v2": 0,
            "real_len_enclosing_pdata": (own[2] - own[0]) if own else 0,
            "primera_instr_v1": b1["first"] if b1 else None,
            "ultima_instr_v1": b1["last"] if b1 else None,
        })

    # ---- the codegen walk for the BD family and the big block ----------
    def walk_of(va: int) -> list[str]:
        r = byva.get(va)
        if not r:
            return []
        declared = r["len_v2"] if r["len_v2_known"] else 0
        if not declared:
            return []
        return codegen_walk(guest, pd, upper_hints, va, declared)

    focus = {}
    for va in BD_FAMILY + [BIG_BLOCK_START]:
        r = byva.get(va)
        if not r:
            continue
        focus[f"0x{va:08X}"] = {
            "va_int": va,
            "len_v1": r["len_v1"],
            "n_insn_v1": r["n_insn_v1"],
            "len_v2": r["len_v2"],
            "len_v2_source": r["len_v2_source"],
            "n_insn_v2_modelled": len(walk_of(va)),
            "block_v2_capstone": walk_of(va),
            "block_v1_emitted": v1.bodies[va]["instrs"] if va in v1.bodies else [],
            "truncada_en_v1": r["truncada_en_v1"],
            "truncada_en_v2": r["truncada_en_v2"],
        }

    n_trunc_v1 = sum(1 for r in rows
                      if r["va_int"] in set(removed) and r["present_v1"])
    n_trunc_v2 = sum(1 for r in rows
                      if r["va_int"] in set(removed) and r["present_v2"])

    n_inc_v1 = sum(1 for r in rows if r["cuerpo_incompleto_v1"])
    n_inc_v2 = sum(1 for r in rows if r["cuerpo_incompleto_v2"])

    return {
        "mode": "tree" if v2_tree is not None else "ground-truth",
        "v1_tree": str(v1.root),
        "v2_tree": str(v2_tree.root) if v2_tree is not None else None,
        "v2_seed_entries": len(seed_v2),
        "guest_image": str(guest.path),
        "guest_mapping": "file_offset = guest_va - 0x82000000 (flat image)",
        "n_funciones_v1": v1.functions,
        "n_funciones_v2": (v2_tree.functions if v2_tree is not None else len(seed_v2)),
        "n_truncadas_v1": n_trunc_v1,
        "n_truncadas_v2": n_trunc_v2,
        "n_cuerpos_incompletos_v1": n_inc_v1,
        "n_cuerpos_incompletos_v2": n_inc_v2,
        "n_removed_405": len(removed_rows),
        "focus": focus,
        "rows": rows,
        "removed_405": removed_rows,
    }


# --------------------------------------------------------------------------
# printing
# --------------------------------------------------------------------------
def print_report(rep: dict, full: bool = False) -> None:
    print("=" * 112)
    print(f"diff_recomp_trees   mode = {rep['mode']}")
    print(f"  v1 tree : {rep['v1_tree']}")
    if rep["mode"] == "tree":
        print(f"  v2 tree : {rep['v2_tree']}   (OBSERVED)")
    else:
        print(f"  v2 side : MODELLED from the corrected seed "
              f"({rep['v2_seed_entries']} entries) + .pdata   (NOT observed)")
    print(f"  guest   : {rep['guest_image']}  ({rep['guest_mapping']})")
    print("=" * 112)
    print(f"n_funciones_v1            = {rep['n_funciones_v1']}")
    print(f"n_funciones_v2            = {rep['n_funciones_v2']}")
    print(f"n_truncadas_v1            = {rep['n_truncadas_v1']}   "
          f"(false-cut class: seed entries removed by the corrected seed that still "
          f"exist as a function in v1)")
    print(f"n_truncadas_v2            = {rep['n_truncadas_v2']}   "
          f"(same class in v2"
          + ("  [BY CONSTRUCTION: the corrected seed does not configure them]"
             if rep["mode"] != "tree" else "  [OBSERVED]") + ")")
    print(f"n_cuerpos_incompletos_v1  = {rep['n_cuerpos_incompletos_v1']}   "
          f"(configured entries whose v1 body is shorter than the real body)")
    print(f"n_cuerpos_incompletos_v2  = {rep['n_cuerpos_incompletos_v2']}")

    # ---- the 405 --------------------------------------------------------
    rows = rep["removed_405"]
    if rows:
        lens = sorted(r["len_v1"] for r in rows)
        hist = {}
        for l in lens:
            hist[l] = hist.get(l, 0) + 1
        print("")
        print(f"--- the {len(rows)} false cuts (case B, removed by the corrected seed) ---")
        print(f"  len_v1 histogram : " + ", ".join(f"{v}B x{c}" for v, c in sorted(hist.items())))
        print(f"  len_v1 min={lens[0]} max={lens[-1]} sum={sum(lens)}")
        encl = [r["real_len_enclosing_pdata"] for r in rows]
        print(f"  real length of the enclosing .pdata function: "
              f"min={min(encl)} max={max(encl)} sum={sum(encl)}")
        print("")
        print(f"  {'va':<12} {'len_v1':>7} {'instr':>6} {'real(.pdata)':>13}  "
              f"{'primera':<26} {'ultima':<22}")
        for r in rows[:20]:
            print(f"  {r['va']:<12} {r['len_v1']:>7} {r['n_insn_v1']:>6} "
                  f"{r['real_len_enclosing_pdata']:>13}  "
                  f"{(r['primera_instr_v1'] or '-'):<26} {(r['ultima_instr_v1'] or '-'):<22}")
        if len(rows) > 20:
            print(f"  ... ({len(rows) - 20} more in the JSON report)")

    # ---- focus ----------------------------------------------------------
    print("")
    print("--- focus: BD family + the 648 B block ---")
    print(f"  {'va':<12} {'len_v1':>7} {'n_v1':>5} {'len_v2':>7} {'src_v2':<16} "
          f"{'n_v2*':>5} {'tr_v1':>6} {'tr_v2':>6}")
    for key, f in rep["focus"].items():
        print(f"  {key:<12} {f['len_v1']:>7} {f['n_insn_v1']:>5} {f['len_v2']:>7} "
              f"{f['len_v2_source']:<16} {f['n_insn_v2_modelled']:>5} "
              f"{str(f['truncada_en_v1']):>6} {str(f['truncada_en_v2']):>6}")
    print("  n_v2* = walk model (valid for CONFIG entries: seed-declared ones); "
          "for .pdata functions it is only the declared boundary.")

    # ---- the noreturn tail ---------------------------------------------
    f60 = rep["focus"].get("0x8278BD60")
    if f60:
        print("")
        print("--- sub_8278BD60: the 5 instructions of the invited noreturn tail ---")
        print(f"  v1: {f60['len_v1']} B / {f60['n_insn_v1']} instr   "
              f"v2: {f60['len_v2']} B / {f60['n_insn_v2_modelled']} instr modelled")
        block = f60["block_v2_capstone"]
        all_ok = True
        for (want, va), got in zip(BD60_INVITEE, block):
            ok = _norm(want) == _norm(got)
            all_ok &= ok
            print(f"    0x{va:08X}: want '{want}'  got '{got}'   {'OK' if ok else 'MISMATCH'}")
        if len(block) < 5:
            all_ok = False
            print(f"    !! the modelled v2 body has only {len(block)} instructions")
        print(f"  => the 5 invited instructions are present in the v2 body: "
              f"{'YES' if all_ok else 'NO'}")

    # ---- the 648 B block ------------------------------------------------
    fb = rep["focus"].get(f"0x{BIG_BLOCK_START:08X}")
    if fb:
        want = BIG_BLOCK_END - BIG_BLOCK_START
        print("")
        print(f"--- the {want} B block 0x{BIG_BLOCK_START:08X}..0x{BIG_BLOCK_END:08X} ---")
        print(f"  len_v1={fb['len_v1']} B ({fb['n_insn_v1']} instr)   "
              f"len_v2={fb['len_v2']} B (src={fb['len_v2_source']})")
        print(f"  complete in v2 (boundary >= {want} B): {fb['len_v2'] >= want}")
        print("  NOTE: len_v2 is the BOUNDARY the codegen will declare (the .pdata")
        print("        extent).  The number of guest instructions it will emit for")
        print("        that function's CFG is NOT DETERMINED without running the")
        print("        codegen (the walk model below only applies to CONFIG entries,")
        print("        i.e. seed-declared ones).")

    # ---- the seed universe table ----------------------------------------
    print("")
    print("--- per function: the seed universe (1206 entries of the original seed) ---")
    print(f"  {'va':<12} {'len_v1':>7} {'len_v2':>7} {'delta':>7} {'real':>7} "
          f"{'tr_v1':>6} {'tr_v2':>6} {'seed_v2':>8}  "
          f"{'primera_instr_ground_truth':<30} {'ultima_instr_ground_truth':<22}")
    universe = [r for r in rep["rows"] if r["in_seed_v1"] or r["in_seed_v2"]]
    for r in universe:
        print(f"  {r['va']:<12} {r['len_v1']:>7} {r['len_v2']:>7} {r['delta']:>7} "
              f"{r['real_len']:>7} {str(r['truncada_en_v1']):>6} "
              f"{str(r['truncada_en_v2']):>6} {str(r['in_seed_v2']):>8}  "
              f"{(r['primera_instr_ground_truth'] or '-'):<30} "
              f"{(r['ultima_instr_ground_truth'] or '-'):<22}")
    if full:
        print("")
        print("--- per function: ALL rows ---")
        for r in rep["rows"]:
            print(f"  {r['va']:<12} {r['len_v1']:>7} {r['len_v2']:>7} {r['delta']:>7} "
                  f"{r['real_len']:>7} {str(r['truncada_en_v1']):>6} "
                  f"{str(r['truncada_en_v2']):>6}")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Structural diff between two recompiled trees.")
    ap.add_argument("--v1", required=True, help="v1 generated tree (directory)")
    ap.add_argument("--v2", help="v2 generated tree (directory)")
    ap.add_argument("--v2-seed", help="corrected seed (ground-truth mode)")
    ap.add_argument("--guest", default=None, help="guest image (default: ../NeverDead_base.exe)")
    ap.add_argument("--json", action="store_true", help="machine readable output")
    ap.add_argument("--full", action="store_true", help="print every row")
    args = ap.parse_args(argv)

    if Cs is None:
        print("diff_recomp_trees: Capstone is not installed (pip install capstone==5.0.7)",
              file=sys.stderr)
        return 2
    if not args.v2 and not args.v2_seed:
        print("diff_recomp_trees: give --v2 <tree> or --v2-seed <toml>", file=sys.stderr)
        return 2

    root = Path(__file__).resolve().parents[1]

    def resolve(p: str) -> Path:
        q = Path(p)
        return q if q.is_absolute() else (root / q).resolve()

    v1_root = resolve(args.v1)
    if not v1_root.is_dir():
        print(f"diff_recomp_trees: v1 tree not found: {v1_root}", file=sys.stderr)
        return 2

    guest_path = Path(args.guest) if args.guest else (root.parent / "NeverDead_base.exe")
    if not guest_path.is_file():
        print(f"diff_recomp_trees: guest image not found: {guest_path}", file=sys.stderr)
        return 2

    seed_v1_path = root / "nd_seed_overrides.toml"
    v1_seed = read_seed(seed_v1_path) if seed_v1_path.is_file() else {}
    # the false-cut class is a property of the TWO SEEDS, not of the trees:
    # it is always the entries of the original seed that the corrected seed drops.
    seed_v2_path = root / "nd_seed_overrides_v2.toml"
    seed_v2_class = read_seed(seed_v2_path) if seed_v2_path.is_file() else {}

    guest = Guest(guest_path)
    pd = Pdata(pdata_extents(guest))

    v1 = Tree(v1_root)
    v2_tree = None
    v2_seed = None
    if args.v2:
        v2_root = resolve(args.v2)
        if not v2_root.is_dir():
            print(f"diff_recomp_trees: v2 tree not found: {v2_root}", file=sys.stderr)
            return 2
        v2_tree = Tree(v2_root)
    if args.v2_seed:
        v2_seed = read_seed(resolve(args.v2_seed))

    rep = build_report(v1, v2_tree, v2_seed, v1_seed, guest, pd, seed_v2_class)
    rep["sha256_v1_tree"] = _tree_hash(v1_root)

    if args.json:
        out = dict(rep)
        if not args.full:
            # keep the JSON usable: the full 32k-row table is only emitted with --full
            out["rows"] = [r for r in rep["rows"] if r["in_seed_v1"] or r["in_seed_v2"]]
            out["rows_omitted"] = len(rep["rows"]) - len(out["rows"])
            out["rows_note"] = ("only the seed universe is listed; use --full for every "
                                "function of the trees")
        print(json.dumps(out, indent=2, sort_keys=True))
    else:
        print_report(rep, full=args.full)
    return 0


def _tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted((q for q in root.rglob("*") if q.is_file()), key=lambda q: q.as_posix()):
        h.update(p.relative_to(root).as_posix().encode("utf-8"))
        h.update(b"\0")
        h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest().upper()


if __name__ == "__main__":
    sys.exit(main())
