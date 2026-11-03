#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
m4_chain_run - ejecuta la cadena de parches del CMakeLists.txt contra un arbol
generado y registra el resultado de cada script (Owner: Worker, M4).

El objetivo es medir, script a script, si la cadena alcanza el arbol v2
(antes: 5 scripts apuntaban a v1 => 17 sitios ausentes) y si es idempotente.

    python tools/m4_chain_run.py --gen generated/neverdead_default_v2 --out LOGDIR
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def chain_scripts() -> list[str]:
    txt = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8", errors="replace")
    return re.findall(r'COMMAND \$\{Python3_EXECUTABLE\} "\$\{CMAKE_CURRENT_SOURCE_DIR\}/([^"]+)"', txt)


def treehash(gen: Path) -> str:
    files = sorted(p for p in gen.rglob("*") if p.is_file())
    agg = hashlib.sha256()
    for p in files:
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        rel = str(p.relative_to(gen)).replace("\\", "/")
        agg.update(rel.encode())
        agg.update(h.encode())
    return f"{agg.hexdigest()}:{len(files)}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gen", default="generated/neverdead_default_v2")
    ap.add_argument("--out", default=None, help="directorio de logs")
    ap.add_argument("--apply", action="store_true", help="aplica (por defecto: --check)")
    args = ap.parse_args(argv)

    gen = ROOT / args.gen
    if not gen.is_dir():
        print(f"no existe {gen}", file=sys.stderr)
        return 2
    out = Path(args.out) if args.out else ROOT / ".agents/teamwork/worker" / f"m4_chain_{gen.name}"
    out.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["NEVERDEAD_GENERATED_DIR"] = str(gen)
    env["PYTHONPATH"] = str(ROOT / "tools")

    before = treehash(gen)
    rows = []
    for i, s in enumerate(chain_scripts(), 1):
        cmd = [sys.executable, str(ROOT / s)]
        if not args.apply:
            cmd.append("--check")
        p = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True,
                           text=True, errors="replace", timeout=1800)
        log = out / f"{i:02d}_{Path(s).stem}.log"
        log.write_text(p.stdout + "\n--- stderr ---\n" + p.stderr, encoding="utf-8")
        tail = [l for l in (p.stdout or "").strip().splitlines() if l.strip()]
        rows.append((i, Path(s).name, p.returncode, tail[-1][:150] if tail else ""))
        print(f"[{i:02d}] {Path(s).name:<34} rc={p.returncode}  {tail[-1][:120] if tail else ''}")
    after = treehash(gen)
    print()
    print(f"tree before: {before}")
    print(f"tree after : {after}")
    print(f"CHANGED    : {'YES' if before != after else 'NO'}")
    (out / "chain_summary.txt").write_text(
        "\n".join(f"{i:02d}\t{n}\trc={rc}\t{t}" for i, n, rc, t in rows)
        + f"\n\ntree before: {before}\ntree after : {after}\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
