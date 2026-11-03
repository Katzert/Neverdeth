#!/usr/bin/env python3
"""Empaqueta la Release Alpha para el jugador (sin assets ni binario del juego).

Salida: dist/NeverDead_Recomp_v0.1.0_Alpha.zip

Incluye SOLO lo necesario para jugar si el usuario ya tiene su copia del juego
(NeverDead_base.bin + assets/, que NO viajan en el ZIP por copyright).
"""
from __future__ import annotations

import zipfile
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
VERSION = "v0.1.0_Alpha"
OUT = PROJ / "dist" / f"NeverDead_Recomp_{VERSION}.zip"

# (ruta en el proyecto, ruta dentro del zip)
FILES = [
    ("build_clang/neverdead.exe", "neverdead.exe"),
    ("build_clang/rexruntime.dll", "rexruntime.dll"),
    ("build_clang/rexgpu-xenos.dll", "rexgpu-xenos.dll"),
    ("neverdead.toml", "neverdead.toml"),
    ("neverdead_manifest.toml", "neverdead_manifest.toml"),
    ("JUGAR_NEVERDEAD.bat", "JUGAR_NEVERDEAD.bat"),
    ("README.md", "README.md"),
]
DIRS = [
    ("config", "config"),
    ("docs/assets", "docs/assets"),
]
# Herramienta de analisis de assets: el extractor real vive en tools/asura_extractor
# (31 MB de .exe de terceros, fuera del paquete). Se incluye el script de analisis.
OPTIONAL = [
    ("tools/analyze_assets.py", "tools/analyze_assets.py"),
    ("tools/BENCH_NEVERDEAD.bat", "tools/BENCH_NEVERDEAD.bat"),
    ("patches/sdk/rex/ppc/context.h", "patches/sdk/rex/ppc/context.h"),
    ("patches/sdk/README.md", "patches/sdk/README.md"),
]

# Nunca deben entrar en el ZIP. Solo se prohibe el assets/ de la RAIZ: el
# branding vive en docs/assets/ y si debe viajar.
FORBIDDEN_PREFIX = ("assets/", "generated/", "build_clang/logs")
FORBIDDEN_ANY = ("NeverDead_base.bin",)


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    missing: list[str] = []
    added: list[tuple[str, int]] = []

    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for src_rel, arc in FILES:
            src = PROJ / src_rel
            if not src.is_file():
                missing.append(src_rel)
                continue
            z.write(src, arc)
            added.append((arc, src.stat().st_size))

        for dir_rel, arc_root in DIRS:
            base = PROJ / dir_rel
            if not base.is_dir():
                missing.append(dir_rel)
                continue
            for f in sorted(base.rglob("*")):
                if f.is_file():
                    arc = f"{arc_root}/{f.relative_to(base).as_posix()}"
                    z.write(f, arc)
                    added.append((arc, f.stat().st_size))

        for src_rel, arc in OPTIONAL:
            src = PROJ / src_rel
            if src.is_file():
                z.write(src, arc)
                added.append((arc, src.stat().st_size))

    names = [a for a, _ in added]
    leaked = [
        n for n in names
        if n.startswith(FORBIDDEN_PREFIX) or any(bad in n for bad in FORBIDDEN_ANY)
    ]
    if leaked:
        OUT.unlink(missing_ok=True)
        print("ABORTADO: el ZIP contiene rutas prohibidas:", leaked)
        return 1

    size = OUT.stat().st_size
    print(f"OK  {OUT.relative_to(PROJ)}  ({size/1024/1024:.2f} MB, {len(added)} archivos)")
    for arc, sz in added:
        print(f"    {sz:>10,}  {arc}")
    if missing:
        print("AVISO: no encontrados ->", ", ".join(missing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
