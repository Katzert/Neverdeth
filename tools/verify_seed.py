#!/usr/bin/env python3
"""
tools/verify_seed.py - STRICT, INDEPENDENT verifier of a ReXGlue function table.

This tool does NOT import `build_seed_from_pdata.py`: it re-reads the guest
bytes and re-derives the expected extents with its own decoder and its own
`.pdata` parser, so a bug in the builder cannot hide behind the same bug in
the checker.

WHAT IS CHECKED (every failure makes the tool exit 1)
-----------------------------------------------------
 1. SCHEMA
      * the file parses as TOML;
      * every table key is one of `size`, `end`, `name`, `parent`,
        `share_registers`;
      * no entry declares `size` AND `end` at the same time;
      * `end` is strictly greater than the address of its entry;
      * `size` (when present) is positive and a multiple of 4.

 2. GROUND TRUTH
      * the guest image is read with the FLAT mapping
        `file_offset = guest_va - 0x82000000`;
      * `.pdata` is parsed as 8-byte big-endian `{va, flags}` records at raw
        0x16F800 (length 0x238D0) - NOT as 12-byte records;
      * the extent of a function is the NEXT `.pdata` begin (exclusive).

 3. STANDALONE ENTRIES (`parent` absent)
      * the address must be a real `.pdata` begin inside `.text`;
      * the declared extent must be EXACTLY the real `.pdata` extent.
        A SHORTER extent is a TRUNCATED BODY (the bug this project exists to
        remove) and is a hard failure - the older verifier accepted it.

 4. CHUNK ENTRIES (`parent` != 0)
      * the parent must be a real `.pdata` begin;
      * the chunk must lie inside the parent's extent;
      * the declared extent must be EXACTLY the real end of the block that
        starts at the chunk address (walked with the codegen's own terminator
        rules).  A shorter extent is a TRUNCATED BODY and a hard failure.

 5. GAP SEEDS (an entry without any key)
      * the address must NOT be a `.pdata` begin and must NOT be inside any
        `.pdata` extent;
      * the address must be the target of a direct `b`/`bl`.

 6. OVERLAPS
      * zero overlaps between STANDALONE entries;
      * a chunk may only overlap the standalone entry of ITS OWN parent;
      * chunk-vs-chunk overlaps are reported but NOT failed: the ReXGlue
        codegen exempts chunks from the boundary-overlap test and two chunks
        of the same parent report the same parent, so the parent lookup
        stays unambiguous.

 7. `share_registers`
      * it is only meaningful on a chunk (a standalone one is a warning);
      * a chunk that carries the flag MUST reference at least one register of
        r14..r31 in its own body - otherwise the flag is unjustified and the
        entry is a hard failure (a wrongly flagged funclet makes the caller
        observe a zeroed register).

Exit codes:
    0  every entry is justified by the guest bytes
    1  at least one entry is not justified
    2  missing prerequisite (guest image / Capstone / table file)

Usage:
    python tools/verify_seed.py                          # config/functions.toml
    python tools/verify_seed.py --seed <toml> [--json] [--verbose]
"""

from __future__ import annotations

import argparse
import bisect
import json
import re
import struct
import sys
import tomllib
from pathlib import Path

try:
    from capstone import CS_ARCH_PPC, CS_MODE_64, CS_MODE_BIG_ENDIAN, Cs
except ImportError:  # pragma: no cover
    Cs = None  # type: ignore[assignment]


# --------------------------------------------------------------------------
# Ground truth constants (flat guest image)
# --------------------------------------------------------------------------
GUEST_BASE = 0x82000000
PDATA_RAW = 0x16F800
PDATA_LEN = 0x238D0
PDATA_REC = 8
TEXT_LO = 0x821B2200
TEXT_HI = 0x827C03E4
NONVOLATILE = range(14, 32)

ALLOWED_KEYS = {"size", "end", "name", "parent", "share_registers"}


class Missing(Exception):
    """A prerequisite is missing."""


# --------------------------------------------------------------------------
# guest image
# --------------------------------------------------------------------------
class Guest:
    def __init__(self, path: Path):
        if not path.is_file():
            raise Missing(f"guest image not found: {path}")
        self.path = path
        self.data = path.read_bytes()
        self.md = Cs(CS_ARCH_PPC, CS_MODE_BIG_ENDIAN | CS_MODE_64)
        self.md.skipdata = True

    def off(self, va: int) -> int:
        return va - GUEST_BASE

    def decode(self, va: int):
        o = self.off(va)
        if o < 0 or o + 4 > len(self.data):
            return None
        for ins in self.md.disasm(self.data[o:o + 4], va, count=1):
            if ins.mnemonic == ".byte":
                return None
            return ins
        return None

    def target(self, ins) -> int | None:
        if not ins.mnemonic.startswith("b"):
            return None
        tok = ins.op_str.split(",")[0].strip()
        if not tok.startswith("0x"):
            return None
        try:
            return int(tok, 16)
        except ValueError:
            return None


# --------------------------------------------------------------------------
# .pdata (8-byte records)
# --------------------------------------------------------------------------
class Pdata:
    def __init__(self, guest: Guest):
        raw = guest.data[PDATA_RAW:PDATA_RAW + PDATA_LEN]
        n = len(raw) // PDATA_REC
        self.n_records = n
        recs = [struct.unpack_from(">II", raw, i * PDATA_REC) for i in range(n)]
        self.begins = sorted(va for va, _f in recs
                             if TEXT_LO <= va < TEXT_HI and va % 4 == 0)
        self.begin_set = set(self.begins)

    def extent_end(self, begin: int) -> int:
        i = bisect.bisect_left(self.begins, begin)
        if i < 0 or i + 1 >= len(self.begins):
            return TEXT_HI
        return self.begins[i + 1]

    def owner(self, va: int) -> int | None:
        i = bisect.bisect_right(self.begins, va) - 1
        if i < 0:
            return None
        b = self.begins[i]
        if b <= va < self.extent_end(b):
            return b
        return None

    def is_begin(self, va: int) -> bool:
        return va in self.begin_set


# --------------------------------------------------------------------------
# block walk (the codegen's own terminator rules)
# --------------------------------------------------------------------------
def walk_block(guest: Guest, va: int, upper: int) -> int:
    """Exclusive end of the block that starts at `va`, bounded by `upper`.

    Terminators: `blr`, `bctr`, an unconditional `b` that LEAVES the region
    and a 4-byte hole.  A conditional branch never ends a block.
    """
    cur = va
    while True:
        if cur >= upper:
            return cur
        ins = guest.decode(cur)
        if ins is None:
            return cur + 4
        m = ins.mnemonic
        if m in ("blr", "bctr"):
            return cur + 4
        if m == "b":
            t = guest.target(ins)
            if t is None or t < va or t >= upper:
                return cur + 4
        cur += 4


# --------------------------------------------------------------------------
# branch targets
# --------------------------------------------------------------------------
def branch_targets(guest: Guest) -> dict[int, list[tuple[int, str]]]:
    out: dict[int, list[tuple[int, str]]] = {}
    blob = guest.data[guest.off(TEXT_LO):guest.off(TEXT_LO) + (TEXT_HI - TEXT_LO)]
    for ins in guest.md.disasm(blob, TEXT_LO):
        if not ins.mnemonic.startswith("b"):
            continue
        t = guest.target(ins)
        if t is None:
            continue
        out.setdefault(t, []).append((ins.address, ins.mnemonic))
    return out


# --------------------------------------------------------------------------
# table reading
# --------------------------------------------------------------------------
def load_table(path: Path) -> dict[int, dict]:
    if not path.is_file():
        raise Missing(f"table not found: {path}")
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise Missing(f"{path} is not valid TOML: {exc}") from exc
    functions = data.get("functions")
    if not isinstance(functions, dict):
        raise Missing(f'{path} has no [functions] table')
    out: dict[int, dict] = {}
    for key, body in functions.items():
        m = re.fullmatch(r"0[xX]([0-9A-Fa-f]+)", key)
        if not m:
            raise Missing(f'{path}: malformed function key {key!r}')
        va = int(m.group(1), 16)
        if not isinstance(body, dict):
            raise Missing(f'{path}: [functions."{key}"] is not a table')
        out[va] = dict(body)
    if not out:
        raise Missing(f"{path}: the [functions] table is empty")
    return out


# --------------------------------------------------------------------------
# verification
# --------------------------------------------------------------------------
class Verifier:
    def __init__(self, guest: Guest, pdata: Pdata, targets: dict[int, list]):
        self.guest = guest
        self.pdata = pdata
        self.targets = targets
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.rows: list[dict] = []
        self.n_standalone = 0
        self.n_chunk = 0
        self.n_gap = 0
        self.n_share = 0
        self.n_truncated = 0

    # -- helpers ---------------------------------------------------------
    def err(self, va: int, msg: str) -> None:
        self.errors.append(f"0x{va:08X}: {msg}")

    def warn(self, va: int, msg: str) -> None:
        self.warnings.append(f"0x{va:08X}: {msg}")

    def _reg_refs(self, va: int, end: int) -> set[int]:
        """The integer registers r14..r31 mentioned in [va, end)."""
        out: set[int] = set()
        cur = va
        while cur < end:
            ins = self.guest.decode(cur)
            if ins is None:
                break
            for tok in re.findall(r"\br(\d+)\b", ins.op_str):
                r = int(tok)
                if r in NONVOLATILE:
                    out.add(r)
            cur += 4
        return out

    # -- the checks ------------------------------------------------------
    def run(self, table: dict[int, dict]) -> None:
        # ---- 1. schema ------------------------------------------------
        for va in sorted(table):
            body = table[va]
            extra = set(body) - ALLOWED_KEYS
            if extra:
                self.err(va, f"unknown key(s) {sorted(extra)}")
            has_size = "size" in body
            has_end = "end" in body
            if has_size and has_end:
                self.err(va, "declares `size` AND `end` at the same time "
                            "(they are mutually exclusive)")
            if has_size:
                s = body["size"]
                if not isinstance(s, int) or s <= 0 or s % 4:
                    self.err(va, f"size {s!r} is not a positive multiple of 4")
            if has_end:
                e = body["end"]
                if not isinstance(e, int) or e <= va:
                    self.err(va, f"end {e!r} is not greater than the address")
            if "parent" in body:
                p = body["parent"]
                if not isinstance(p, int) or p == 0:
                    self.err(va, f"parent {p!r} must be a non-zero address")
            if "share_registers" in body:
                if not isinstance(body["share_registers"], bool):
                    self.err(va, "share_registers must be a boolean")
            if "name" in body and not isinstance(body["name"], str):
                self.err(va, "name must be a string")

        # ---- 2/3/4/5. per entry ---------------------------------------
        for va in sorted(table):
            body = table[va]
            if not body:
                self._check_gap(va)
                continue
            if "parent" in body and body["parent"]:
                self._check_chunk(va, body)
            else:
                self._check_standalone(va, body)

        # ---- 6. overlaps ------------------------------------------------
        self._check_overlaps(table)

    # -- individual entry kinds ------------------------------------------
    def _check_standalone(self, va: int, body: dict) -> None:
        self.n_standalone += 1
        if "share_registers" in body and body["share_registers"]:
            self.warn(va, "share_registers on a standalone entry has no effect "
                         "(it only means something for a chunk)")
        if not self.pdata.is_begin(va):
            self.err(va, "standalone entry is not a real .pdata begin inside "
                         ".text (a standalone entry must be a real function)")
            return
        real_end = self.pdata.extent_end(va)
        if "end" in body:
            declared = body["end"]
        elif "size" in body:
            declared = va + body["size"]
        else:
            self.err(va, "standalone entry without `end` and without `size` "
                         "(an empty table is only legal for a gap seed)")
            return
        if declared < real_end:
            self.n_truncated += 1
            self.err(va, f"TRUNCATED BODY: the declared extent ends at "
                         f"0x{declared:08X} but the real .pdata extent ends at "
                         f"0x{real_end:08X} ({real_end - va} B real, "
                         f"{declared - va} B declared)")
        elif declared > real_end:
            self.err(va, f"the declared extent (0x{declared:08X}) is longer "
                         f"than the real .pdata extent (0x{real_end:08X})")
        else:
            self.rows.append({"va": f"0x{va:08X}", "kind": "standalone",
                              "end": f"0x{declared:08X}",
                              "size": declared - va, "status": "OK"})

    def _check_chunk(self, va: int, body: dict) -> None:
        self.n_chunk += 1
        parent = body["parent"]
        if not self.pdata.is_begin(parent):
            self.err(va, f"parent 0x{parent:08X} is not a real .pdata begin")
            return
        pend = self.pdata.extent_end(parent)
        if not (parent <= va < pend):
            self.err(va, f"the chunk is not inside the extent of its parent "
                         f"0x{parent:08X}..0x{pend:08X}")
            return
        if "end" in body:
            declared = body["end"]
        elif "size" in body:
            declared = va + body["size"]
        else:
            self.err(va, "chunk without `end` and without `size`")
            return
        if declared <= va:
            self.err(va, f"end 0x{declared:08X} is not greater than the address")
            return
        if declared > pend:
            self.err(va, f"the chunk ends at 0x{declared:08X}, past the end of "
                         f"its parent (0x{pend:08X})")
            return
        real_end = walk_block(self.guest, va, pend)
        if declared < real_end:
            self.n_truncated += 1
            self.err(va, f"TRUNCATED BODY: the declared chunk ends at "
                         f"0x{declared:08X} but the real block that starts here "
                         f"ends at 0x{real_end:08X} ({real_end - va} B real, "
                         f"{declared - va} B declared)")
            return
        if declared > real_end:
            self.err(va, f"the declared chunk end (0x{declared:08X}) is longer "
                         f"than the real block end (0x{real_end:08X})")
            return
        if body.get("share_registers"):
            self.n_share += 1
            refs = self._reg_refs(va, declared)
            if not refs:
                self.err(va, "share_registers is not justified: the chunk body "
                             "references no register of r14..r31 at all")
            else:
                self.rows.append({"va": f"0x{va:08X}", "kind": "chunk",
                                  "parent": f"0x{parent:08X}",
                                  "end": f"0x{declared:08X}",
                                  "size": declared - va,
                                  "share_registers": True,
                                  "status": "OK"})
                return
        self.rows.append({"va": f"0x{va:08X}", "kind": "chunk",
                          "parent": f"0x{parent:08X}",
                          "end": f"0x{declared:08X}",
                          "size": declared - va, "status": "OK"})

    def _check_gap(self, va: int) -> None:
        self.n_gap += 1
        if self.pdata.is_begin(va):
            self.err(va, "an empty entry at a real .pdata begin is illegal: "
                         "a real function must declare its extent")
            return
        if self.pdata.owner(va) is not None:
            self.err(va, "an empty entry inside a .pdata extent is illegal: "
                         "use `parent` + `end` to declare a chunk")
            return
        if va not in self.targets:
            self.err(va, "an empty entry must be the target of a direct b/bl")
            return
        self.rows.append({"va": f"0x{va:08X}", "kind": "gap", "status": "OK"})

    # -- overlaps ----------------------------------------------------------
    def _check_overlaps(self, table: dict[int, dict]) -> None:
        standalones = []
        chunks = []
        for va in sorted(table):
            body = table[va]
            if not body:
                continue
            if "parent" in body and body["parent"]:
                end = body.get("end", va + body.get("size", 0))
                chunks.append((va, end, body["parent"]))
            else:
                end = body.get("end", va + body.get("size", 0))
                standalones.append((va, end))
        # standalone vs standalone
        for i, (a, ae) in enumerate(standalones):
            for b, be in standalones[i + 1:]:
                if b >= ae:
                    break
                self.err(b, f"OVERLAP: the standalone entry 0x{a:08X}+0x{ae - a:X} "
                            f"overlaps this entry (0x{b:08X}+0x{be - b:X})")
        # chunk vs standalone: only the parent's own extent may be overlapped
        for cv, ce, cp in chunks:
            for sv, se in standalones:
                if sv >= ce:
                    break
                if sv < ce and se > cv and sv != cp:
                    self.err(cv, f"OVERLAP: the chunk overlaps the standalone "
                                 f"entry 0x{sv:08X} (which is not its parent "
                                 f"0x{cp:08X})")
        # chunk vs chunk: reported, not fatal
        self.chunk_overlaps = 0
        cs = sorted(chunks)
        for i, (a, ae, _ap) in enumerate(cs):
            for b, be, _bp in cs[i + 1:]:
                if b >= ae:
                    break
                self.chunk_overlaps += 1


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Strict independent verifier of a ReXGlue function table.")
    ap.add_argument("--project-root", type=Path, default=None)
    ap.add_argument("--seed", type=Path, default=None,
                    help="table to verify (default: config/functions.toml)")
    ap.add_argument("--bin", type=Path, default=None,
                    help="guest image (default: ../NeverDead_base.exe)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--verbose", action="store_true",
                    help="print one line per entry")
    ap.add_argument("--selftest", action="store_true",
                    help="run the negative self-tests (each mutation of the "
                         "table MUST be rejected) and exit 0 only if all of "
                         "them are rejected")
    args = ap.parse_args(argv)

    root = (args.project_root or Path(__file__).resolve().parents[1]).resolve()
    seed_path = args.seed or (root / "config" / "functions.toml")
    bin_path = args.bin or (root.parent / "NeverDead_base.exe")

    try:
        if Cs is None:
            raise Missing("Capstone is not installed (pip install capstone==5.0.7)")
        guest = Guest(bin_path)
        pdata = Pdata(guest)
        targets = branch_targets(guest)
        table = load_table(seed_path)
    except Missing as exc:
        print(f"verify_seed: MISSING PREREQUISITE: {exc}", file=sys.stderr)
        return 2

    v = Verifier(guest, pdata, targets)
    v.run(table)

    report = {
        "table": str(seed_path),
        "guest_image": str(bin_path),
        "pdata_records": pdata.n_records,
        "pdata_entries_aligned": len(pdata.begins),
        "distinct_branch_targets": len(targets),
        "entries": len(table),
        "entries_standalone": v.n_standalone,
        "entries_chunk": v.n_chunk,
        "entries_gap": v.n_gap,
        "entries_share_registers": v.n_share,
        "truncated_bodies": v.n_truncated,
        "chunk_overlaps": getattr(v, "chunk_overlaps", 0),
        "errors": v.errors,
        "warnings": v.warnings,
        "verified_ok": not v.errors,
    }

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"verify_seed: {seed_path}")
        print(f"  guest image                : {bin_path}")
        print(f"  .pdata records (8 B)       : {pdata.n_records}")
        print(f"  .pdata entries in .text    : {len(pdata.begins)}")
        print(f"  distinct branch targets    : {len(targets)}")
        print(f"  entries in the table       : {len(table)}")
        print(f"    standalone                : {v.n_standalone}")
        print(f"    chunk                     : {v.n_chunk}")
        print(f"    gap seed (empty)          : {v.n_gap}")
        print(f"    share_registers           : {v.n_share}")
        print(f"  TRUNCATED BODIES           : {v.n_truncated}")
        print(f"  chunk/chunk overlaps       : {report['chunk_overlaps']} "
              f"(allowed: the codegen exempts chunks from the overlap test)")
        print(f"  errors                     : {len(v.errors)}")
        print(f"  warnings                   : {len(v.warnings)}")
        for e in v.errors[:40]:
            print(f"    FAIL {e}")
        if len(v.errors) > 40:
            print(f"    ... ({len(v.errors) - 40} more)")
        for w in v.warnings[:10]:
            print(f"    WARN {w}")
        if len(v.warnings) > 10:
            print(f"    ... ({len(v.warnings) - 10} more)")

    if args.verbose:
        for r in v.rows:
            print(f"{r['va']} {r['kind']:<10} {r.get('size', 0):>7} {r['status']}")

    if args.selftest:
        return self_test(root, seed_path, bin_path, guest, pdata, targets)

    return 1 if v.errors else 0


# --------------------------------------------------------------------------
# negative self-tests
# --------------------------------------------------------------------------
ENTRY_RX = re.compile(
    r'\[functions\."(0x[0-9A-Fa-f]+)"\]\n'
    r'(?:parent = (\d+)\n)?'
    r'(?:end = (\d+)\n)?'
    r'(?:share_registers = true\n)?')


def _mutate(txt: str, va: int, replacement: str) -> str | None:
    """Replace the entry of `va` by `replacement` (raw TOML text)."""
    m = ENTRY_RX.search(txt)
    for m in ENTRY_RX.finditer(txt):
        if int(m.group(1), 16) == va:
            return txt[:m.start()] + replacement + txt[m.end():]
    return None


def self_test(root: Path, seed_path: Path, bin_path: Path, guest: Guest,
              pdata: Pdata, targets: dict) -> int:
    """Every mutation below MUST be rejected (>= 1 error)."""
    import tempfile

    txt = seed_path.read_text(encoding="utf-8")
    cases = [
        ("NEG-1  truncated chunk 0x8278BD60 (20 B -> 4 B)",
         0x8278BD60,
         '[functions."0x8278BD60"]\nparent = 2188950840\nend = 2188950884\n',
         "TRUNCATED BODY"),
        ("NEG-2  an interior label declared STANDALONE",
         0x8278BD9C,
         '[functions."0x8278BD9C"]\nend = 2188950964\n',
         "not a real .pdata begin"),
        ("NEG-3  `size` and `end` declared at the same time",
         0x821CED90,
         '[functions."0x821CED90"]\nsize = 80\nend = 2188057680\n',
         "mutually exclusive"),
        ("NEG-4  a standalone extent that runs into the next function",
         0x821CED90,
         '[functions."0x821CED90"]\nend = 2188058000\n',
         "longer than the real .pdata extent"),
        ("NEG-5  share_registers on a chunk that touches no non-volatile",
         0x821D0910,
         '[functions."0x821D0910"]\nparent = 2182940880\nend = 2182941024\n'
         'share_registers = true\n',
         "not justified"),
    ]
    ok = True
    with tempfile.TemporaryDirectory() as td:
        for name, va, repl, expect in cases:
            mutated = _mutate(txt, va, repl)
            if mutated is None:
                print(f"  FAIL  {name}: the entry 0x{va:08X} is not in the table")
                ok = False
                continue
            p = Path(td) / "mutated.toml"
            p.write_text(mutated, encoding="utf-8")
            v = Verifier(guest, pdata, targets)
            v.run(load_table(p))
            hit = [e for e in v.errors if expect in e]
            good = bool(v.errors) and bool(hit)
            ok &= good
            print(f"  {'PASS' if good else 'FAIL'}  {name}")
            if v.errors:
                print(f"          -> {v.errors[0]}")
            else:
                print("          -> NOT REJECTED (this is a verifier bug)")
    print("SELFTEST OK" if ok else "SELFTEST FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
