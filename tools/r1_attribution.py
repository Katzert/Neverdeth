#!/usr/bin/env python3
"""
r1_attribution.py - Attribution of the ABSOLUTE guest stack pointer (r1) delta.

R5-bis (2026-10-21).  Reads the real watchdog log and attributes the absolute
r1 movement to concrete guest functions, using ONLY the pure-read probes that
`apply_bd_family_faithful.py` injects:

    neverdead: BDFAM-P entry sub_8278BD34 seq=5 r1=0x70190340 lr=0x8278BD4C
    neverdead: BDFAM-P exit  sub_8278BD34 seq=5 r1=0x70190340
    neverdead: STKP    entry sub_823C4C70 seq=1 r1=0x701905A0 lr=0x821FC210
    neverdead: STKP    exit  sub_823C4C70 seq=1 r1=0x70190540

Two views are produced:

  1. PER FUNCTION: for every probed function, the number of measured calls and
     the net r1 effect (entry -> exit).  A non-zero net is a divergence of the
     recompiled build with respect to the guest, because the guest's own
     frame discipline for that entry is fixed by its bytes.

  2. PER ITERATION: the absolute r1 delta of each iteration of the startup
     loop, together with the function to which the delta is attributed and the
     log line that demonstrates it.  The iteration boundary is the entry of
     the outermost probed function of the call chain (the "anchor"); the
     attribution of the residual (unbracketed) growth is the pair of probe
     samples that bracket it, printed with their log lines.

Nothing is inferred: every number printed is read from the log.

Usage:
    python tools/r1_attribution.py [--log PATH] [--top 15] [--json OUT.json]
"""
from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path

RE_PROBE = re.compile(
    r"(BDFAM-P|STKP)\s+(entry|exit)\s+(\S+)\s+seq=(\d+)\s+r1=0x([0-9A-Fa-f]+)"
)
RE_TLS = re.compile(r"KeTlsSetValue ctx r1=0x([0-9A-Fa-f]+)")
RE_BD34 = re.compile(r"BD34-tail\s")


def newest_log(log_dir: Path) -> Path | None:
    logs = []
    for p in log_dir.glob("neverdead_*.log"):
        m = re.search(r"_(\d+)\.log$", p.name)
        if m:
            logs.append((int(m.group(1)), p))
    return max(logs)[1] if logs else None


def collect(path: Path):
    """Return (samples, lines).

    samples: list of (line_no, probe_tag, function, kind, seq, r1)
    """
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    samples = []
    for i, line in enumerate(lines, start=1):
        m = RE_PROBE.search(line)
        if m:
            samples.append((i, m.group(1), m.group(3), m.group(2), int(m.group(4)),
                            int(m.group(5), 16)))
            continue
        m = RE_TLS.search(line)
        if m:
            samples.append((i, "TLS", "KeTlsSetValue", "sample", 0, int(m.group(1), 16)))
    return samples, lines


def per_function(samples):
    """calls / net_r1 / max rise / max drop per probed function."""
    opens: dict[tuple, int] = {}
    st = collections.defaultdict(lambda: {"calls": 0, "entries": 0, "net_r1": 0,
                                          "max_rise": 0, "max_drop": 0,
                                          "rises": [], "drops": []})
    for ln, tag, fn, kind, seq, r1 in samples:
        if tag == "TLS":
            continue
        key = (tag, fn)
        if kind == "entry":
            opens[(tag, fn, seq)] = r1
            st[key]["entries"] += 1
        else:
            r1_in = opens.pop((tag, fn, seq), None)
            st[key]["calls"] += 1
            if r1_in is None:
                continue
            d = r1 - r1_in
            st[key]["net_r1"] += d
            if d > st[key]["max_rise"]:
                st[key]["max_rise"] = d
            if d < st[key]["max_drop"]:
                st[key]["max_drop"] = d
            if d:
                (st[key]["rises"] if d > 0 else st[key]["drops"]).append(
                    {"delta": d, "from_line": ln, "r1_in": r1_in, "r1_out": r1})
    return st


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--log", type=Path, default=None)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--anchor", default=None,
                    help="function name used as the iteration anchor")
    args = ap.parse_args(argv)

    log = args.log or newest_log(Path(__file__).resolve().parents[1] / "build_clang/logs")
    if log is None or not log.is_file():
        print("no log found", file=__import__("sys").stderr)
        return 1

    samples, lines = collect(log)
    st = per_function(samples)

    print(f"log: {log.name}  ({len(lines)} lines, {len(samples)} r1 samples)")
    if samples:
        print(f"first r1 = 0x{samples[0][5]:08X}   last r1 = 0x{samples[-1][5]:08X}   "
              f"total delta = {samples[-1][5] - samples[0][5]:+d} B")
    print()

    print("=== 1) PER FUNCTION (entry -> exit) ===")
    print(f"{'probe':<9}{'function':<20}{'entries':>8}{'exits':>7}{'net_r1':>11}"
          f"{'max_rise':>10}{'max_drop':>10}")
    for key in sorted(st, key=lambda k: (-abs(st[k]["net_r1"]), k[1])):
        s = st[key]
        print(f"{key[0]:<9}{key[1]:<20}{s['entries']:>8}{s['calls']:>7}"
              f"{s['net_r1']:>+11}{s['max_rise']:>10}{s['max_drop']:>10}")

    print()
    print("=== 2) TOP ABSOLUTE RISES BETWEEN CONSECUTIVE PROBE SAMPLES ===")
    rows = []
    for j in range(1, len(samples)):
        a, b = samples[j - 1], samples[j]
        rows.append((b[5] - a[5], a, b))
    for d, a, b in sorted(rows, key=lambda r: -r[0])[: args.top]:
        print(f"  {d:>+7} B  line {a[0]:>5} [{a[1]} {a[2]} {a[3]} r1=0x{a[5]:08X}]"
              f"  ->  line {b[0]:>5} [{b[1]} {b[2]} {b[3]} r1=0x{b[5]:08X}]")
        for k in range(a[0], min(b[0] + 1, len(lines))):
            if k != a[0] - 1 and k != b[0] - 1:
                print(f"          {k+1:>5}| {lines[k].split('] ')[-1]}")

    per_fn_entries = collections.defaultdict(list)
    for ln, tag, fn, kind, seq, r1 in samples:
        if kind == "entry":
            per_fn_entries[(tag, fn)].append((ln, seq, r1))
    print()
    print("=== 3) PER-ITERATION ABSOLUTE DELTA (anchor = entry of the anchor function) ===")
    # The anchor is the outermost probed member of the call chain: it is the
    # probed function with the largest |net_r1| (the chain members accumulate
    # the leak of the inner ones).  It can be overridden with --anchor.
    if args.anchor:
        anchor_key = None
        for k in st:
            if k[1] == args.anchor:
                anchor_key = k
        if anchor_key is None:
            print(f"anchor {args.anchor} not probed in this log")
            return 1
        anchor = per_fn_entries[anchor_key]
    else:
        anchor_key = max(st, key=lambda k: (abs(st[k]["net_r1"]), st[k]["entries"]))
        anchor = per_fn_entries[anchor_key]
    print(f"anchor function: {anchor_key[1]} ({len(anchor)} measured entries, "
          f"net_r1={st[anchor_key]['net_r1']:+d} B)")
    print(f"{'it':>4}{'line':>7}{'seq':>6}{'r1':>12}{'delta':>9}   attributed to")
    prev = None
    it = 0
    for ln, seq, r1 in anchor:
        d = r1 - prev if prev is not None else 0
        print(f"{it:>4}{ln:>7}{seq:>6}  0x{r1:08X}{d:>+9}   {anchor_key[1]}")
        prev = r1
        it += 1

    if args.json:
        out = {
            "log": str(log),
            "log_lines": len(lines),
            "r1_samples": len(samples),
            "first_r1": samples[0][5] if samples else None,
            "last_r1": samples[-1][5] if samples else None,
            "total_delta": (samples[-1][5] - samples[0][5]) if samples else None,
            "by_function": {f"{k[0]}:{k[1]}": v for k, v in st.items()},
        }
        args.json.write_text(json.dumps(out, indent=2), encoding="utf-8")
        print()
        print(f"JSON written to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
