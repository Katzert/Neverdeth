#!/usr/bin/env python3
"""Benchmark v6: A (base + parche fpscr), B (--no-d3d12_tiled_shared_memory),
C (Preset PC Bajo Recursos, 16 flags OFF).

Protocolo v4: lanzar, esperar 45 s (el juego auto-avanza al gameplay 3D del
hospital), medir ventanas de [FRAME] del log con ND_DIAG=1. La muestra C
pulsa el boton de preset nuevo (ID 1032) del menu F1 y asienta 10 s.

Uso:  python tools/bench_v6.py A|B|C
"""
import ctypes
import ctypes.wintypes as wt
import datetime as dt
import os
import re
import struct
import subprocess
import sys
import time

ROOT = r"C:\Users\katze\Downloads\_Proyectos_y_Backups\neverdead_project"
EXE = os.path.join(ROOT, "build_clang", "neverdead.exe")
LOGDIR = os.path.join(ROOT, "build_clang", "logs")
SCRATCH = os.path.join(ROOT, "scratch")

u32 = ctypes.windll.user32
g32 = ctypes.windll.gdi32
u32.SetProcessDPIAware()

WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
BM_CLICK = 0x00F5
VK_F1 = 0x70
ID_PRESET_LOW = 1032   # IDC_CFG_BTN_PRESET_LOW (v6)
ID_CLOSE = 1030


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class BIH(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


def grab(path, hwnd, w, h):
    hdc = u32.GetDC(hwnd)
    mdc = g32.CreateCompatibleDC(hdc)
    bmp = g32.CreateCompatibleBitmap(hdc, w, h)
    g32.SelectObject(mdc, bmp)
    u32.PrintWindow(hwnd, mdc, 2)
    bi = BIH()
    bi.biSize = ctypes.sizeof(BIH)
    bi.biWidth = w
    bi.biHeight = -h
    bi.biPlanes = 1
    bi.biBitCount = 32
    buf = ctypes.create_string_buffer(w * 4 * h)
    g32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    g32.DeleteObject(bmp)
    g32.DeleteDC(mdc)
    u32.ReleaseDC(hwnd, hdc)
    with open(path, "wb") as f:
        f.write(b"BM")
        row = w * 4
        f.write(struct.pack("<IHHI", 14 + 40 + row * h, 0, 0, 54))
        f.write(struct.pack("<IiiHHIIiiII", 40, w, -h, 1, 32, 0, row * h, 2835, 2835, 0, 0))
        f.write(buf.raw)


def window_of_pid(pid):
    best, area = None, 0

    def cb(h, lp):
        nonlocal best, area
        if not u32.IsWindowVisible(h):
            return True
        owner = wt.DWORD()
        u32.GetWindowThreadProcessId(h, ctypes.byref(owner))
        if owner.value != pid:
            return True
        r = RECT()
        u32.GetClientRect(h, ctypes.byref(r))
        a = (r.right - r.left) * (r.bottom - r.top)
        if a > area:
            area, best = a, h
        return True

    u32.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)(cb), 0)
    return best


def open_menu(hwnd):
    u32.PostMessageW(hwnd, WM_KEYDOWN, VK_F1, 0)
    time.sleep(0.1)
    u32.PostMessageW(hwnd, WM_KEYUP, VK_F1, 0)
    for _ in range(20):
        time.sleep(0.3)
        cfg = u32.FindWindowW("NeverDeadConfigClass", None)
        if cfg:
            return cfg
    return None


def close_menu(cfg):
    u32.SendMessageW(u32.GetDlgItem(cfg, ID_CLOSE), BM_CLICK, 0, 0)
    time.sleep(1.0)


def main():
    mode = (sys.argv[1] if len(sys.argv) > 1 else "A").upper()
    assert mode in ("A", "B", "C", "D", "E", "F"), "modo A-F"
    os.makedirs(SCRATCH, exist_ok=True)

    args = [EXE, "--game_data_root", "assets", "--cache_path", "cache"]
    if mode == "B":
        args.append("--no-d3d12_tiled_shared_memory")
    if mode == "E":
        args.append("--direct_host_resolve")
    if mode == "F":
        args += ["--mrt_edram_used_range_clamp_to_min", "--no-execute_unclipped_draw_vs_on_cpu"]
    args += sys.argv[2:]  # flags extra por CLI (p.ej. --no-pre_mask_resolve_l2_block)

    env = dict(os.environ)
    env["ND_DIAG"] = "1"           # [FRAME]/[SWAP]/[CS-GUARD]/[PAINT-MODE]
    env["ND_PROFILE_THREADS"] = "1"  # contadores de proceso en picos

    before = set(os.listdir(LOGDIR)) if os.path.isdir(LOGDIR) else set()
    proc = subprocess.Popen(args, cwd=ROOT, env=env)
    t_launch = time.time()
    time.sleep(8)
    hwnd = window_of_pid(proc.pid)
    if not hwnd:
        print("ERROR: no se encontro la ventana del juego")
        proc.terminate()
        return 1
    r = RECT()
    u32.GetClientRect(hwnd, ctypes.byref(r))
    w, h = r.right, r.bottom
    print(f"[{mode}] PID={proc.pid} ventana {w}x{h}; esperando gameplay (45 s)")
    while time.time() - t_launch < 45:
        time.sleep(1)

    # log nuevo de esta corrida
    log = None
    for _ in range(10):
        after = set(os.listdir(LOGDIR)) if os.path.isdir(LOGDIR) else set()
        news = sorted(after - before)
        if news:
            log = os.path.join(LOGDIR, news[-1])
            break
        time.sleep(1)
    if log is None:
        log = max((os.path.join(LOGDIR, f) for f in os.listdir(LOGDIR)),
                  key=os.path.getmtime)
    print(f"[{mode}] log: {os.path.basename(log)}")

    if mode in ("C", "D"):
        cfg = open_menu(hwnd)
        if not cfg:
            print("ERROR: no se abrio el menu F1")
            proc.terminate()
            return 1
        if mode == "C":
            btn = u32.GetDlgItem(cfg, ID_PRESET_LOW)
            if not btn:
                print("ERROR: no se encontro el boton Preset PC Bajo Recursos (ID 1032)")
                close_menu(cfg)
                proc.terminate()
                return 1
            u32.SendMessageW(btn, BM_CLICK, 0, 0)
            time.sleep(0.5)
            print(f"[{mode}] preset 16 flags aplicado; asentando 10 s (rampa de ~6 s)")
        else:
            # Estado B de v4: los 9 efectos OFF uno a uno (HUD queda ON).
            BM_GETCHECK = 0x00F0
            for cid in range(1010, 1019):  # SHADOW..RMLAA
                h = u32.GetDlgItem(cfg, cid)
                if h and u32.SendMessageW(h, BM_GETCHECK, 0, 0) != 1:
                    u32.SendMessageW(h, BM_CLICK, 0, 0)
                    time.sleep(0.25)
            print(f"[{mode}] 9 efectos OFF (estado B de v4); asentando 10 s")
        close_menu(cfg)
        time.sleep(10.0)

    t0 = dt.datetime.now()
    time.sleep(9.0)
    t1 = dt.datetime.now()
    try:
        grab(os.path.join(SCRATCH, f"v6_{mode}.bmp"), hwnd, w, h)
    except (MemoryError, OSError) as e:
        print(f"[{mode}] captura omitida: {e}")

    rx = re.compile(
        r"\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+)\].*\[FRAME\] (\d+) frames avg=([\d.]+) ms "
        r"\(([\d.]+) FPS\)")
    samples, guard, paintmode = [], 0, 0
    with open(log, encoding="utf-8", errors="replace") as f:
        for line in f:
            if "[CS-GUARD]" in line:
                guard += 1
            if "[PAINT-MODE] restaurado" in line:
                paintmode += 1
            m = rx.search(line)
            if not m:
                continue
            ts = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S.%f")
            if t0 <= ts <= t1:
                samples.append((int(m.group(2)), float(m.group(3)), float(m.group(4))))

    print(f"[{mode}] muestras [FRAME] en la ventana: {len(samples)}")
    for frames, avg_ms, fps in samples:
        print(f"    {frames} frames avg={avg_ms:.2f} ms ({fps:.1f} FPS)")
    if samples:
        avg_ms = sum(s[1] for s in samples) / len(samples)
        print(f"[{mode}] MEDIA: {avg_ms:.2f} ms/frame ({1000.0/avg_ms:.1f} FPS)")
    print(f"[{mode}] [CS-GUARD] totales en la corrida: {guard}")
    print(f"[{mode}] [PAINT-MODE] restaurados: {paintmode}")

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
