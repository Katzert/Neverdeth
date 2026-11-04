#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/apply_v13_perf_hooks.py
Aplica de forma idempotente los hooks de rendimiento y telemetria v13 sobre
el arbol recompilado generado (NdOnDrawCall en sub_826B1F88 y
NdShouldSkipLumaResolve en sub_823ED208).

Compatible con recompilacion v1 y v2 gracias a la resolucion dinamica de TUs
mediante nd_tu.py (honra NEVERDEAD_GENERATED_DIR).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

try:
    import nd_tu
except ImportError:
    nd_tu = None


def get_gen_dir() -> Path:
    if nd_tu is not None:
        return nd_tu.gen_dir(ROOT)
    override = os.environ.get("NEVERDEAD_GENERATED_DIR")
    if override:
        p = Path(override)
        return p if p.is_absolute() else (ROOT / p)
    return ROOT / "generated" / "neverdead_default"


def patch_file(path: Path, target_sig: str, replacement_code: str, marker: str) -> bool:
    if not path.is_file():
        print(f"[WARN] Archivo no existe: {path}")
        return False

    content = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
    if marker in content:
        print(f"[SKIP] {path.name}: {marker} ya presente (idempotente).")
        return True

    if target_sig in content:
        content = content.replace(target_sig, replacement_code, 1)
        path.write_text(content, encoding="utf-8", newline="\n")
        print(f"[OK]   {path.name}: inyectado {marker} con exito.")
        return True
    else:
        print(f"[FAIL] {path.name}: patron signature no encontrado.")
        return False


def main() -> int:
    gen_dir = get_gen_dir()
    print(f"[apply_v13_perf_hooks] Operando sobre arbol: {gen_dir}")
    if not gen_dir.is_dir():
        print(f"[ERROR] Directorio generado no encontrado: {gen_dir}", file=sys.stderr)
        return 1

    success = True

    # 1. Hook NdOnDrawCall() en sub_826B1F88
    func_draw = "sub_826B1F88"
    tu_draw = nd_tu.tu_of_func(gen_dir, func_draw) if nd_tu else None
    if tu_draw is None or not tu_draw.is_file():
        tu_draw = gen_dir / "neverdead_recomp.98.cpp"

    sig_draw = "DEFINE_REX_FUNC(sub_826B1F88) {\n\tREX_FUNC_PROLOGUE();"
    rep_draw = (
        'extern "C" void NdOnDrawCall();\n'
        'DEFINE_REX_FUNC(sub_826B1F88) {\n'
        '\tREX_FUNC_PROLOGUE();\n'
        '\tNdOnDrawCall();'
    )
    if not patch_file(tu_draw, sig_draw, rep_draw, "NdOnDrawCall"):
        success = False

    # 2. Hook NdShouldSkipLumaResolve() en sub_823ED208
    func_luma = "sub_823ED208"
    tu_luma = nd_tu.tu_of_func(gen_dir, func_luma) if nd_tu else None
    if tu_luma is None or not tu_luma.is_file():
        tu_luma = gen_dir / "neverdead_recomp.67.cpp"

    sig_luma = "DEFINE_REX_FUNC(sub_823ED208) {\n\tREX_FUNC_PROLOGUE();"
    rep_luma = (
        'extern "C" bool NdShouldSkipLumaResolve();\n'
        'DEFINE_REX_FUNC(sub_823ED208) {\n'
        '\tREX_FUNC_PROLOGUE();\n'
        '\tif (NdShouldSkipLumaResolve()) return;'
    )
    if not patch_file(tu_luma, sig_luma, rep_luma, "NdShouldSkipLumaResolve"):
        success = False

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
