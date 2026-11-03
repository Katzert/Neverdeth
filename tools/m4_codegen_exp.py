#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
m4_codegen_exp - harness de codegen para los experimentos de M4 (E-Sigma / E-Delta).

Owner: Worker (M4).  NO toca el arbol de producto: cada experimento escribe en
un directorio de salida propio y usa un manifiesto con rutas absolutas.

    python tools/m4_codegen_exp.py --seed EXP_SEED.toml --out-dir OUT_DIR [--label L]
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
SDK = Path(r"C:\Users\katze\.gemini\antigravity-ide\scratch\vibe-recomp\rexglue_sdk\win-amd64\bin\rexglue.exe")
XEX = _ROOT.parent / "NeverDead_uncompressed.xex"

MANIFEST = """[project]
name = "neverdead"
sdk_version = "0.10.0"
game_root = "{root}"

[entrypoint]
file_path = "{xex}"
out_directory_path = "{out}"
includes = ["{seed}"]
"""

RE_SUMMARY = re.compile(r"Codegen summary:\s*(.+)")
RE_OVERLAP = re.compile(r"Overlapping boundaries")
RE_UNRES_B = re.compile(r"Unresolved b target 0x([0-9A-Fa-f]+) from 0x([0-9A-Fa-f]+)")
RE_UNRES_BL = re.compile(r"Unresolved bl target 0x([0-9A-Fa-f]+)")
RE_UNRES_COND = re.compile(r"Unresolved conditional branch to 0x([0-9A-Fa-f]+)")
RE_ANALYZE = re.compile(r"phase\s+\S+:\s*Discover|^\s*Analyze:|Unresolved b target"
                         r"|Unresolved bl target|has no blocks - generating stub", re.MULTILINE)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", required=True, help="fichero TOML del seed del experimento")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--work-dir", default=None)
    ap.add_argument("--label", default="exp")
    ap.add_argument("--sdk", default=str(SDK))
    ap.add_argument("--force", action="store_true", help="añade el flag global --force")
    args = ap.parse_args(argv)

    out = Path(args.out_dir)
    work = Path(args.work_dir) if args.work_dir else out.parent / (out.name + "_work")
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)

    seed_name = Path(args.seed).name
    seed_copy = work / seed_name
    if Path(args.seed).resolve() != seed_copy.resolve():
        shutil.copy2(Path(args.seed), seed_copy)

    man = work / f"manifest_{args.label}.toml"
    man.write_text(
        MANIFEST.format(root=_ROOT.parent.as_posix(), xex=XEX.as_posix(),
                        out=out.as_posix(), seed=seed_name),
        encoding="utf-8",
    )

    cmd = [args.sdk, "codegen", str(man), "--ignore-stamp"]
    if args.force:
        cmd.insert(1, "--force")
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=str(work), capture_output=True, text=True,
                          errors="replace", timeout=3600)
    el = time.time() - t0
    txt = (proc.stdout or "") + (proc.stderr or "")
    log = work / f"{args.label}.log"
    log.write_text(txt, encoding="utf-8", errors="replace")

    m = RE_SUMMARY.search(txt)
    res = {
        "label": args.label,
        "exit": proc.returncode,
        "exit_hex": f"0x{proc.returncode & 0xFFFFFFFF:08X}",
        "elapsed_s": round(el, 1),
        "out_dir": str(out),
        "manifest": str(man),
        "log": str(log),
        "summary": m.group(1).strip() if m else None,
        "overlapping_boundaries": len(RE_OVERLAP.findall(txt)),
        "unresolved_b": len(RE_UNRES_B.findall(txt)),
        "unresolved_bl": len(RE_UNRES_BL.findall(txt)),
        "unresolved_conditional": len(RE_UNRES_COND.findall(txt)),
        "reanalyzed_markers": len(RE_ANALYZE.findall(txt)),
        "last_line": next((l for l in reversed(txt.splitlines()) if l.strip()), "")[:200],
    }
    print(json.dumps(res, indent=2))
    (work / f"{args.label}.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
