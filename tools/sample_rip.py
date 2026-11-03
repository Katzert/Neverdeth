#!/usr/bin/env python3
"""Muestreador de RIP fuera de proceso para neverdead.exe.

Lanza el juego, suspende cada hilo brevemente y lee su RIP (sin unwinding).
Agrupa las muestras y las resuelve contra build_clang/neverdead.map, de modo
que un bucle caliente en el codigo recompilado aparece como `sub_XXXXXXXX`.

Uso:
    python tools/sample_rip.py [segundos] [intervalo_ms]
"""
import ctypes
import ctypes.wintypes as wt
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, "build_clang", "neverdead.exe")
CWD = os.path.join(ROOT, "build_clang")
ASSETS = os.path.join(ROOT, "assets")
MAP = os.path.join(CWD, "neverdead.map")

TH32CS_SNAPTHREAD = 0x00000004
THREAD_SUSPEND_RESUME = 0x0002
THREAD_GET_CONTEXT = 0x0008
THREAD_QUERY_INFORMATION = 0x0040
CONTEXT_CONTROL = 0x00100001
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class THREADENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("cntUsage", wt.DWORD),
        ("th32ThreadID", wt.DWORD),
        ("th32OwnerProcessID", wt.DWORD),
        ("tpBasePri", ctypes.c_long),
        ("tpDeltaPri", ctypes.c_long),
        ("dwFlags", wt.DWORD),
    ]


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("cntUsage", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wt.DWORD),
        ("szExeFile", wt.CHAR * 260),
    ]


def load_map():
    """Devuelve [(addr, name)] ordenado por direccion."""
    syms = []
    rx = re.compile(r"^\s+[0-9a-f]{4}:[0-9a-f]{8}\s+(\S+)\s+([0-9a-f]{16})\s+(\S+)")
    with open(MAP, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            m = rx.match(line)
            if not m:
                continue
            name, addr, obj = m.group(1), int(m.group(2), 16), m.group(3)
            if name.startswith("__imp__") or name.startswith("__real@"):
                continue
            syms.append((addr, name, obj))
    syms.sort()
    return syms


def resolve(syms, addr):
    lo, hi = 0, len(syms) - 1
    best = None
    while lo <= hi:
        mid = (lo + hi) // 2
        if syms[mid][0] <= addr:
            best = syms[mid]
            lo = mid + 1
        else:
            hi = mid - 1
    if best is None:
        return None
    base, name, obj = best
    return name, obj, addr - base


class MODULEINFO(ctypes.Structure):
    _fields_ = [("lpBaseOfDll", ctypes.c_void_p),
                ("SizeOfImage", wt.DWORD),
                ("EntryPoint", ctypes.c_void_p)]


def modules_of(pid):
    """[(base, size, nombre)] del proceso, via psapi."""
    k32 = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi
    psapi.EnumProcessModules.argtypes = [wt.HANDLE, ctypes.POINTER(ctypes.c_void_p),
                                         wt.DWORD, ctypes.POINTER(wt.DWORD)]
    psapi.GetModuleInformation.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, wt.DWORD]
    psapi.GetModuleFileNameExW.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.LPWSTR, wt.DWORD]
    h = k32.OpenProcess(0x0410, False, pid)
    if not h:
        return []
    mods = (ctypes.c_void_p * 1024)()
    needed = wt.DWORD()
    psapi.EnumProcessModules(h, mods, ctypes.sizeof(mods), ctypes.byref(needed))
    out = []
    for i in range(needed.value // ctypes.sizeof(ctypes.c_void_p)):
        mi = MODULEINFO()
        psapi.GetModuleInformation(h, mods[i], ctypes.byref(mi), ctypes.sizeof(mi))
        buf = ctypes.create_unicode_buffer(512)
        psapi.GetModuleFileNameExW(h, mods[i], buf, 512)
        out.append((mi.lpBaseOfDll or 0, mi.SizeOfImage, buf.value))
    k32.CloseHandle(h)
    return out


def module_of(mods, addr):
    for base, size, name in mods:
        if base <= addr < base + size:
            return os.path.basename(name), addr - base
    return None, addr


def sample(pid, syms, mods, seconds, interval_ms):
    k32 = ctypes.windll.kernel32
    k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
    samples = Counter()
    per_thread = defaultdict(Counter)
    thread_order = []
    end = time.time() + seconds
    n = 0
    while time.time() < end:
        snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
        if snap == INVALID_HANDLE_VALUE:
            time.sleep(interval_ms / 1000.0)
            continue
        te = THREADENTRY32()
        te.dwSize = ctypes.sizeof(THREADENTRY32)
        ok = k32.Thread32First(snap, ctypes.byref(te))
        while ok:
            if te.th32OwnerProcessID == pid:
                tid = te.th32ThreadID
                h = k32.OpenThread(THREAD_SUSPEND_RESUME | THREAD_GET_CONTEXT |
                                   THREAD_QUERY_INFORMATION, False, tid)
                if h:
                    if k32.SuspendThread(h) != 0xFFFFFFFF:
                        # CONTEXT de x64: ContextFlags vive en 0x30 (los seis
                        # P1Home..P6Home ocupan 0x00..0x2F) y Rip en 0xF8.
                        ctx = (ctypes.c_uint64 * 160)()
                        ctypes.memmove(ctypes.addressof(ctx) + 0x30,
                                       ctypes.byref(wt.DWORD(CONTEXT_CONTROL)), 4)
                        if k32.GetThreadContext(h, ctypes.c_void_p(ctypes.addressof(ctx))):
                            rip = ctypes.c_uint64.from_buffer(ctx, 0xF8).value
                            mod, off = module_of(mods, rip)
                            if mod == "neverdead.exe":
                                name = resolve(syms, 0x140000000 + off)
                                key = (name[0], name[1]) if name else ("neverdead+0x%X" % off, "?")
                            else:
                                key = ("%s+0x%X" % (mod or "?", off), mod or "?")
                            samples[key] += 1
                            per_thread[tid][key] += 1
                            if tid not in thread_order:
                                thread_order.append(tid)
                        k32.ResumeThread(h)
                    k32.CloseHandle(h)
            ok = k32.Thread32Next(snap, ctypes.byref(te))
        k32.CloseHandle(snap)
        n += 1
        time.sleep(interval_ms / 1000.0)
    return samples, per_thread, thread_order, n


def main():
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 12.0
    interval = int(sys.argv[2]) if len(sys.argv) > 2 else 50
    syms = load_map()
    print(f"map: {len(syms)} simbolos")
    env = os.environ.copy()
    p = subprocess.Popen([EXE, "--game_data_root", ASSETS], cwd=CWD, env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"PID {p.pid}; calentando 2s...")
    time.sleep(2.0)
    mods = modules_of(p.pid)
    for base, size, name in mods:
        if base == 0x140000000 or "rex" in name.lower():
            print(f"modulo {os.path.basename(name)} base=0x{base:X} size=0x{size:X}")
    samples, per_thread, order, n = sample(p.pid, syms, mods, seconds, interval)
    try:
        p.kill()
    except Exception:
        pass
    p.wait()
    print(f"{n} rondas de muestreo, {sum(samples.values())} muestras de RIP")
    print("\n== global ==")
    for (name, obj), c in samples.most_common(30):
        print(f"{c:6d}  {name:28s} {obj}")
    print("\n== por hilo ==")
    for tid in order:
        print(f"-- tid {tid} ({sum(per_thread[tid].values())} muestras)")
        for (name, obj), c in per_thread[tid].most_common(10):
            print(f"   {c:6d}  {name:28s} {obj}")


if __name__ == "__main__":
    main()
