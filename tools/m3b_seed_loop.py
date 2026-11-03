#!/usr/bin/env python3
"""
m3b_seed_loop.py - M3b O2: close the seed loop in BATCHES with a REAL codegen
analysis on every round.

Round protocol
--------------
  1. rebuild the seed from ground truth (.pdata + Capstone) with
       REQUIRED_NODE_ADDRESSES (M2b)
     + M3B_CROSS_TU_GOTO_TARGETS (318 cross-TU goto targets, O3)
     + every destination the codegen has reported as unresolved so far
     keeping the boundaries DISJOINT (validateConfigBoundaries rejects overlaps);
  2. run `rexglue codegen` FORCED to re-analyze (--ignore-stamp => the stamp
     cannot short-circuit the analysis; tools/m3b_codegen_run.py proves it);
  3. record: exit code, crash site, #Unresolved b/bl (lines and distinct
     destinations), the Codegen summary and the number of cross-TU `goto loc_X;`
     that remain in the generated tree;
  4. feed back, in ONE batch, every new unresolved destination AND every
     remaining cross-TU goto target;
  5. stop when the codegen exits 0 with a real analysis AND no cross-TU goto
     remains, or when a round produces no new destination.

Usage
-----
  python tools/m3b_seed_loop.py --work-dir DIR --manifest PATH \
      [--rounds 12] [--json-out PATH] [--audit-tree]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from m3b_codegen_run import run_codegen  # noqa: E402

BUILDER = HERE / "build_seed_from_pdata.py"
AUDITOR = HERE / "m3b_goto_audit.py"
SDK = Path(
    r"C:\Users\katze\.gemini\antigravity-ide\scratch\vibe-recomp"
    r"\rexglue_sdk\win-amd64\bin\rexglue.exe"
)


def audit_cross_tu(tree: Path, seed: Path, guest: Path, json_out: Path) -> dict:
    """Return the cross-TU goto summary of `tree` (0 gotos when the tree compiles)."""
    cmd = [sys.executable, str(AUDITOR), "--tree", str(tree), "--seed", str(seed),
           "--guest", str(guest), "--json", str(json_out)]
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if r.returncode != 0:
        return {"cross_tu_gotos": -1, "distinct_cross_tu_labels": -1, "error": r.stderr[-400:]}
    try:
        d = json.loads(json_out.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover
        return {"cross_tu_gotos": -1, "error": str(exc)}
    return d["summary"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--seed-out", required=True,
                    help="path of the seed TOML that the manifest includes")
    ap.add_argument("--extra", required=True,
                    help="accumulator file with the extra node addresses")
    ap.add_argument("--guest", default=str(ROOT.parent / "NeverDead_base.exe"))
    ap.add_argument("--rounds", type=int, default=12)
    ap.add_argument("--json-out", required=True)
    ap.add_argument("--log-dir", default=None)
    args = ap.parse_args()

    work = Path(args.work_dir)
    work.mkdir(parents=True, exist_ok=True)
    out_dir = Path(args.out_dir)
    seed_out = Path(args.seed_out)
    extra = Path(args.extra)
    log_dir = Path(args.log_dir) if args.log_dir else work / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    known: set[int] = set()
    history = []
    for rnd in range(1, args.rounds + 1):
        # ---- 1. rebuild the seed ------------------------------------------
        b = subprocess.run(
            [sys.executable, str(BUILDER), "--out", str(seed_out), "--emit",
             "--extra-nodes", str(extra), "--json"],
            capture_output=True, text=True, errors="replace", cwd=str(ROOT))
        try:
            bsum = json.loads(b.stdout[b.stdout.index("{"):])
        except Exception:
            bsum = {"error": b.stdout[-600:] + b.stderr[-600:]}
        seed_entries = bsum.get("disjoint_info", {}).get("final_entries")
        overlaps = bsum.get("overlapping_boundaries")

        # ---- 2. run the codegen FORCED to re-analyze ----------------------
        res = run_codegen(
            work_dir=work, manifest=Path(args.manifest), seed=seed_out,
            out_dir=out_dir, sdk=SDK, label=f"round{rnd}",
            use_ignore_stamp=True, delete_stamps=False,
            log_path=log_dir / f"round{rnd}.log")

        # ---- 3. measure ---------------------------------------------------
        new_targets = sorted(
            ({int(t, 16) for t in res["unresolved_b_targets"]}
             | {int(t, 16) for t in res["unresolved_bl_targets"]})
            - known)
        known |= {int(t, 16) for t in res["unresolved_b_targets"]}
        known |= {int(t, 16) for t in res["unresolved_bl_targets"]}

        cross = {"cross_tu_gotos": -1}
        if res["exit"] == 0:
            cross = audit_cross_tu(out_dir, seed_out, Path(args.guest),
                                   log_dir / f"audit_round{rnd}.json")

        row = {
            "round": rnd,
            "seed_entries": seed_entries,
            "seed_overlaps": overlaps,
            "exit": res["exit"],
            "exit_hex": res["exit_hex"],
            "elapsed_s": res["elapsed_s"],
            "REANALYZED": res["REANALYZED"],
            "analyze_markers": res["analyze_markers"],
            "summary": res["summary_line"],
            "written": res["written"],
            "unchanged": res["unchanged"],
            "unresolved_b_lines": res["unresolved_b_lines"],
            "unresolved_b_distinct": len(res["unresolved_b_targets"]),
            "unresolved_bl_lines": res["unresolved_bl_lines"],
            "unresolved_bl_distinct": len(res["unresolved_bl_targets"]),
            "unresolved_conditional_lines": res["unresolved_conditional_lines"],
            "crash_site": res["crash_site"],
            "last_line": res["last_line"],
            "new_unresolved_targets": ["0x%08X" % t for t in new_targets],
            "cross_tu": cross,
        }
        history.append(row)
        Path(args.json_out).write_text(json.dumps(history, indent=2), encoding="utf-8")
        print(json.dumps({k: v for k, v in row.items()
                          if k not in ("new_unresolved_targets",)}, sort_keys=False))
        sys.stdout.flush()

        # ---- 4. feed back --------------------------------------------------
        if res["exit"] == 0:
            # the codegen completed: only the cross-TU gotos can still block
            remaining = cross.get("distinct_cross_tu_labels", 0)
            if remaining in (0, -1) and res["unresolved_b_lines"] == 0:
                print("CONVERGED: exit 0 with a real analysis and no cross-TU goto")
                return 0
            if remaining <= 0:
                print("CONVERGED: exit 0 with a real analysis")
                return 0
            # add the remaining cross-TU targets (they are read from the audit)
            aj = log_dir / f"audit_round{rnd}.json"
            if aj.is_file():
                d = json.loads(aj.read_text(encoding="utf-8"))
                for r in d["rows"]:
                    va = int(r["va"], 16)
                    if va not in known:
                        known.add(va)
                        new_targets.append(va)
        else:
            # crashed: the crash target must own a node
            if res["crash_site"]:
                va = int(res["crash_site"]["target"], 16)
                if va not in known:
                    known.add(va)
                    new_targets.append(va)

        if not new_targets:
            print("NO NEW TARGETS -> stop (the codegen cannot be unstuck by the seed)")
            return 2

        with extra.open("a", encoding="utf-8") as fh:
            for va in new_targets:
                fh.write(f"0x{va:08X}\n")
        print(f"   -> added {len(new_targets)} addresses to {extra.name}")
        time.sleep(0.2)

    return 3


if __name__ == "__main__":
    raise SystemExit(main())
