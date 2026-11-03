#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
m4_v1_integrity - comprueba el arbol v1 contra el manifiesto M0 (M4).

Owner: Worker (M4).  El arbol v1 (generated/neverdead_default) es INTOCABLE:
este script comprueba que sus 276 entradas del manifiesto M0 siguen siendo
byte-identicas.

    python tools/m4_v1_integrity.py [--manifest tools/audited_snapshots/tree_digest.txt]
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default=str(ROOT / "tools/audited_snapshots/tree_digest.txt"))
    args = ap.parse_args(argv)
    man = Path(args.manifest)
    ok = bad = miss = 0
    skipped = 0
    badlist = []
    for line in man.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("  ", 1)
        if len(parts) != 2:
            parts = line.split(None, 1)
        if len(parts) != 2 or len(parts[0].strip()) != 64:
            skipped += 1  # lineas de resumen del manifiesto (no son entradas)
            continue
        h, rel = parts[0].strip(), parts[1].strip()
        p = ROOT / rel
        if not p.is_file():
            # el manifiesto anade una descripcion entre parentesis en algunas
            # entradas: se prueba tambien con el primer token de la ruta.
            cand = ROOT / rel.split("  ")[0].strip()
            if cand.is_file():
                p = cand
            else:
                miss += 1
                badlist.append(("MISSING", rel))
                continue
        h2 = hashlib.sha256(p.read_bytes()).hexdigest().upper()
        if h2 == h.upper():
            ok += 1
        else:
            bad += 1
            badlist.append(("DIFF", rel))
    print(f"MANIFEST M0 (v1): ok={ok} diff={bad} missing={miss} total={ok+bad+miss} "
          f"(lineas de resumen del manifiesto: {skipped})")
    for k, r in badlist[:20]:
        print(f"  {k} {r}")
    return 0 if (bad == 0 and miss == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
