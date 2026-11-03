#!/usr/bin/env python3
"""
dead_tail_attribution.py - M5-C attribution of the guest stack pointer (r1)
movement around the fused guest function sub_823C4C70 and its "dead tail".

WHAT THIS TOOL MEASURES (nothing is inferred, everything is read from the log)
-----------------------------------------------------------------------------
The ReXGlue codegen fused TWO guest functions into one C++ function:

    guest 0x823C4C70 : mflr r12 ; bl 0x8278bd34      <- the real function
    guest 0x823C4C78 : stwu r1,-0xb0(r1) ... b 0x8278bd84   <- "dead tail"

The guest never executes the dead tail (the shared queue BD34..BD70 ends in
`b 0x8278ad9c` = restore + blr, i.e. it tail-calls out of 0x823C4C70).  The
recompiled build, however, RETURNS from the shared queue, falls into the dead
tail and executes its prologue + early exit, which ends in `b 0x8278bd84` ->
sub_8278BD84 -> `stwu r1,-0x60(r1)` with NO matching pop.

This tool produces, for every measured call:

  * the BD34 entry / exit r1 and the callsite (the guest `lr`),
  * the r1 delta of the segment that follows the BD34 exit up to the next
    BD34 entry (the "post-BD34 segment"),
  * the attribution of that delta to the probed functions crossed in it,
    with the residual that no probe covers.

Usage:
    python tools/dead_tail_attribution.py [--log PATH] [--json OUT.json]
"""
from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path

RE_PROBE = re.compile(
    r"(BDFAM-P|STKP)\s+(entry|exit)\s+(\S+)\s+seq=(\d+)\s+r1=0x([0-9A-Fa-f]+)"
    r"(?:\s+lr=0x([0-9A-Fa-f]+))?"
)
RE_BD34TAIL = re.compile(r"BD34-tail\s+(\S+)\s+lr=0x([0-9A-Fa-f]+)")

ROOT = Path(__file__).resolve().parents[1]


def newest_log(log_dir: Path) -> Path | None:
    logs = []
    for p in log_dir.glob("neverdead_*.log"):
        m = re.search(r"_(\d+)\.log$", p.name)
        if m:
            logs.append((int(m.group(1)), p))
    return max(logs)[1] if logs else None


def parse(path: Path):
    """Return (samples, bd34tails, nlines).

    samples: list of dicts {line, tag, fn, kind, seq, r1, lr}
    """
    raw = path.read_text(encoding="utf-8", errors="replace").splitlines()
    samples = []
    tails = []
    for i, ln in enumerate(raw):
        m = RE_PROBE.search(ln)
        if m:
            samples.append(
                {
                    "line": i + 1,
                    "tag": m.group(1),
                    "fn": m.group(3),
                    "kind": m.group(2),
                    "seq": int(m.group(4)),
                    "r1": int(m.group(5), 16),
                    "lr": int(m.group(6), 16) if m.group(6) else None,
                }
            )
            continue
        t = RE_BD34TAIL.search(ln)
        if t:
            tails.append({"line": i + 1, "r31": t.group(1), "lr": int(t.group(2), 16)})
    return samples, tails, len(raw)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", type=Path, default=None)
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--anchor", default="sub_821FC1F0")
    args = ap.parse_args(argv)

    log = args.log or newest_log(ROOT / "build_clang" / "logs")
    if log is None or not log.is_file():
        print("no log found")
        return 1
    samples, tails, nlines = parse(log)

    out: dict = {"log": str(log), "log_lines": nlines}

    # ---------------- per function (entry -> exit) ----------------
    byfn: dict[str, list[dict]] = collections.defaultdict(list)
    for s in samples:
        byfn[s["fn"]].append(s)
    perf: dict[str, dict] = {}
    for fn, ss in byfn.items():
        entries = [s for s in ss if s["kind"] == "entry"]
        exits = [s for s in ss if s["kind"] == "exit"]
        nets = []
        for e in entries:
            cand = [x for x in exits if x["line"] > e["line"]]
            if cand:
                nets.append(cand[0]["r1"] - e["r1"])
        perf[fn] = {
            "entries": len(entries),
            "exits": len(exits),
            "net_r1": sum(nets),
            "per_call": sorted(set(nets)),
            "calls": len(nets),
        }
    out["per_function"] = perf

    # ---------------- BD34 calls and the post-BD34 segment ----------------
    bd = [s for s in samples if s["fn"] == "sub_8278BD34"]
    bd_entries = [s for s in bd if s["kind"] == "entry"]
    bd_exits = [s for s in bd if s["kind"] == "exit"]
    calls = []
    for i, e in enumerate(bd_entries):
        ex = next((x for x in bd_exits if x["line"] > e["line"]), None)
        nxt = bd_entries[i + 1] if i + 1 < len(bd_entries) else None
        seg = {
            "call": i + 1,
            "entry_line": e["line"],
            "entry_r1": e["r1"],
            "entry_lr": e["lr"],
            "exit_line": ex["line"] if ex else None,
            "exit_r1": ex["r1"] if ex else None,
            "bd34_net": (ex["r1"] - e["r1"]) if ex else None,
            "callsite_lr": e["lr"],
        }
        if ex and nxt:
            seg["post_segment"] = {
                "from_line": ex["line"],
                "to_line": nxt["line"],
                "delta": nxt["r1"] - ex["r1"],
            }
        calls.append(seg)
    out["bd34_calls"] = calls

    # ---------------- attribution of the post-BD34 segments ----------------
    attr_rows = []
    for i, c in enumerate(calls):
        if "post_segment" not in c:
            continue
        lo = c["post_segment"]["from_line"]
        hi = c["post_segment"]["to_line"]
        inside = [s for s in samples if lo < s["line"] < hi]
        # net of each function whose entry and exit are both inside the segment
        crossed = []
        attributed = 0
        for fn in sorted({s["fn"] for s in inside}):
            ss = [s for s in inside if s["fn"] == fn]
            ents = [s for s in ss if s["kind"] == "entry"]
            exs = [s for s in ss if s["kind"] == "exit"]
            nets = []
            for e in ents:
                c2 = next((x for x in exs if x["line"] > e["line"]), None)
                if c2:
                    nets.append(c2["r1"] - e["r1"])
            if not nets:
                continue
            net = sum(nets)
            attributed += net
            crossed.append(
                {"fn": fn, "calls": len(nets), "net_r1": net, "per_call": sorted(set(nets))}
            )
        total = c["post_segment"]["delta"]
        attr_rows.append(
            {
                "call": c["call"],
                "from_line": lo,
                "to_line": hi,
                "delta": total,
                "attributed": attributed,
                "residual": total - attributed,
                "crossed": crossed,
            }
        )
    out["post_segment_attribution"] = attr_rows

    # ---------------- anchor windows (pre / post) ----------------
    anchor = [s for s in samples if s["fn"] == args.anchor and s["kind"] == "entry"]
    windows = []
    for k in range(len(anchor) - 1):
        la, ra = anchor[k]["line"], anchor[k]["r1"]
        lb, rb = anchor[k + 1]["line"], anchor[k + 1]["r1"]
        inner = [s for s in bd if la < s["line"] < lb]
        ent = [s for s in inner if s["kind"] == "entry"]
        exi = [s for s in inner if s["kind"] == "exit"]
        windows.append(
            {
                "win": k,
                "gap": rb - ra,
                "pre": (ent[0]["r1"] - ra) if ent else None,
                "post": (rb - exi[-1]["r1"]) if exi else None,
                "n_bd34": len(inner),
            }
        )
    out["anchor_windows"] = windows
    out["anchor"] = args.anchor

    # ---------------- text report ----------------
    print(f"log: {log}  ({nlines} lines, {len(samples)} probe samples)")
    print()
    print("=== A) per function (entry -> exit) ===")
    print(f"{'function':<22}{'calls':>7}{'net_r1':>10}  per-call")
    for fn, d in sorted(perf.items(), key=lambda kv: -abs(kv[1]["net_r1"])):
        if d["calls"] == 0:
            continue
        print(f"{fn:<22}{d['calls']:>7}{d['net_r1']:>10}  {d['per_call']}")
    print()
    print("=== B) BD34 calls: entry / exit / callsite ===")
    print(f"{'#':>3}{'entryL':>8}{'entry r1':>12}{'exit r1':>12}{'net':>7}{'lr':>12}")
    for c in calls:
        print(
            f"{c['call']:>3}{c['entry_line']:>8}0x{c['entry_r1']:08X}"
            f"{('0x%08X' % c['exit_r1']) if c['exit_r1'] is not None else '-':>12}"
            f"{(('%+d' % c['bd34_net']) if c['bd34_net'] is not None else '-'):>7}"
            f"{('0x%08X' % c['entry_lr']) if c['entry_lr'] else '-':>12}"
        )
    print()
    print("=== C) post-BD34 segment attribution ===")
    print(f"{'#':>3}{'lines':>14}{'delta':>9}{'attrib':>9}{'resid':>9}  crossed")
    for r in attr_rows:
        cr = ", ".join(f"{x['fn']}:{x['net_r1']:+d}({x['calls']})" for x in r["crossed"])
        print(
            f"{r['call']:>3}{('%d-%d' % (r['from_line'], r['to_line'])):>14}"
            f"{r['delta']:>+9}{r['attributed']:>+9}{r['residual']:>+9}  {cr}"
        )
    print()
    print("=== D) anchor windows (pre / post) ===")
    print(f"{'win':>4}{'gap':>9}{'pre':>9}{'post':>9}{'nBD34':>8}")
    for w in windows:
        print(
            f"{w['win']:>4}{w['gap']:>+9}{(w['pre'] if w['pre'] is not None else '-'):>9}"
            f"{(w['post'] if w['post'] is not None else '-'):>9}{w['n_bd34']:>8}"
        )
    print()
    hist = collections.Counter(
        (r["delta"]) for r in attr_rows
    )
    print("post-BD34 segment delta histogram:", dict(sorted(hist.items())))
    print("sum of post-BD34 segment deltas:", sum(r["delta"] for r in attr_rows))

    if args.json:
        args.json.write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(f"\nJSON written to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
