#!/usr/bin/env python3
"""Mide el ring PM4 SOLO en gameplay 3D confirmado visualmente.

El protocolo anterior esperaba 45 s fijos y asumia que el juego ya estaba en el
hospital. Eso dejo ventanas dudosas en el borde del auto-avance. Aqui se
muestrea la ventana cada 2 s y se calcula la fraccion de pixeles no negros:

    titulo / carga   ~40-45 %
    gameplay 3D      > 85 %

Con 3 muestras seguidas por encima del umbral se considera gameplay real: se
guarda una captura de prueba, se dejan correr 14 s mas para acumular ventanas
[RING] de 60 frames y solo esas ventanas entran en el informe.

Uso:  python tools/bench_ring.py [--threshold 0.80] [--max-wait 180]
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import datetime as dt
import os
import re
import subprocess
import sys
import time

ROOT = r"C:\Users\katze\Downloads\_Proyectos_y_Backups\neverdead_project"
EXE = os.path.join(ROOT, "build_clang", "neverdead.exe")
LOGDIR = os.path.join(ROOT, "build_clang", "logs")
PROOF = os.path.join(ROOT, "docs", "assets", "ring_gameplay_proof.bmp")

u32 = ctypes.windll.user32
g32 = ctypes.windll.gdi32
u32.SetProcessDPIAware()


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class BIH(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


def find_hwnd(pid: int):
    hits = []
    proc_type = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(hwnd, _):
        owner = wt.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and u32.IsWindowVisible(hwnd):
            r = RECT()
            u32.GetClientRect(hwnd, ctypes.byref(r))
            w, h = r.right - r.left, r.bottom - r.top
            if w > 400 and h > 300:
                hits.append((hwnd, w, h))
                return False
        return True

    u32.EnumWindows(proc_type(cb), 0)
    return hits[0] if hits else None


def grab_raw(hwnd, w, h) -> bytes:
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
    bi.biCompression = 0
    buf = ctypes.create_string_buffer(w * h * 4)
    g32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    g32.DeleteObject(bmp)
    g32.DeleteDC(mdc)
    u32.ReleaseDC(hwnd, hdc)
    return buf.raw


def non_black(raw: bytes) -> float:
    n = len(raw)
    cnt = nb = 0
    for i in range(0, n - 4, 4 * 97):
        cnt += 1
        if raw[i] + raw[i + 1] + raw[i + 2] > 30:
            nb += 1
    return nb / max(cnt, 1)


RING_RE = re.compile(
    r"\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+)\].*\[RING\] swap #(\d+) \| "
    r"(\d+) dwords \| draws=(\d+) resolves=(\d+) bin_mask=(\d+) bin_select=(\d+) "
    r"pred=(\d+) regs=(\d+) swaps=(\d+) \| msaa=(\d+) pitch=(\d+) "
    r"modecontrol=0x([0-9A-Fa-f]+) surface=0x([0-9A-Fa-f]+)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=0.80)
    ap.add_argument("--max-wait", type=float, default=180.0)
    args = ap.parse_args()

    env = dict(os.environ)
    env["ND_RING"] = "1"
    env["ND_DIAG"] = "1"

    before = set(os.listdir(LOGDIR)) if os.path.isdir(LOGDIR) else set()
    proc = subprocess.Popen([EXE, "--game_data_root", "assets", "--cache_path", "cache"],
                            cwd=ROOT, env=env)
    print(f"PID={proc.pid}; esperando ventana...")
    hwnd = None
    for _ in range(60):
        time.sleep(0.5)
        hwnd = find_hwnd(proc.pid)
        if hwnd:
            break
    if not hwnd:
        proc.terminate()
        print("ERROR: sin ventana")
        return 1
    h, w = hwnd[2], hwnd[1]
    print(f"ventana {w}x{h}")

    streak = 0
    gameplay_at = None
    t0 = time.time()
    while time.time() - t0 < args.max_wait:
        raw = grab_raw(*hwnd)
        frac = non_black(raw)
        tag = "GAMEPLAY?" if frac > args.threshold else "menu/carga"
        print(f"  t={time.time() - t0:5.1f}s  no-negro={frac * 100:5.1f}%  {tag}")
        if frac > args.threshold:
            streak += 1
            if streak >= 3:
                gameplay_at = dt.datetime.now()
                with open(PROOF, "wb") as f:
                    row = w * 4
                    f.write(b"BM")
                    f.write((14 + 40 + row * h).to_bytes(4, "little"))
                    f.write(b"\0\0\0\0")
                    f.write((54).to_bytes(4, "little"))
                    f.write((40).to_bytes(4, "little"))
                    f.write(w.to_bytes(4, "little", signed=True))
                    f.write((-h).to_bytes(4, "little", signed=True))
                    f.write((1).to_bytes(2, "little"))
                    f.write((32).to_bytes(2, "little"))
                    f.write((0).to_bytes(4, "little"))
                    f.write((row * h).to_bytes(4, "little"))
                    f.write((2835).to_bytes(4, "little"))
                    f.write((2835).to_bytes(4, "little"))
                    f.write((0).to_bytes(4, "little"))
                    f.write((0).to_bytes(4, "little"))
                    f.write(raw)
                print(f"  >> gameplay confirmado a las {gameplay_at:%H:%M:%S}; captura en {PROOF}")
                break
        else:
            streak = 0
        time.sleep(2.0)

    if gameplay_at is None:
        proc.terminate()
        print("ERROR: no se detecto gameplay 3D")
        return 1

    print("  midiendo 14 s de ring en gameplay...")
    time.sleep(14.0)
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()

    after = set(os.listdir(LOGDIR)) if os.path.isdir(LOGDIR) else set()
    new = sorted(after - before)
    if not new:
        print("ERROR: sin log nuevo")
        return 1
    log = os.path.join(LOGDIR, new[-1])
    print(f"log: {log}")

    rows = []
    with open(log, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = RING_RE.search(line)
            if not m:
                continue
            ts = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S.%f")
            if ts < gameplay_at:
                continue
            rows.append((ts, int(m.group(2)), int(m.group(3)), int(m.group(4)),
                         int(m.group(5)), int(m.group(6)), int(m.group(7)),
                         int(m.group(8)), int(m.group(9)), int(m.group(10)),
                         int(m.group(11)), int(m.group(12)), m.group(13)))

    print()
    print(f"Ventanas [RING] en gameplay confirmado: {len(rows)}")
    if not rows:
        print("  (ninguna; sube --max-wait o baja el umbral)")
        return 1
    print(f"{'swap':>6} {'dwords':>7} {'draws':>6} {'resol':>6} {'binM':>5} "
          f"{'binS':>5} {'pred':>5} {'regs':>7} {'msaa':>5} {'pitch':>6}")
    for r in rows:
        print(f"{r[1]:>6} {r[2]:>7} {r[3]:>6} {r[4]:>6} {r[5]:>5} {r[6]:>5} "
              f"{r[7]:>5} {r[8]:>7} {r[10]:>5} {r[11]:>6}")
    n = len(rows)
    avg = lambda i: sum(r[i] for r in rows) / n
    print()
    print(f"MEDIA ({n} ventanas): draws={avg(3):.1f} resolves={avg(4):.1f} "
          f"bin_mask={avg(5):.1f} bin_select={avg(6):.1f} pred={avg(7):.1f} "
          f"regs={avg(8):.0f} dwords={avg(2):.0f} msaa={avg(10):.1f} pitch={avg(11):.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
