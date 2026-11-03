#!/usr/bin/env python3
"""
tools/progress_meter.py - F3 progress metric for the NeverDead recompilation.

Measures, from a real watchdog log on disk, how far the recompiled guest got
towards the 3D render path:

    codegen -> apply_*.py -> build -> run_game_watchdog.py -> logs/neverdead_NNN.log

Usage:
    python tools/progress_meter.py            # text table, newest log
    python tools/progress_meter.py --json     # JSON, newest log
    python tools/progress_meter.py --log build_clang/logs/neverdead_138.log

Exit codes: 0 = the log was found and analysed, 1 = no log found.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG_DIR = PROJECT_ROOT / "build_clang" / "logs"

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

RE_BD34 = re.compile(r"BD34-tail\s+(0x[0-9A-Fa-f]+|\d+)\s+lr=")
RE_TRANSLATED = re.compile(r"Translated\s+(\d+)\s+shaders")
RE_FRAME = re.compile(r"\bFrame\s+#(\d+)")
# The real guest fault reported by the ReXGlue runtime.
RE_UNHANDLED_VIOLATION = re.compile(r"Unhandled guest access violation")
# A triggered REX_FATAL (== std::abort()) prints its branch: "Unresolved branch
# from 0xAAAA to 0xBBBB".
RE_REX_FATAL = re.compile(r"Unresolved branch from (0x[0-9A-Fa-f]+) to (0x[0-9A-Fa-f]+)")
# R4-B r1 attribution probe emitted by apply_bd_family_faithful.py at every
# owned BD entry (pure reads of ctx.r1, no semantic change):
#   neverdead: BDFAM-P entry sub_8278BD34 seq=5 r1=0x70190340 lr=0x8278BD4C
#   neverdead: BDFAM-P exit  sub_8278BD34 seq=5 r1=0x70190340
RE_BDFAM_PROBE = re.compile(
    r"BDFAM-P\s+(entry|exit)\s+(\S+)\s+seq=(\d+)\s+r1=0x([0-9A-Fa-f]+)(?:\s+lr=(0x[0-9A-Fa-f]+))?"
)
# R5-bis STKP probe: pure reads of ctx.r1/ctx.lr injected by
# apply_bd_family_faithful.py into the non-BD functions of the startup path
# and into the BD-region entries that had no probe yet.
#   neverdead: STKP entry sub_823C4C70 seq=1 r1=0x701905A0 lr=0x821FC210
#   neverdead: STKP exit  sub_823C4C70 seq=1 r1=0x70190540
RE_STKP_PROBE = re.compile(
    r"STKP\s+(entry|exit)\s+(\S+)\s+seq=(\d+)\s+r1=0x([0-9A-Fa-f]+)"
)

P_RELEASE_FAILED = "BaseHeap::Release failed because address is not a region start"
P_MAIN_LOOP = "Entering Asura main loop"
P_HEAP_GUARD = "[HEAP-GUARD] ignored invalid free"
# M6: the neutralised CRT terminate (sub_8278BBE8).  Every occurrence is one
# guest "terminate route" entry, i.e. one execution of the guest's
# 0x8278BD98 = `bl 0x8278bbe8`.
P_BBE8_TERMINATE = "BBE8 terminate intercepted"
# NOTE: "0x270190000" is the literal string used by the original brief.  It was
# measured over every log in build_clang/logs/ and never occurs (0 hits in all
# 100+ logs), so this counter is 0 BY CONSTRUCTION and carries no information.
# The real guest fault signal is `unhandled_guest_violations` below.
P_PROT_FAULT_LEGACY = "0x270190000"

# Lines that only *list* an import (NDIDUMP / IMPORT-SIN-WRAPPER / IMPORT-DIAG)
# are NOT evidence that the milestone was reached.
P_IMPORT_TABLE = ("NDIDUMP", "IMPORT-SIN-WRAPPER", "IMPORT-DIAG")

STACK_LO = 0x70000000
STACK_HI = 0x7FFFFFFF
# M5: the outermost probed function of the startup chain; its ENTRY samples
# are the iteration boundaries used for the per-iteration r1 delta.
R1_ANCHOR = "sub_821FC1F0"

# The six milestones of the ladder towards 3D rendering.
HITOS: dict[str, re.Pattern] = {
    "VdInitializeEngines": re.compile(r"\bVdInitializeEngines\b"),
    "VdInitializeRingBuffer": re.compile(r"\bVdInitializeRingBuffer\b"),
    "VdEnableRingBufferRPtrWriteBack": re.compile(r"\bVdEnableRingBufferRPtrWriteBack\b"),
    # "VdSetDisplayModeOverride" is a different call: exclude it.
    "VdSetDisplayMode": re.compile(r"\bVdSetDisplayMode\b(?!Override)"),
    "TranslatedShadersGt0": re.compile(r"Translated\s+(\d+)\s+shaders"),
    "VdSwap": re.compile(r"\bVdSwap\s+#"),
}


def newest_log(log_dir: Path) -> Path | None:
    """Newest neverdead_NNN.log (numeric ordering, robust to 4+ digit ids)."""
    if not log_dir.is_dir():
        return None

    def log_id(p: Path) -> int:
        m = re.search(r"_(\d+)\.log$", p.name)
        return int(m.group(1)) if m else -1

    logs = [p for p in log_dir.glob("neverdead_*.log") if p.is_file() and log_id(p) >= 0]
    if not logs:
        return None
    return max(logs, key=log_id)


def _to_int(tok: str) -> int:
    return int(tok, 16) if tok.lower().startswith("0x") else int(tok, 10)


def analyse(log_path: Path) -> dict:
    raw = log_path.read_text(encoding="utf-8", errors="replace")
    lines = raw.splitlines()

    bd34_values: list[int] = []
    bd34_lines: list[int] = []
    release_failed = 0
    heap_guard = 0
    unregistered = 0
    main_loop = 0
    prot_fault = 0
    unhandled = 0
    fatal_sites: list[str] = []
    probe_open: dict[tuple[str, int], int] = {}
    probe_by_fn: dict[str, dict] = {}
    probe_entry_order: list[tuple[str, int, int, int]] = []  # (fn, seq, r1, line)
    stkp_open: dict[tuple[str, int], int] = {}
    stkp_by_fn: dict[str, dict] = {}
    stkp_entry_order: list[tuple[str, int, int, int]] = []  # (fn, seq, r1, line)
    frames = 0
    shaders = 0
    hit_lines: dict[str, tuple[int, str]] = {}
    # M6: the guest's "terminate" route (sub_8278BBE8, the CRT terminate that
    # the project keeps neutralised).  The entries are attributed to the lr
    # that the sub_8278BD98 probe saw, i.e. to the guest call site that
    # entered the shared epilogue queue.  Nothing is inferred: both numbers
    # are counted from the log lines themselves.
    terminate_entries = 0
    terminate_sites: dict[str, int] = {}
    last_bd98_lr: str | None = None

    for idx, line in enumerate(lines, start=1):
        m = RE_BD34.search(line)
        if m:
            bd34_values.append(_to_int(m.group(1)))
            bd34_lines.append(idx)

        if P_RELEASE_FAILED in line:
            release_failed += 1
        if P_HEAP_GUARD in line:
            heap_guard += 1
        if "unregistered indirect call" in line:
            unregistered += 1
        if P_MAIN_LOOP in line:
            main_loop += 1
        if P_PROT_FAULT_LEGACY in line:
            prot_fault += 1
        if RE_UNHANDLED_VIOLATION.search(line):
            unhandled += 1
        if P_BBE8_TERMINATE in line:
            terminate_entries += 1
            key = last_bd98_lr if last_bd98_lr else "unknown"
            terminate_sites[key] = terminate_sites.get(key, 0) + 1
        m = RE_REX_FATAL.search(line)
        if m:
            fatal_sites.append(f"{m.group(1)}->{m.group(2)}")
        m = RE_BDFAM_PROBE.search(line)
        if m:
            kind, fn, seq, r1 = m.group(1), m.group(2), int(m.group(3)), int(m.group(4), 16)
            if kind == "entry":
                if fn == "sub_8278BD98":
                    last_bd98_lr = m.group(5) if m.lastindex and m.lastindex >= 5 else None
                probe_open[(fn, seq)] = r1
                probe_entry_order.append((fn, seq, r1, idx))
            else:
                r1_in = probe_open.pop((fn, seq), None)
                st = probe_by_fn.setdefault(fn, {"calls": 0, "net_r1": 0, "max_rise": 0, "max_drop": 0})
                st["calls"] += 1
                if r1_in is not None:
                    d = r1 - r1_in
                    st["net_r1"] += d
                    st["max_rise"] = max(st["max_rise"], d)
                    st["max_drop"] = min(st["max_drop"], d)
        m = RE_STKP_PROBE.search(line)
        if m:
            kind, fn, seq, r1 = m.group(1), m.group(2), int(m.group(3)), int(m.group(4), 16)
            if kind == "entry":
                stkp_open[(fn, seq)] = r1
                stkp_entry_order.append((fn, seq, r1, idx))
            else:
                r1_in = stkp_open.pop((fn, seq), None)
                st = stkp_by_fn.setdefault(fn, {"calls": 0, "net_r1": 0, "max_rise": 0, "max_drop": 0})
                st["calls"] += 1
                if r1_in is not None:
                    d = r1 - r1_in
                    st["net_r1"] += d
                    st["max_rise"] = max(st["max_rise"], d)
                    st["max_drop"] = min(st["max_drop"], d)
        if RE_FRAME.search(line):
            frames += 1
        m = RE_TRANSLATED.search(line)
        if m:
            shaders = int(m.group(1))

        is_import_table = any(tok in line for tok in P_IMPORT_TABLE)
        for name, rx in HITOS.items():
            if name in hit_lines:
                continue
            m = rx.search(line)
            if not m:
                continue
            if name == "TranslatedShadersGt0":
                if int(m.group(1)) <= 0:
                    continue
            elif is_import_table:
                # Only listing the import is not reaching the milestone.
                continue
            hit_lines[name] = (idx, line.strip())

    first = bd34_values[0] if bd34_values else None
    last = bd34_values[-1] if bd34_values else None
    drift = (first - last) if (first is not None and last is not None) else None
    stack_entries = sum(1 for v in bd34_values if STACK_LO <= v <= STACK_HI)

    hitos: dict[str, dict] = {}
    for name in HITOS:
        if name in hit_lines:
            ln, text = hit_lines[name]
            hitos[name] = {
                "status": "HIT",
                "evidence": {"file": str(log_path), "line": ln, "text": text},
            }
        else:
            hitos[name] = {"status": "MISS", "evidence": None}

    # M5: the per-iteration delta of the guest stack pointer.  The iteration
    # boundary is the ENTRY of the outermost probed function of the startup
    # chain (the "anchor"); the series is read verbatim from the log, nothing
    # is inferred.  A positive delta means the guest stack grew in that
    # iteration (the "over-pop"); the reference build (neverdead_128.log)
    # descends instead.
    anchor_name = R1_ANCHOR
    anchor_entries = [s for s in stkp_entry_order if s[0] == anchor_name]
    r1_iterations = []
    for i, s in enumerate(anchor_entries):
        delta = None
        if i > 0:
            delta = s[2] - anchor_entries[i - 1][2]
        r1_iterations.append(
            {"it": i, "line": s[3], "seq": s[1], "r1": s[2], "delta": delta}
        )
    deltas = [it["delta"] for it in r1_iterations if it["delta"] is not None]
    r1_delta_per_iteration = {
        "anchor": anchor_name,
        "anchor_entries": len(anchor_entries),
        "iterations": r1_iterations,
        "total_delta_bytes": (deltas[-1] if deltas else None),
        "positive_deltas": sum(1 for d in deltas if d > 0),
        "negative_deltas": sum(1 for d in deltas if d < 0),
        "zero_deltas": sum(1 for d in deltas if d == 0),
        "max_positive_delta": max(deltas) if deltas else None,
        "max_negative_delta": min(deltas) if deltas else None,
        "grows_up": bool(deltas and sum(deltas) > 0),
    }

    # r1 rise between two consecutive probed BD entries (the attribution
    # signal: a positive value means r1 grew without an intervening pop).
    rise_best = 0
    rise_at = None
    for i in range(1, len(probe_entry_order)):
        d = probe_entry_order[i][2] - probe_entry_order[i - 1][2]
        if d > rise_best:
            rise_best = d
            rise_at = {
                "from": f"{probe_entry_order[i-1][0]}#{probe_entry_order[i-1][1]}",
                "to": f"{probe_entry_order[i][0]}#{probe_entry_order[i][1]}",
                "from_line": probe_entry_order[i - 1][3],
                "to_line": probe_entry_order[i][3],
            }

    return {
        "log_file": str(log_path),
        "log_lines": len(lines),
        "bd34_calls": len(bd34_values),
        "bd34_r31_first": first,
        "bd34_r31_last": last,
        "bd34_r31_drift_bytes": drift,
        "bd34_r31_is_stack": bool(last is not None and STACK_LO <= last <= STACK_HI),
        "bd34_r31_stack_entries": stack_entries,
        "release_failed_count": release_failed,
        # Legacy key of the original brief.  ALWAYS 0 by construction: the
        # literal "0x270190000" never occurs in any log.  Kept for traceability
        # with AC2; the informative counter is unhandled_guest_violations.
        "prot_fault_0x270190000": prot_fault,
        "unhandled_guest_violations": unhandled,
        "fatal_rex_count": len(fatal_sites),
        "rex_fatal_sites": fatal_sites,
        "bd_r1_probe": {
            "entries_logged": len(probe_entry_order),
            "by_function": probe_by_fn,
            "max_rise_between_entries": rise_best,
            "max_rise_at": rise_at,
        },
        # R5-bis: the STKP probe (pure reads) on the non-BD functions of the
        # startup path and on the BD-region entries that had no probe yet.
        "stk_r1_probe": {
            "entries_logged": len(stkp_entry_order),
            "by_function": stkp_by_fn,
            "functions_probed": len(stkp_by_fn),
            "first_r1": stkp_entry_order[0][2] if stkp_entry_order else None,
            "last_r1": stkp_entry_order[-1][2] if stkp_entry_order else None,
        },
        # M5: the delta of the guest stack pointer per iteration of the
        # startup loop (read verbatim from the log).
        "r1_delta_per_iteration": r1_delta_per_iteration,
        # M1 (swe_native_3d_v2): flat mirror of the growth verdict, so that a
        # consumer does not have to dig into the nested structure.
        "grows_up": r1_delta_per_iteration["grows_up"],
        # M6: the guest's terminate route (sub_8278BBE8) and the guest call
        # sites that entered it (the lr seen by the sub_8278BD98 probe).
        "terminate_entries": terminate_entries,
        "terminate_sites": terminate_sites,
        "heap_guard_hits": heap_guard,
        "unregistered_indirect_calls": unregistered,
        "main_loop_entered": bool(main_loop),
        "frame_count": frames,
        "translated_shaders": shaders,
        "hitos": hitos,
    }


def _fmt(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return f"{value} (0x{value:08X})" if value > 0xFFFF else str(value)
    return str(value)


def print_text(m: dict) -> None:
    print(f"NeverDead F3 progress meter")
    print(f"log: {m['log_file']}")
    print("-" * 78)
    print(f"{'metric':<34} {'value':>24}")
    print("-" * 78)
    for key in (
        "log_lines",
        "bd34_calls",
        "bd34_r31_first",
        "bd34_r31_last",
        "bd34_r31_drift_bytes",
        "bd34_r31_is_stack",
        "bd34_r31_stack_entries",
        "release_failed_count",
        "unhandled_guest_violations",
        "prot_fault_0x270190000",
        "fatal_rex_count",
        "heap_guard_hits",
        "unregistered_indirect_calls",
        "terminate_entries",
        "main_loop_entered",
        "frame_count",
        "translated_shaders",
        "grows_up",
    ):
        print(f"{key:<34} {_fmt(m[key]):>24}")
    if m.get("terminate_sites"):
        print("-" * 78)
        print(f"{'terminate route by guest site':<34} {'entries':>8}   (lr seen by the sub_8278BD98 probe)")
        print("-" * 78)
        for lr, n in sorted(m["terminate_sites"].items(), key=lambda kv: -kv[1]):
            print(f"{lr:<34} {n:>8}")
    if m["rex_fatal_sites"]:
        print(f"{'rex_fatal_sites':<34} {', '.join(m['rex_fatal_sites'][:10]):>24}")
    pr = m.get("bd_r1_probe", {})
    if pr.get("entries_logged"):
        print("-" * 78)
        print(f"{'BD r1 probe':<34} {'calls':>6} {'net_r1':>10} {'max_rise':>10} {'max_drop':>10}")
        print("-" * 78)
        for fn, st in sorted(pr["by_function"].items()):
            print(
                f"{fn:<34} {st['calls']:>6} {st['net_r1']:>10} {st['max_rise']:>10} {st['max_drop']:>10}"
            )
        print(f"{'max r1 rise between entries':<34} {pr['max_rise_between_entries']:>10}   {pr['max_rise_at']}")
    print("-" * 78)
    print(f"{'milestone':<34} {'status':>8}   evidence")
    print("-" * 78)
    for name, h in m["hitos"].items():
        ev = h.get("evidence")
        loc = f"{Path(ev['file']).name}:{ev['line']}" if ev else ""
        txt = ev["text"][:70] if ev else ""
        print(f"{name:<34} {h['status']:>8}   {loc} {txt}")
    print("-" * 78)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="NeverDead F3 progress metric.")
    ap.add_argument("--log", type=Path, default=None, help="log to analyse (default: newest in build_clang/logs)")
    ap.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    ap.add_argument("--json", action="store_true", help="emit JSON instead of the text table")
    args = ap.parse_args(argv)

    log_path = args.log
    if log_path is None:
        log_path = newest_log(args.log_dir)
    if log_path is None or not Path(log_path).is_file():
        print(f"progress_meter: no log found (looked in {args.log_dir})", file=sys.stderr)
        return 1

    m = analyse(Path(log_path))
    if args.json:
        print(json.dumps(m, indent=2, sort_keys=True))
    else:
        print_text(m)
    return 0


if __name__ == "__main__":
    sys.exit(main())
