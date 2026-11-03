#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
m3c_bridge_verify - hashes de los cuerpos de las funciones propietarias del
puente del entrypoint (y de sub_824D9DF8) para demostrar que el script de
O1 no altera el arbol v1 y que sobre un arbol FRESCO de v2 produce
exactamente el mismo cuerpo que el arbol v2 ya auditado.

Uso:
    python tools/m3c_bridge_verify.py --tree DIR [--tree DIR ...] [--json PATH]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import nd_tu  # noqa: E402

OWNERS = ("xstart", "sub_826D1238")
EXTRA = ("sub_824D9DF8",)


def func_body(text: str, name: str) -> str | None:
    m = re.search(r"(?m)^DEFINE_REX_FUNC\(" + re.escape(name) + r"\)", text)
    if not m:
        return None
    nxt = re.search(r"(?m)^DEFINE_REX_FUNC\(", text[m.end():])
    end = m.end() + nxt.start() if nxt else len(text)
    return text[m.start():end]


def read_text(p: Path) -> str:
    with p.open("r", encoding="utf-8", errors="replace", newline="") as fp:
        return fp.read()


def sha(text: str | None) -> str | None:
    if text is None:
        return None
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def tree_bodies(gen_dir: Path) -> dict:
    out = {"tree": str(gen_dir), "bodies": {}}
    for name in OWNERS + EXTRA:
        p = nd_tu.tu_of_name(gen_dir, name)
        if p is None:
            out["bodies"][name] = {"tu": None, "sha256": None, "len": None}
            continue
        body = func_body(read_text(p), name)
        out["bodies"][name] = {
            "tu": p.name,
            "sha256": sha(body),
            "len": len(body) if body is not None else None,
        }
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", action="append", required=True, type=Path)
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args(argv)

    res = [tree_bodies(t) for t in args.tree]
    for r in res:
        print(f"== {r['tree']}")
        for name, info in r["bodies"].items():
            print(f"   {name:16s} tu={str(info['tu']):24s} len={str(info['len']):6s} "
                  f"sha256={info['sha256']}")
    if len(res) >= 2:
        a, b = res[0], res[1]
        same = all(a["bodies"][n]["sha256"] == b["bodies"][n]["sha256"]
                   for n in OWNERS + EXTRA)
        print(f"\ncompare {a['tree']} vs {b['tree']}: bodies identical = {same}")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(res, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
