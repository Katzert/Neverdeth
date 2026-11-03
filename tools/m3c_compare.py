#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
m3c_compare - tabla comparativa v1 vs v2 con tools/progress_meter.py (O3).

Uso:
    python tools/m3c_compare.py --v1 <log> --v2 <log> [--json-out PATH]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
METER = HERE / "progress_meter.py"

KEYS = [
    ("log_lines", "lineas de log"),
    ("bd34_calls", "llamadas BD34"),
    ("bd34_r31_drift_bytes", "deriva r31 de BD34 (B)"),
    ("release_failed_count", "release_failed"),
    ("unhandled_guest_violations", "violaciones de invitado no manejadas"),
    ("fatal_rex_count", "fatal_rex"),
    ("heap_guard_hits", "impactos del guard de heap"),
    ("main_loop_entered", "main_loop_entered"),
    ("frame_count", "frames"),
    ("translated_shaders", "shaders traducidos"),
    ("terminate_entries", "entradas terminate"),
    ("unregistered_indirect_calls", "llamadas indirectas sin registrar"),
    ("rex_fatal_sites", "sitios REX_FATAL"),
]


def meter(log: Path) -> dict:
    r = subprocess.run([sys.executable, str(METER), "--log", str(log), "--json"],
                       capture_output=True, text=True, errors="replace")
    txt = r.stdout or ""
    try:
        return json.loads(txt[txt.index("{"):])
    except Exception:
        return {}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--v1", type=Path, required=True)
    ap.add_argument("--v2", type=Path, required=True)
    ap.add_argument("--json-out", type=Path, default=None)
    args = ap.parse_args(argv)

    a, b = meter(args.v1), meter(args.v2)
    print(f"{'clave':<42} {'v1 (' + args.v1.name + ')':<26} v2 (" + args.v2.name + ")")
    print("-" * 120)
    for k, desc in KEYS:
        va, vb = a.get(k), b.get(k)
        sa = str(va)
        sb = str(vb)
        if len(sa) > 24:
            sa = sa[:21] + "..."
        if len(sb) > 24:
            sb = sb[:21] + "..."
        print(f"{k + ' (' + desc + ')':<42} {sa:<26} {sb}")
    for tag, d in (("v1", a), ("v2", b)):
        r = d.get("r1_delta_per_iteration") or {}
        print(f"{tag}: r1_delta_per_iteration -> grows_up={r.get('grows_up')} "
              f"iteraciones={len(r.get('iterations') or [])} "
              f"total_delta_bytes={r.get('total_delta_bytes')}")
        print(f"{tag}: terminate_sites={d.get('terminate_sites')}")
        print(f"{tag}: hitos Vd*/shaders:")
        for hk, hv in (d.get("hitos") or {}).items():
            ev = (hv or {}).get("evidence")
            print(f"      {hk:<32} {(hv or {}).get('status'):<6} "
                  f"evidencia={(ev or {}).get('text', '-')}")
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps({"v1": a, "v2": b}, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
