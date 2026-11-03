#!/usr/bin/env python3
"""Abre el menu F1, lo captura en BMP y verifica que F1 y ESC lo cierran.

Salida: dist/menu_f1_alpha_preview.bmp (se convierte a .png aparte)
"""
import ctypes
import ctypes.wintypes as wt
import os
import struct
import subprocess
import sys
import time

ROOT = r"C:\Users\katze\Downloads\_Proyectos_y_Backups\neverdead_project"
EXE = os.path.join(ROOT, "build_clang", "neverdead.exe")
OUT = os.path.join(ROOT, "dist", "menu_f1_alpha_preview.bmp")

u32 = ctypes.windll.user32
g32 = ctypes.windll.gdi32
u32.SetProcessDPIAware()
WM_KEYDOWN, WM_KEYUP, VK_F1, VK_ESCAPE = 0x0100, 0x0101, 0x70, 0x1B


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class BIH(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


def game_window(pid):
    best, area = None, 0

    def cb(h, lp):
        nonlocal best, area
        if not u32.IsWindowVisible(h):
            return True
        owner = wt.DWORD()
        u32.GetWindowThreadProcessId(h, ctypes.byref(owner))
        if owner.value != pid:
            return True
        cls = ctypes.create_unicode_buffer(64)
        u32.GetClassNameW(h, cls, 64)
        if cls.value == "NeverDeadConfigClass":
            return True
        r = RECT()
        u32.GetClientRect(h, ctypes.byref(r))
        a = (r.right - r.left) * (r.bottom - r.top)
        if a > area:
            area, best = a, h
        return True

    u32.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)(cb), 0)
    return best


def tap(hwnd, vk):
    u32.PostMessageW(hwnd, WM_KEYDOWN, vk, 0)
    time.sleep(0.05)
    u32.PostMessageW(hwnd, WM_KEYUP, vk, 0)


def capture(hwnd, path):
    r = RECT()
    u32.GetClientRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    hdc = u32.GetDC(hwnd)
    mdc = g32.CreateCompatibleDC(hdc)
    bmp = g32.CreateCompatibleBitmap(hdc, w, h)
    g32.SelectObject(mdc, bmp)
    u32.PrintWindow(hwnd, mdc, 2)  # PW_RENDERFULLCONTENT
    bi = BIH()
    bi.biSize = ctypes.sizeof(BIH)
    bi.biWidth, bi.biHeight = w, -h
    bi.biPlanes, bi.biBitCount = 1, 32
    buf = ctypes.create_string_buffer(w * 4 * h)
    g32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    g32.DeleteObject(bmp)
    g32.DeleteDC(mdc)
    u32.ReleaseDC(hwnd, hdc)
    row = w * 4
    with open(path, "wb") as f:
        f.write(b"BM")
        f.write(struct.pack("<IHHI", 14 + 40 + row * h, 0, 0, 54))
        f.write(struct.pack("<IiiHHIIiiII", 40, w, -h, 1, 32, 0, row * h, 2835, 2835, 0, 0))
        f.write(buf.raw)
    return w, h


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    proc = subprocess.Popen([EXE, "--game_data_root", "assets", "--cache_path", "cache"], cwd=ROOT)
    t0 = time.time()
    hwnd = None
    while time.time() - t0 < 25 and hwnd is None:
        time.sleep(1)
        hwnd = game_window(proc.pid)
    if not hwnd:
        print("ERROR: sin ventana de juego")
        proc.terminate()
        return 1
    print(f"juego listo en {time.time()-t0:.0f}s, hwnd={hwnd}")
    time.sleep(6)

    # 1) F1 abre
    tap(hwnd, VK_F1)
    cfg = None
    for _ in range(20):
        time.sleep(0.3)
        cfg = u32.FindWindowW("NeverDeadConfigClass", None)
        if cfg:
            break
    if not cfg:
        print("FALLO: F1 no abrio el menu")
        proc.terminate()
        return 1
    print(f"F1 abre: OK (hwnd={cfg}, visible={bool(u32.IsWindowVisible(cfg))})")
    time.sleep(0.6)
    w, h = capture(cfg, OUT)
    print(f"captura {w}x{h} -> {OUT}")

    # 2) F1 con el foco en el menu lo cierra
    u32.SetForegroundWindow(cfg)
    time.sleep(0.4)
    tap(cfg, VK_F1)
    time.sleep(0.6)
    closed = not u32.IsWindowVisible(cfg)
    print(f"F1 con foco en el menu -> cerrado: {closed}")

    # 3) F1 reabre y ESC cierra
    u32.SetForegroundWindow(hwnd)
    time.sleep(0.3)
    tap(hwnd, VK_F1)
    time.sleep(0.8)
    reopened = bool(u32.IsWindowVisible(cfg))
    print(f"F1 reabre: {reopened}")
    if reopened:
        u32.SetForegroundWindow(cfg)
        time.sleep(0.4)
        tap(cfg, VK_ESCAPE)
        time.sleep(0.6)
        print(f"ESC cierra: {not u32.IsWindowVisible(cfg)}")

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
