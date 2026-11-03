#!/usr/bin/env python3
"""
batch_decompress_asura.py - Descomprime en bloque todos los contenedores .guiBE y .asrBE comprimidos con LZX.
Convierte los archivos a su formato nativo raw 'Asura   ' para lectura directa a 0 ms.
"""
import glob
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
QUICKBMS_EXE = PROJECT_ROOT / "tools" / "quickbms" / "quickbms.exe"
BMS_SCRIPT = PROJECT_ROOT / "tools" / "decompress_xmem.bms"
ASSETS_DIR = PROJECT_ROOT / "assets"

def main():
    if not QUICKBMS_EXE.exists() or not BMS_SCRIPT.exists():
        print(f"[!] Error: Herramientas QuickBMS no encontradas.")
        return 1

    targets = []
    for ext in ("*.guiBE", "*.asrBE"):
        for p in ASSETS_DIR.glob(f"**/{ext}"):
            with open(p, "rb") as f:
                header = f.read(4)
                if header in (b"\x0f\xf5\x12\xed", b"\x0f\xf5\x12\xee"):
                    targets.append(p)

    print(f"[+] Total de contenedores comprimidos detectados: {len(targets)}")

    tmp_out = PROJECT_ROOT / "tools" / "tmp_decomp"
    tmp_out.mkdir(parents=True, exist_ok=True)

    success = 0
    for t in targets:
        fname = t.name
        # Run quickbms
        cmd = [str(QUICKBMS_EXE), "-o", "-Y", str(BMS_SCRIPT), str(t), str(tmp_out)]
        res = subprocess.run(cmd, capture_output=True, text=True)
        out_file = tmp_out / fname
        if out_file.exists():
            with open(out_file, "rb") as f:
                magic = f.read(8)
            if magic == b"Asura   ":
                # Create backup
                bak = t.with_suffix(t.suffix + ".bak_xmem")
                if not bak.exists():
                    shutil.copy2(t, bak)
                # Overwrite with uncompressed raw
                shutil.copy2(out_file, t)
                out_file.unlink()
                print(f"[OK] {fname}: descomprimido exitosamente ({t.stat().st_size // 1024} KB)")
                success += 1
            else:
                print(f"[!] {fname}: magic inesperado: {magic}")
        else:
            print(f"[!] {fname}: fallo en extraccion QuickBMS")

    # Cleanup tmp dir
    try:
        shutil.rmtree(tmp_out)
    except Exception:
        pass

    print(f"\n[+] Proceso completado: {success}/{len(targets)} descomprimidos a raw 'Asura   '.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
