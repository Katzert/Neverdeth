#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m3c_grep_logs - busca un patron en varios logs y resume coincidencias por fichero."""
from __future__ import annotations

import argparse
import re
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pattern")
    ap.add_argument("paths", nargs="+", type=Path)
    ap.add_argument("--max", type=int, default=8)
    args = ap.parse_args(argv)
    rx = re.compile(args.pattern, re.I)
    for p in args.paths:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        hits = [ln for ln in text.splitlines() if rx.search(ln)]
        print(f"{p.name:45s} hits={len(hits)}")
        for ln in hits[: args.max]:
            print(f"      {ln[:190]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
