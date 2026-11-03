#!/usr/bin/env python3
"""Sonda de globals del invitado para neverdead.exe (sin rebuild).

El arena virtual del guest esta mapeado en el host a base + 0x200000000
(v. log "Guest memory arena mapped"), asi que un global en 0x82B3xxxx se lee
con ReadProcessMemory en 0x282B3xxxx.  Sirve para ver en que estado se queda
el arranque sin tocar el codigo recompilado.

Uso: python tools/probe_guest.py [segundos]
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, "build_clang", "neverdead.exe")
CWD = os.path.join(ROOT, "build_clang")
ASSETS = os.path.join(ROOT, "assets")
GUEST_BASE = 0x200000000  # arena virtual -> host

# (nombre, VA guest, tipo)
WATCH = [
    ("engine_state@82B312F8", 0x82B312F8, "u32"),
    ("engine_substate@82B312FC", 0x82B312FC, "u32"),
    ("engine_done@82B312DE", 0x82B312DE, "u8"),
    ("frontend_state@82B32084", 0x82B32084, "u32"),
    ("fe_flag8318@82B3207E", 0x82B3207E, "u8"),
    ("fe_flag8336@82B32090", 0x82B32090, "u8"),
    ("fe_flag8338@82B32092", 0x82B32092, "u8"),
    ("fe_obj8328@82B32088", 0x82B32088, "u32"),
    ("fe_timer8352@82B320A0", 0x82B320A0, "f32"),
    ("g_8072@82B31F88", 0x82B31F88, "u32"),
    ("g_8076@82B31F8C", 0x82B31F8C, "u32"),
    ("g_8080@82B31F90", 0x82B31F90, "u32"),
    ("g_8030@82B31F5E", 0x82B31F5E, "u8"),
    ("g_18396@82B347DC", 0x82B347DC, "u32"),
    ("g_18828@82B3498C", 0x82B3498C, "u32"),
]


def read(k32, h, va, kind):
    n = 1 if kind == "u8" else 4
    buf = (ctypes.c_ubyte * n)()
    got = ctypes.c_size_t()
    if not k32.ReadProcessMemory(h, ctypes.c_void_p(GUEST_BASE + va), buf, n, ctypes.byref(got)):
        return None
    if kind == "u8":
        return buf[0]
    v = int.from_bytes(bytes(buf), "little")
    if kind == "f32":
        return ctypes.c_float.from_buffer_copy(bytes(buf)).value
    return v


def main():
    secs = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    k32 = ctypes.windll.kernel32
    p = subprocess.Popen([EXE, "--game_data_root", ASSETS], cwd=CWD,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    h = k32.OpenProcess(0x0410, False, p.pid)
    print(f"PID {p.pid}  (Ctrl+C para salir)")
    t0 = time.time()
    try:
        while time.time() - t0 < secs and p.poll() is None:
            vals = []
            for name, va, kind in WATCH:
                v = read(k32, h, va, kind)
                vals.append(f"{name}={v if v is not None else '??'}")
            print(f"[{time.time()-t0:5.1f}s] " + "  ".join(vals), flush=True)
            time.sleep(1.0)
    finally:
        if p.poll() is None:
            p.kill()
        p.wait()
        k32.CloseHandle(h)


if __name__ == "__main__":
    main()
