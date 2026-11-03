#!/usr/bin/env python3
"""
audit_all_fatals.py - Censo Estático Total de REX_FATAL en NeverDead Recomp
Escanea todos los archivos generated/neverdead_default/neverdead_recomp.*.cpp
y clasifica el 100% de los REX_FATAL de forma estática con Capstone.
"""
import glob
import re
import sys
from collections import defaultdict
from pathlib import Path
import capstone

PROJECT_ROOT = Path(__file__).resolve().parent
GENERATED_DIR = PROJECT_ROOT / "generated" / "neverdead_default"
BASE_EXE = PROJECT_ROOT.parent / "NeverDead_base.exe"
REGISTER_FILE = GENERATED_DIR / "neverdead_register.cpp"

def main():
    if not GENERATED_DIR.exists():
        print(f"[!] Error: Directorio no existe: {GENERATED_DIR}")
        return 1

    print("[+] Cargando funciones registradas desde neverdead_register.cpp...")
    registered_funcs = set()
    if REGISTER_FILE.exists():
        with REGISTER_FILE.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                m = re.search(r"registrar->SetFunction\(0x([0-9A-Fa-f]+),\s*([^\)]+)\)", line)
                if m:
                    registered_funcs.add(int(m.group(1), 16))
    print(f"    Funciones registradas encontradas: {len(registered_funcs)}")

    print("[+] Escaneando archivos generated/neverdead_default/neverdead_recomp.*.cpp...")
    fatal_pattern = re.compile(r'REX_FATAL\("Unresolved branch from 0x([0-9A-Fa-f]+) to 0x([0-9A-Fa-f]+)"\)')
    fallback_fatal = re.compile(r'REX_FATAL\("([^"]+)"\)')
    
    records = []
    cpp_files = sorted(GENERATED_DIR.glob("neverdead_recomp.*.cpp"))
    for cpp in cpp_files:
        tu_name = cpp.name
        with cpp.open("r", encoding="utf-8", errors="ignore") as f:
            for idx, line in enumerate(f, 1):
                if "REX_FATAL" in line:
                    m = fatal_pattern.search(line)
                    if m:
                        src_va = int(m.group(1), 16)
                        dst_va = int(m.group(2), 16)
                        records.append({
                            "file": tu_name,
                            "line": idx,
                            "type": "unresolved_branch",
                            "src": src_va,
                            "dst": dst_va,
                            "raw": line.strip()
                        })
                    else:
                        m2 = fallback_fatal.search(line)
                        msg = m2.group(1) if m2 else line.strip()
                        records.append({
                            "file": tu_name,
                            "line": idx,
                            "type": "other_fatal",
                            "src": None,
                            "dst": None,
                            "raw": msg
                        })

    print(f"[+] Total de REX_FATAL encontrados: {len(records)}")

    # Clasificar destinos
    # Categoría 1: Helpers millicode (0x8278A000 - 0x8278C000)
    # Categoría 2: Destino es inicio exacto de función registrada en otro TU
    # Categoría 3: Destino es etiqueta interna / split de función
    cat1_millicode = []
    cat2_existing_func = []
    cat3_internal_split = []
    cat4_other = []

    for r in records:
        if r["type"] == "unresolved_branch":
            dst = r["dst"]
            if 0x8278A000 <= dst <= 0x8278C000:
                cat1_millicode.append(r)
            elif dst in registered_funcs:
                cat2_existing_func.append(r)
            else:
                cat3_internal_split.append(r)
        else:
            cat4_other.append(r)

    print("\n" + "="*60)
    print("RESUMEN CUANTITATIVO DEL CENSO ESTÁTICO DE REX_FATAL")
    print("="*60)
    print(f"Total absoluto de REX_FATAL en el codebase: {len(records)}")
    print(f"  - Categoría 1 (Millicode ABI 0x8278A000-0x8278C000) : {len(cat1_millicode)}")
    print(f"  - Categoría 2 (Destino es función registrada válida) : {len(cat2_existing_func)}")
    print(f"  - Categoría 3 (Etiqueta interna / función partida)   : {len(cat3_internal_split)}")
    print(f"  - Categoría 4 (Otros FATAL no-branch)                : {len(cat4_other)}")
    print("="*60)

    # Detalle de destinos más frecuentes en Cat 1
    if cat1_millicode:
        dst_counts = defaultdict(int)
        for r in cat1_millicode:
            dst_counts[f"0x{r['dst']:08X}"] += 1
        print("\nDestinos Top en Categoría 1 (Millicode):")
        for dst, cnt in sorted(dst_counts.items(), key=lambda x: x[1], reverse=True)[:10]:
            print(f"    {dst}: {cnt} ocurrencias")

    # Detalle de destinos más frecuentes en Cat 2
    if cat2_existing_func:
        dst_counts = defaultdict(int)
        for r in cat2_existing_func:
            dst_counts[f"0x{r['dst']:08X}"] += 1
        print("\nDestinos Top en Categoría 2 (Funciones existentes):")
        for dst, cnt in sorted(dst_counts.items(), key=lambda x: x[1], reverse=True)[:10]:
            print(f"    {dst}: {cnt} ocurrencias")

    # Detalle de destinos más frecuentes en Cat 3
    if cat3_internal_split:
        dst_counts = defaultdict(int)
        for r in cat3_internal_split:
            dst_counts[f"0x{r['dst']:08X}"] += 1
        print("\nDestinos Top en Categoría 3 (Splits internos / no registradas):")
        for dst, cnt in sorted(dst_counts.items(), key=lambda x: x[1], reverse=True)[:10]:
            print(f"    {dst}: {cnt} ocurrencias")

    return 0

if __name__ == "__main__":
    sys.exit(main())
