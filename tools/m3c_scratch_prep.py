#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
m3c_scratch_prep - prepara un scratch autocontenido para el codegen.

El manifest v2 usa rutas relativas al directorio del proyecto
(``../NeverDead_uncompressed.xex``). Para poder correr el codegen en un
scratch temporal (y comprobar el puente del entrypoint sobre un arbol
FRESCO, o bisecar la semilla) hace falta un manifest con rutas absolutas
apuntando al XEX real.

Uso:
    python tools/m3c_scratch_prep.py --work-dir DIR --seed nd_seed_overrides_v2.toml \
        [--xex PATH] [--game-root PATH]
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DEFAULT_XEX = ROOT.parent / "NeverDead_uncompressed.xex"
DEFAULT_GAME_ROOT = ROOT.parent

MANIFEST_TEMPLATE = """# M3c (swe_native_3d_v2) - manifest de SCRATCH con rutas absolutas.
# Copia mecanica de neverdead_manifest_v2.toml con:
#   - entrypoint.file_path  -> ruta absoluta del XEX
#   - game_root             -> ruta absoluta
#   - out_directory_path    -> <work-dir>/out
#   - includes              -> la semilla que se le pase (copia local)
[project]
name = "neverdead"
sdk_version = "0.10.0"
# Rutas en TOML literal string (comillas simples): las basic strings
# interpretan los backslashes de Windows como escapes.
game_root = '{game_root}'

[entrypoint]
file_path = '{xex}'
out_directory_path = '{out_dir}'
includes = ["{seed_name}"]
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work-dir", required=True, type=Path)
    ap.add_argument("--seed", required=True, type=Path)
    ap.add_argument("--xex", type=Path, default=DEFAULT_XEX)
    ap.add_argument("--game-root", type=Path, default=DEFAULT_GAME_ROOT)
    args = ap.parse_args(argv)

    work = args.work_dir
    work.mkdir(parents=True, exist_ok=True)
    out_dir = work / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    seed_copy = work / "seed_m3c.toml"
    shutil.copy2(args.seed, seed_copy)
    manifest = work / "manifest_m3c.toml"
    manifest.write_text(
        MANIFEST_TEMPLATE.format(
            game_root=str(args.game_root.resolve()),
            xex=str(args.xex.resolve()),
            out_dir=str(out_dir.resolve()),
            seed_name=seed_copy.name,
        ),
        encoding="utf-8",
    )
    print(f"manifest : {manifest}")
    print(f"seed     : {seed_copy}")
    print(f"out dir  : {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
