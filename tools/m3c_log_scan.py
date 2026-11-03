#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
m3c_log_scan - escanea un log de codegen y agrupa los mensajes por forma.

Uso: python tools/m3c_log_scan.py <log> [--grep PATTERN] [--region LO HI]
"""
from __future__ import annotations

import argparse
import collections
import re
from pathlib import Path

VA = re.compile(r"0x[0-9A-Fa-f]+")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("log", type=Path)
    ap.add_argument("--grep", default=None)
    ap.add_argument("--region", nargs=2, type=lambda s: int(s, 0), default=None)
    ap.add_argument("--top", type=int, default=40)
    args = ap.parse_args(argv)

    lines = args.log.read_text(encoding="utf-8", errors="replace").splitlines()
    shapes: collections.Counter = collections.Counter()
    examples: dict[str, list[str]] = collections.defaultdict(list)
    for ln in lines:
        shape = VA.sub("VA", ln)
        shape = re.sub(r"\b\d+\b", "N", shape).strip()
        shapes[shape] += 1
        if len(examples[shape]) < 3:
            examples[shape].append(ln)
    for shape, n in shapes.most_common(args.top):
        print(f"{n:7d} | {shape[:150]}")
    if args.grep:
        print(f"\n--- lines matching {args.grep!r} ---")
        lo, hi = args.region if args.region else (0, 0)
        rx = re.compile(args.grep, re.I)
        n = 0
        for ln in lines:
            if rx.search(ln):
                if args.region:
                    vas = [int(v, 16) for v in VA.findall(ln) if v.startswith("0x")]
                    if not any(lo <= v <= hi for v in vas):
                        continue
                print(ln[:200])
                n += 1
        print(f"({n} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
