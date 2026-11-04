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

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
EXE = os.path.join(ROOT, "build_clang", "Neverdeth.exe")
if not os.path.exists(EXE):
    EXE = os.path.join(ROOT, "build_clang", "neverdead.exe")
LOGDIR = os.path.join(ROOT, "build_clang", "logs")
PROOF = os.path.join(ROOT, "build_clang", "ring_gameplay_proof.bmp")
PROOF_PNG = os.path.join(ROOT, "build_clang", "ring_gameplay_proof.png")

u32 = ctypes.windll.user32
g32 = ctypes.windll.gdi32
u32.SetProcessDPIAware()

VK_RETURN = 0x0D
VK_SPACE = 0x20
VK_A = 0x41
SW_RESTORE = 9


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


def press_enter(hwnd: int) -> None:
    try:
        u32.ShowWindow(hwnd, SW_RESTORE)
        u32.BringWindowToTop(hwnd)
        u32.SetForegroundWindow(hwnd)
        # Enviar Enter
        u32.PostMessageW(hwnd, 0x0100, VK_RETURN, 0x001C0001)  # WM_KEYDOWN
        u32.keybd_event(VK_RETURN, 0, 0, 0)
        time.sleep(0.05)
        u32.PostMessageW(hwnd, 0x0101, VK_RETURN, 0xC01C0001)  # WM_KEYUP
        u32.keybd_event(VK_RETURN, 0, 2, 0)
        # Enviar Space
        time.sleep(0.05)
        u32.PostMessageW(hwnd, 0x0100, VK_SPACE, 0x00390001)  # WM_KEYDOWN
        u32.keybd_event(VK_SPACE, 0, 0, 0)
        time.sleep(0.05)
        u32.PostMessageW(hwnd, 0x0101, VK_SPACE, 0xC0390001)  # WM_KEYUP
        u32.keybd_event(VK_SPACE, 0, 2, 0)
        # Enviar Tecla 'A'
        time.sleep(0.05)
        u32.PostMessageW(hwnd, 0x0100, VK_A, 0x001E0001)  # WM_KEYDOWN
        u32.keybd_event(VK_A, 0, 0, 0)
        time.sleep(0.05)
        u32.PostMessageW(hwnd, 0x0101, VK_A, 0xC01E0001)  # WM_KEYUP
        u32.keybd_event(VK_A, 0, 2, 0)
    except Exception:
        pass


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
    ap.add_argument("--duration", type=float, default=22.0)
    ap.add_argument("--filter", type=str, default="0")
    ap.add_argument("--ring", type=str, default="1")
    ap.add_argument("--throttle", type=str, default="1")
    ap.add_argument("--luma-opt", type=str, default="0")
    args = ap.parse_args()

    env = dict(os.environ)
    env["ND_RING"] = args.ring
    env["ND_DIAG"] = "1"
    env["ND_XDK_FILTER"] = args.filter
    env["ND_GUEST_THROTTLE"] = args.throttle
    env["ND_OPT_LUMA_RESOLVE"] = args.luma_opt
    env["ND_AUTO_NAV"] = "1"

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
    last_press = 0.0
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
                try:
                    from PIL import Image
                    im = Image.frombuffer("RGBA", (w, h), raw, "raw", "BGRA", 0, 1)
                    im.convert("RGB").save(PROOF_PNG)
                except Exception:
                    pass
                print(f"  >> gameplay 3D confirmado ({frac*100:.1f}% no-negro) a las {gameplay_at:%H:%M:%S}; captura en {PROOF}")
                break
        else:
            streak = 0
            cur_t = time.time()
            if cur_t - t0 >= 4.0 and cur_t - last_press >= 3.0:
                press_enter(hwnd[0])
                last_press = cur_t
        time.sleep(2.0)

    if gameplay_at is None:
        proc.terminate()
        print("ERROR: no se detecto gameplay 3D")
        return 1

    print(f"  midiendo {args.duration:.0f} s de ring en gameplay...")
    time.sleep(args.duration)
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
    print(f"Ventanas [RING] totales tras confirmacion: {len(rows)}")
    # Filtrar estrictamente solo ventanas con firma de hospital (>400 draws, >200k dwords)
    gameplay_rows = [r for r in rows if r[3] >= 400 and r[2] >= 200000]
    print(f"Ventanas [RING] verificadas en Hospital 3D (draws>=400, dwords>=200k): {len(gameplay_rows)}")
    active_rows = gameplay_rows if gameplay_rows else rows

    if active_rows:
        print(f"{'swap':>6} {'dwords':>7} {'draws':>6} {'resol':>6} {'binM':>5} "
              f"{'binS':>5} {'pred':>5} {'regs':>7} {'msaa':>5} {'pitch':>6}")
        for r in active_rows:
            print(f"{r[1]:>6} {r[2]:>7} {r[3]:>6} {r[4]:>6} {r[5]:>5} {r[6]:>5} "
                  f"{r[7]:>5} {r[8]:>7} {r[10]:>5} {r[11]:>6}")
        n = len(active_rows)
        avg = lambda i: sum(r[i] for r in active_rows) / n
        print()
        print(f"MEDIA HOSPITAL 3D ({n} ventanas): draws={avg(3):.1f} resolves={avg(4):.1f} "
              f"bin_mask={avg(5):.1f} bin_select={avg(6):.1f} pred={avg(7):.1f} "
              f"regs={avg(8):.0f} dwords={avg(2):.0f} msaa={avg(10):.1f} pitch={avg(11):.0f}")

    # Extraer telemetria XDK_SHADOW
    shadow_lines = []
    with open(log, encoding="utf-8", errors="replace") as f:
        for line in f:
            if "[XDK_SHADOW]" in line:
                shadow_lines.append(line.strip())
    if shadow_lines:
        print("\n--- Telemetria [XDK_SHADOW] ---")
        for sl in shadow_lines[-6:]:
            print(" ", sl)

    # Extraer metricas de FRAME durante el gameplay
    frame_lines = []
    with open(log, encoding="utf-8", errors="replace") as f:
        for line in f:
            if "neverdead: [FRAME]" in line:
                frame_lines.append(line.strip())
    if frame_lines:
        print("\n--- Telemetria [FRAME] en gameplay ---")
        for fl in frame_lines[-6:]:
            print(" ", fl)
    return 0


if __name__ == "__main__":
    sys.exit(main())
