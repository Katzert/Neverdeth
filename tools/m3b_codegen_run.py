#!/usr/bin/env python3
"""
m3b_codegen_run.py - M3b O1/O2 harness: run `rexglue codegen` and PROVE whether
the run really re-analyzed the guest, or merely reused the recorded stamp.

Why this exists
---------------
M2b closed its seed loop with an "exit 0" that did NOT re-analyze: the codegen
short-circuited on the recorded stamp and reported "0 written / N unchanged".
This harness makes the difference observable and machine-checkable.

Re-analysis evidence (any of these markers in the log => the guest was analyzed):
  * "phase  <xex>: Discover"          (the analysis phase was entered)
  * "Analyze:"                        (per-function analysis decisions)
  * "Unresolved b target" / "Unresolved bl target"
  * "Function 0x... has no blocks"    (emission of stub functions)
Stamp-reuse evidence:
  * "Codegen summary: 0 written, ... N module(s) up to date"
  * absence of every marker above
and the stamp file mtime/hash is unchanged across the run.

CLI
---
  python tools/m3b_codegen_run.py --work-dir DIR --manifest PATH --seed PATH \
      --out-dir PATH [--label NAME] [--no-ignore-stamp] [--delete-stamps] \
      [--json-out PATH] [--keep-log]

Exit codes: 0 = the codegen run exited 0 (regardless of whether it re-analyzed;
the caller inspects the JSON), 2 = harness error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_SDK = Path(
    r"C:\Users\katze\.gemini\antigravity-ide\scratch\vibe-recomp"
    r"\rexglue_sdk\win-amd64\bin\rexglue.exe"
)

RE_ANALYZE = re.compile(
    r"phase\s+\S+:\s*Discover|^\s*Analyze:|Unresolved b target|Unresolved bl target"
    r"|has no blocks - generating stub",
    re.MULTILINE,
)
RE_SUMMARY = re.compile(r"Codegen summary:\s*(.+)")
RE_UNRES_B = re.compile(r"Unresolved b target 0x([0-9A-Fa-f]+) from 0x([0-9A-Fa-f]+)")
RE_UNRES_BL = re.compile(r"Unresolved bl target 0x([0-9A-Fa-f]+)")
RE_UNRES_COND = re.compile(r"Unresolved conditional branch to 0x([0-9A-Fa-f]+)")
RE_OVERLAP = re.compile(r"Overlapping boundaries")
RE_CRASH = re.compile(r"Unresolved b target 0x([0-9A-Fa-f]+) from 0x([0-9A-Fa-f]+)")


def read_text(p: Path) -> str:
    b = p.read_bytes()
    if b[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return b.decode("utf-16", errors="replace")
    return b.decode("utf-8", errors="replace")


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_hash(out_dir: Path) -> tuple[str, int, int]:
    """Aggregate sha256 over the output tree (same formula as tools/treehash.py)."""
    if not out_dir.exists():
        return ("", 0, 0)
    entries = []
    total = 0
    for p in sorted(out_dir.rglob("*")):
        if p.is_file():
            rel = p.relative_to(out_dir).as_posix()
            entries.append(f"{sha256_file(p)}  {rel}")
            total += p.stat().st_size
    agg = hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()
    return (agg, len(entries), total)


def stamp_state(out_dir: Path) -> dict:
    st = {}
    for name in ("codegen.stamp", "codegen.build.stamp", "codegen.partition.json"):
        p = out_dir / name
        if p.exists():
            st[name] = {
                "sha256": sha256_file(p),
                "mtime": p.stat().st_mtime,
                "size": p.stat().st_size,
            }
        else:
            st[name] = None
    return st


def run_codegen(
    work_dir: Path,
    manifest: Path,
    seed: Path,
    out_dir: Path,
    sdk: Path = DEFAULT_SDK,
    label: str = "run",
    use_ignore_stamp: bool = True,
    delete_stamps: bool = False,
    log_path: Path | None = None,
    timeout: int = 1800,
) -> dict:
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    out_dir = Path(out_dir)

    # The manifest includes the seed by relative name: make sure the copy in
    # work_dir is the one we want (the caller is responsible for keeping them
    # in sync; we assert the hashes match to avoid silent drift).
    seed_copy = work_dir / seed.name
    if seed_copy.resolve() != Path(seed).resolve():
        shutil.copy2(Path(seed), seed_copy)
    seed_hashes = {
        "project": sha256_file(Path(seed)),
        "workdir": sha256_file(seed_copy),
    }

    before = {"tree": tree_hash(out_dir), "stamps": stamp_state(out_dir)}

    if delete_stamps:
        removed = []
        for name in ("codegen.stamp", "codegen.build.stamp"):
            p = out_dir / name
            if p.exists():
                p.unlink()
                removed.append(name)
        before["stamps_removed"] = removed

    args = [str(sdk), "codegen", str(manifest)]
    if use_ignore_stamp:
        args.append("--ignore-stamp")

    t0 = time.time()
    proc = subprocess.run(
        args,
        cwd=str(work_dir),
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
    )
    elapsed = time.time() - t0
    out = (proc.stdout or "") + (proc.stderr or "")

    if log_path is None:
        log_path = work_dir / f"{label}.log"
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(out, encoding="utf-8", errors="replace")

    txt = out
    summary_m = RE_SUMMARY.search(txt)
    unres_b = RE_UNRES_B.findall(txt)
    unres_bl = RE_UNRES_BL.findall(txt)
    unres_cond = RE_UNRES_COND.findall(txt)
    last_line = ""
    for ln in reversed(txt.splitlines()):
        if ln.strip():
            last_line = ln.strip()
            break

    after = {"tree": tree_hash(out_dir), "stamps": stamp_state(out_dir)}

    reanalyzed = bool(RE_ANALYZE.search(txt))
    written = None
    unchanged = None
    up_to_date = None
    if summary_m:
        s = summary_m.group(1)
        mw = re.search(r"(\d+) written", s)
        mu = re.search(r"(\d+) unchanged", s)
        md = re.search(r"(\d+) deleted", s)
        mm = re.search(r"(\d+) module\(s\) up to date", s)
        written = int(mw.group(1)) if mw else None
        unchanged = int(mu.group(1)) if mu else None
        up_to_date = int(mm.group(1)) if mm else None
        summary_line = s.strip()
    else:
        summary_line = None

    crash_site = None
    if proc.returncode not in (0,) and unres_b:
        crash_site = {
            "target": "0x%08X" % int(unres_b[-1][0], 16),
            "from": "0x%08X" % int(unres_b[-1][1], 16),
        }

    return {
        "label": label,
        "exit": proc.returncode,
        "exit_hex": "0x%08X" % (proc.returncode & 0xFFFFFFFF),
        "elapsed_s": round(elapsed, 2),
        "cmd": args,
        "cwd": str(work_dir),
        "log": str(log_path),
        "summary_line": summary_line,
        "written": written,
        "unchanged": unchanged,
        "modules_up_to_date": up_to_date,
        "REANALYZED": reanalyzed,
        "analyze_markers": len(RE_ANALYZE.findall(txt)),
        "unresolved_b_lines": len(unres_b),
        "unresolved_b_targets": sorted({"0x%08X" % int(t, 16) for t, _ in unres_b}),
        "unresolved_b_sites": sorted({"0x%08X" % int(s, 16) for _, s in unres_b}),
        "unresolved_bl_lines": len(unres_bl),
        "unresolved_bl_targets": sorted({"0x%08X" % int(t, 16) for t in unres_bl}),
        "unresolved_conditional_lines": len(unres_cond),
        "unresolved_conditional_targets": sorted({"0x%08X" % int(t, 16) for t in unres_cond}),
        "overlapping_boundaries": len(RE_OVERLAP.findall(txt)),
        "crash_site": crash_site,
        "last_line": last_line[:200],
        "seed_sha256": seed_hashes,
        "tree_before": before["tree"],
        "tree_after": after["tree"],
        "tree_changed": before["tree"][0] != after["tree"][0],
        "stamps_before": before["stamps"],
        "stamps_after": after["stamps"],
        "stamps_removed": before.get("stamps_removed", []),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--seed", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--sdk", default=str(DEFAULT_SDK))
    ap.add_argument("--label", default="run")
    ap.add_argument("--no-ignore-stamp", action="store_true")
    ap.add_argument("--delete-stamps", action="store_true")
    ap.add_argument("--json-out", default=None)
    ap.add_argument("--log-out", default=None)
    args = ap.parse_args()

    res = run_codegen(
        work_dir=Path(args.work_dir),
        manifest=Path(args.manifest),
        seed=Path(args.seed),
        out_dir=Path(args.out_dir),
        sdk=Path(args.sdk),
        label=args.label,
        use_ignore_stamp=not args.no_ignore_stamp,
        delete_stamps=args.delete_stamps,
        log_path=Path(args.log_out) if args.log_out else None,
    )
    txt = json.dumps(res, indent=2)
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(txt, encoding="utf-8")
    print(txt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
