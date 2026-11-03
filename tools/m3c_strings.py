#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m3c_strings - extrae strings ASCII/UTF-16 de un binario y filtra por regex."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

ASCII = re.compile(rb"[\x20-\x7e]{6,}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", type=Path)
    ap.add_argument("--grep", default=None)
    ap.add_argument("--max", type=int, default=200)
    args = ap.parse_args(argv)
    data = args.path.read_bytes()
    rx = re.compile(args.grep, re.I) if args.grep else None
    n = 0
    for m in ASCII.finditer(data):
        s = m.group().decode("ascii", "replace")
        if rx is None or rx.search(s):
            print(f"0x{m.start():08X} {s[:220]}")
            n += 1
            if n >= args.max:
                break
    print(f"({n} strings)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
