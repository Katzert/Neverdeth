#!/usr/bin/env python3
"""
m3b_goto_audit.py - M3b O3: classify the `goto loc_X;` statements of a
recompiled tree whose target label does NOT exist in the same translation unit.

For every cross-TU goto the tool reports:
  * source TU / source function
  * target VA and the TU/function that owns the label (if any)
  * whether the target address is the BEGIN of a function in the tree
  * whether the target is declared in the seed TOML (CONFIG boundary)
  * whether the target is a `.pdata` begin / an interior label of a `.pdata`
  * the guest instruction at the target (Capstone, flat image)

Usage
-----
  python tools/m3b_goto_audit.py --tree generated/neverdead_default_v2 \
      --seed nd_seed_overrides_v2.toml [--json out.json] [--sample 20]

Exit code 0 always (this is a measurement tool, not a gate).
"""
from __future__ import annotations

import argparse
import bisect
import json
import re
import struct
import sys
from pathlib import Path

try:
    from capstone import CS_ARCH_PPC, CS_MODE_64, CS_MODE_BIG_ENDIAN, Cs
except ImportError:  # pragma: no cover
    Cs = None  # type: ignore[assignment]

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_seed_from_pdata import (  # noqa: E402
    GUEST_BASE,
    PDATA_HI,
    PDATA_LO,
    PDATA_LEN,
    PDATA_RAW,
    Guest,
    Pdata,
    parse_pdata,
    read_seed,
)

RE_FUNC = re.compile(r"DEFINE_REX_FUNC\(\s*(sub_[0-9A-Fa-f]+)\s*\)")
RE_LABEL = re.compile(r"^\s*(loc_[0-9A-Fa-f]+)\s*:", re.MULTILINE)
RE_GOTO = re.compile(r"goto\s+(loc_[0-9A-Fa-f]+)\s*;")
RE_SEED = re.compile(r'\[functions\."(0x[0-9A-Fa-f]+)"\]\s*\n\s*size\s*=\s*(\d+)')


def strip_line_comment(line: str) -> str:
    """Remove a trailing // comment (ignoring // inside a string literal)."""
    in_str = False
    i = 0
    while i < len(line) - 1:
        c = line[i]
        if c == '"' and (i == 0 or line[i - 1] != "\\"):
            in_str = not in_str
        elif not in_str and c == "/" and line[i + 1] == "/":
            return line[:i]
        i += 1
    return line


def hexva(name: str) -> int:
    return int(name.split("_")[1], 16)


def scan_tu(path: Path):
    """Return (funcs, labels, gotos) for one translation unit.

    funcs  : list of (func_name, start_offset) in file order
    labels : set of label names defined in the TU
    gotos  : list of (label_name, line_no, enclosing_func)
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    labels = set(RE_LABEL.findall(text))
    gotos = []
    # enclosing function per line: build a line -> func map
    func_at_line = {}
    cur = None
    for i, line in enumerate(text.splitlines(), 1):
        m = RE_FUNC.search(line)
        if m:
            cur = m.group(1)
        func_at_line[i] = cur
    for i, line in enumerate(text.splitlines(), 1):
        code = strip_line_comment(line)
        for m in RE_GOTO.finditer(code):
            gotos.append((m.group(1), i, func_at_line.get(i)))
    return labels, gotos


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", default="generated/neverdead_default_v2")
    ap.add_argument("--seed", default="nd_seed_overrides_v2.toml")
    ap.add_argument("--guest", default=None,
                    help="flat guest image (default: autodetect NeverDead_base.exe)")
    ap.add_argument("--json", default=None)
    ap.add_argument("--sample", type=int, default=20)
    ap.add_argument("--dump", default=None, help="write the full per-goto table as TSV")
    args = ap.parse_args()

    tree = Path(args.tree)
    seed_path = Path(args.seed)
    seed = read_seed(seed_path) if seed_path.exists() else {}

    # ---- tree scan ------------------------------------------------------
    tu_files = sorted(tree.glob("neverdead_recomp.*.cpp"))
    per_tu_labels: dict[str, set[str]] = {}
    label_owner: dict[str, tuple[str, str | None]] = {}   # label -> (tu, func)
    all_gotos: list[tuple[str, str, int, str | None]] = []  # (tu, label, line, func)
    func_starts: dict[str, str] = {}   # "sub_XXXXXXXX" -> tu

    for p in tu_files:
        text = p.read_text(encoding="utf-8", errors="replace")
        labels, gotos = scan_tu(p)
        per_tu_labels[p.name] = labels
        # owner of each label: the function whose body contains it
        lines = text.splitlines()
        owner: dict[int, str | None] = {}
        cur = None
        for i, line in enumerate(lines, 1):
            m = RE_FUNC.search(line)
            if m:
                cur = m.group(1)
            owner[i] = cur
        for i, line in enumerate(lines, 1):
            for m in RE_LABEL.finditer(line):
                label_owner.setdefault(m.group(1), (p.name, owner.get(i)))
        for m in RE_FUNC.finditer(text):
            func_starts.setdefault(m.group(1), p.name)
        for label, line, func in gotos:
            all_gotos.append((p.name, label, line, func))

    total_gotos = len(all_gotos)
    cross = [(tu, label, line, func) for (tu, label, line, func) in all_gotos
             if label not in per_tu_labels[tu]]
    cross_by_label: dict[str, list] = {}
    for tu, label, line, func in cross:
        cross_by_label.setdefault(label, []).append((tu, line, func))

    # ---- ground truth ---------------------------------------------------
    guest = None
    pdata = None
    if args.guest:
        gp = Path(args.guest)
    else:
        cands = [p for p in Path(".").glob("*.exe") if "base" in p.name.lower()]
        gp = cands[0] if cands else None
    if gp is not None and gp.exists():
        try:
            guest = Guest(gp)
            pdata = Pdata(parse_pdata(guest))
        except Exception:  # pragma: no cover
            guest = None

    md = None
    if guest is not None and Cs is not None:
        md = Cs(CS_ARCH_PPC, CS_MODE_BIG_ENDIAN | CS_MODE_64)
        md.skipdata = True

    def guest_insn(va: int) -> str:
        if guest is None or md is None:
            return ""
        o = va - GUEST_BASE
        if o < 0 or o + 4 > len(guest.data):
            return ""
        for ins in md.disasm(guest.data[o:o + 4], va, count=1):
            return f"{ins.mnemonic} {ins.op_str}".strip()
        return ""

    rows = []
    for label, sites in sorted(cross_by_label.items(), key=lambda kv: hexva(kv[0])):
        va = hexva(label)
        owner_tu, owner_func = label_owner.get(label, (None, None))
        in_seed = va in seed
        seed_size = seed.get(va)
        pd = pdata.containing(va) if pdata else None
        is_pdata_begin = bool(pd) and pd[0] == va
        is_func_start = f"sub_{va:08X}" in func_starts
        rows.append({
            "label": label,
            "va": "0x%08X" % va,
            "n_sites": len(sites),
            "source_tus": sorted({t for t, _, _ in sites}),
            "source_funcs": sorted({f for _, _, f in sites if f}),
            "owner_tu": owner_tu,
            "owner_func": owner_func,
            "in_seed_v2": in_seed,
            "seed_size": seed_size,
            "is_pdata_begin": is_pdata_begin,
            "pdata_range": ("0x%08X..0x%08X" % (pd[0], pd[2])) if pd else None,
            "is_func_start_in_tree": is_func_start,
            "func_start_tu": func_starts.get(f"sub_{va:08X}"),
            "guest_insn": guest_insn(va),
            "example_site": {"tu": sites[0][0], "line": sites[0][1], "func": sites[0][2]},
        })

    summary = {
        "tree": str(tree),
        "seed": str(seed_path),
        "seed_entries": len(seed),
        "tus": len(tu_files),
        "total_gotos": total_gotos,
        "cross_tu_gotos": len(cross),
        "distinct_cross_tu_labels": len(rows),
        "tus_affected": len({t for r in rows for t in r["source_tus"]}),
        "labels_with_owner_in_tree": sum(1 for r in rows if r["owner_tu"]),
        "labels_without_owner": sum(1 for r in rows if not r["owner_tu"]),
        "targets_in_seed_v2": sum(1 for r in rows if r["in_seed_v2"]),
        "targets_not_in_seed_v2": sum(1 for r in rows if not r["in_seed_v2"]),
        "targets_that_are_func_start": sum(1 for r in rows if r["is_func_start_in_tree"]),
        "targets_that_are_pdata_begin": sum(1 for r in rows if r["is_pdata_begin"]),
        "targets_interior_of_pdata": sum(1 for r in rows
                                         if r["pdata_range"] and not r["is_pdata_begin"]),
        "targets_outside_pdata": sum(1 for r in rows if not r["pdata_range"]),
    }

    print("=== M3b O3 audit: goto loc_X with no label in the same TU ===")
    for k, v in summary.items():
        print(f"  {k:38s}: {v}")
    print()
    print(f"--- sample of {min(args.sample, len(rows))} (of {len(rows)}) ---")
    hdr = (f"{'label':14s} {'va':12s} {'n':>4s} {'srcTU':22s} {'ownerTU':22s} "
           f"{'ownerFunc':14s} {'seed':5s} {'pdata':22s} {'fstart':6s} guest_insn")
    print(hdr)
    for r in rows[: args.sample]:
        print(f"{r['label']:14s} {r['va']:12s} {r['n_sites']:4d} "
              f"{(r['source_tus'][0] if r['source_tus'] else '-'):22s} "
              f"{(r['owner_tu'] or '-'):22s} {(r['owner_func'] or '-'):14s} "
              f"{('Y' if r['in_seed_v2'] else 'n'):5s} "
              f"{(r['pdata_range'] or '-'):22s} "
              f"{('Y' if r['is_func_start_in_tree'] else 'n'):6s} {r['guest_insn']}")

    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(
            json.dumps({"summary": summary, "rows": rows}, indent=2), encoding="utf-8")
    if args.dump:
        Path(args.dump).parent.mkdir(parents=True, exist_ok=True)
        with Path(args.dump).open("w", encoding="utf-8") as fh:
            fh.write("label\tva\tn_sites\tsource_tus\towner_tu\towner_func\t"
                     "in_seed\tseed_size\tpdata_begin\tpdata_range\tfunc_start\tguest_insn\n")
            for r in rows:
                fh.write("\t".join([
                    r["label"], r["va"], str(r["n_sites"]),
                    ";".join(r["source_tus"]), r["owner_tu"] or "-", r["owner_func"] or "-",
                    "Y" if r["in_seed_v2"] else "n", str(r["seed_size"] if r["seed_size"] is not None else "-"),
                    "Y" if r["is_pdata_begin"] else "n", r["pdata_range"] or "-",
                    "Y" if r["is_func_start_in_tree"] else "n", r["guest_insn"],
                ]) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
