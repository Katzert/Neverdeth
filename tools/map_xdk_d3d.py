#!/usr/bin/env python3
"""Cartografo estatico de XDK D3D (0x826Bxxxx) y llamadores en Asura Engine.

Escanea generated/neverdead_default/*.cpp para catalogar las funciones
del subsistema grafico estatico de NeverDead y correlacionarlas con los CVars.
"""
import glob
import os
import re
import sys

def main():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    gen_dir = os.path.join(root, "generated", "neverdead_default")
    cpp_files = sorted(glob.glob(os.path.join(gen_dir, "*.cpp")))
    if not cpp_files:
        print(f"ERROR: no se encontraron archivos en {gen_dir}")
        return 1

    func_re = re.compile(r"DEFINE_REX_FUNC\((sub_826B[0-9A-Fa-f]{4})\)")
    call_re = re.compile(r"\b(sub_826B[0-9A-Fa-f]{4})\(")
    resolve_call_re = re.compile(r"sub_826B0EB0\(")

    xdk_funcs = set()
    asura_callers_resolve = set()

    for path in cpp_files:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            cur_func = None
            for line in f:
                mf = func_re.search(line)
                if mf:
                    cur_func = mf.group(1)
                    xdk_funcs.add(cur_func)
                elif "DEFINE_REX_FUNC(sub_" in line:
                    m_other = re.search(r"DEFINE_REX_FUNC\((sub_[0-9A-Fa-f]+)\)", line)
                    cur_func = m_other.group(1) if m_other else None
                elif cur_func and resolve_call_re.search(line):
                    # Check if caller is Asura engine (0x82300000 - 0x82680000)
                    addr = int(cur_func.replace("sub_", ""), 16)
                    if 0x82300000 <= addr <= 0x82680000:
                        asura_callers_resolve.add(cur_func)

    print(f"Funciones XDK D3D encontradas (0x826Bxxxx): {len(xdk_funcs)}")
    print(f"Llamadores de Asura a D3DDevice_Resolve (sub_826B0EB0): {len(asura_callers_resolve)}")
    for caller in sorted(asura_callers_resolve):
        print(f"  {caller}")

    return 0

if __name__ == "__main__":
    sys.exit(main())
