#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m3c_log_ctx - imprime el contexto (N lineas antes/despues) de las coincidencias."""
from __future__ import annotations

import argparse
import re
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("log", type=Path)
    ap.add_argument("pattern")
    ap.add_argument("--before", type=int, default=8)
    ap.add_argument("--after", type=int, default=8)
    ap.add_argument("--max", type=int, default=5)
    args = ap.parse_args(argv)
    lines = args.log.read_text(encoding="utf-8", errors="replace").splitlines()
    rx = re.compile(args.pattern, re.I)
    shown = 0
    for i, ln in enumerate(lines):
        if rx.search(ln):
            shown += 1
            print(f"--------- match #{shown} at line {i + 1} ---------")
            for j in range(max(0, i - args.before), min(len(lines), i + args.after + 1)):
                mark = ">>" if j == i else "  "
                print(f"{mark} {lines[j][:190]}")
            if shown >= args.max:
                break
    if shown == 0:
        print("(no matches)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
