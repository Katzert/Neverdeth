#!/usr/bin/env python3
"""
Measures PM4 ring buffer telemetry only during visually confirmed 3D gameplay.

Samples the game window every 2 s and computes the fraction of non-black
pixels (title/loading screens are ~40-45%, active 3D gameplay is >85%).
Once 3 consecutive samples exceed the threshold, a proof screenshot is saved
and the run continues to accumulate [RING] 60-frame windows for reporting.

Usage:
    python tools/bench_ring.py [--build-dir build_clang] [--threshold 0.80] [--max-wait 180]
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

u32 = ctypes.windll.user32
g32 = ctypes.windll.gdi32

VK_RETURN = 0x0D
VK_SPACE = 0x20
VK_A = 0x41
SW_RESTORE = 9
WM_CLOSE = 0x0010


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class BIH(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


ENUMWINDOWS_PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

u32.SetProcessDPIAware.argtypes = []
u32.SetProcessDPIAware.restype = wt.BOOL
u32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
u32.GetWindowThreadProcessId.restype = wt.DWORD
u32.IsWindowVisible.argtypes = [wt.HWND]
u32.IsWindowVisible.restype = wt.BOOL
u32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(RECT)]
u32.GetClientRect.restype = wt.BOOL
u32.EnumWindows.argtypes = [ENUMWINDOWS_PROC, wt.LPARAM]
u32.EnumWindows.restype = wt.BOOL
u32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
u32.ShowWindow.restype = wt.BOOL
u32.BringWindowToTop.argtypes = [wt.HWND]
u32.BringWindowToTop.restype = wt.BOOL
u32.SetForegroundWindow.argtypes = [wt.HWND]
u32.SetForegroundWindow.restype = wt.BOOL
u32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
u32.PostMessageW.restype = wt.BOOL
u32.keybd_event.argtypes = [wt.BYTE, wt.BYTE, wt.DWORD, ctypes.c_size_t]
u32.keybd_event.restype = None
u32.GetDC.argtypes = [wt.HWND]
u32.GetDC.restype = wt.HDC
u32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
u32.ReleaseDC.restype = ctypes.c_int
u32.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]
u32.PrintWindow.restype = wt.BOOL

g32.CreateCompatibleDC.argtypes = [wt.HDC]
g32.CreateCompatibleDC.restype = wt.HDC
g32.DeleteDC.argtypes = [wt.HDC]
g32.DeleteDC.restype = wt.BOOL
g32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
g32.CreateCompatibleBitmap.restype = wt.HBITMAP
g32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
g32.SelectObject.restype = wt.HGDIOBJ
g32.DeleteObject.argtypes = [wt.HGDIOBJ]
g32.DeleteObject.restype = wt.BOOL
g32.GetDIBits.argtypes = [
    wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, wt.LPVOID, ctypes.POINTER(BIH), wt.UINT
]
g32.GetDIBits.restype = ctypes.c_int

u32.SetProcessDPIAware()


def resolve_build_dir(arg: str) -> str:
    if os.path.isabs(arg):
        return os.path.normpath(arg)
    return os.path.normpath(os.path.join(ROOT, arg))


def find_hwnd(pid: int):
    hits = []

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

    u32.EnumWindows(ENUMWINDOWS_PROC(cb), 0)
    return hits[0] if hits else None


def close_process(proc: subprocess.Popen, hwnd_handle: int | None = None) -> None:
    if hwnd_handle:
        try:
            u32.PostMessageW(hwnd_handle, WM_CLOSE, 0, 0)
            proc.wait(timeout=8)
            return
        except Exception:
            pass
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def press_enter(hwnd: int) -> None:
    try:
        u32.ShowWindow(hwnd, SW_RESTORE)
        u32.BringWindowToTop(hwnd)
        u32.SetForegroundWindow(hwnd)
        for vk, scan in ((VK_RETURN, 0x001C), (VK_SPACE, 0x0039), (VK_A, 0x001E)):
            time.sleep(0.05)
            u32.PostMessageW(hwnd, 0x0100, vk, (scan << 16) | 0x0001)  # WM_KEYDOWN
            u32.keybd_event(vk, 0, 0, 0)
            time.sleep(0.05)
            u32.PostMessageW(hwnd, 0x0101, vk, 0xC0000001 | (scan << 16))  # WM_KEYUP
            u32.keybd_event(vk, 0, 2, 0)
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


def save_proof(raw: bytes, w: int, h: int, bmp_path: str, png_path: str) -> None:
    with open(bmp_path, "wb") as f:
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
        im.convert("RGB").save(png_path)
    except Exception:
        pass


RING_RE = re.compile(
    r"\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+)\].*\[RING\] swap #(\d+) \| "
    r"(\d+) dwords \| draws=(\d+) resolves=(\d+) bin_mask=(\d+) bin_select=(\d+) "
    r"pred=(\d+) regs=(\d+) swaps=(\d+) \| msaa=(\d+) pitch=(\d+) "
    r"modecontrol=0x([0-9A-Fa-f]+) surface=0x([0-9A-Fa-f]+)"
)


def main() -> int:
    ap = argparse.ArgumentParser(description="Measure Neverdeth PM4 ring telemetry in 3D gameplay.")
    ap.add_argument(
        "--build-dir",
        default="build_clang",
        help="Build directory containing Neverdeth.exe (default: build_clang)",
    )
    ap.add_argument("--threshold", type=float, default=0.80)
    ap.add_argument("--max-wait", type=float, default=180.0)
    ap.add_argument("--duration", type=float, default=22.0)
    ap.add_argument("--filter", type=str, default="0")
    ap.add_argument("--ring", type=str, default="1")
    ap.add_argument("--throttle", type=str, default="1")
    args = ap.parse_args()

    build_dir = resolve_build_dir(args.build_dir)
    exe = os.path.join(build_dir, "Neverdeth.exe")
    if not os.path.exists(exe):
        print(f"ERROR: Neverdeth.exe not found at {exe}", file=sys.stderr)
        return 1
    logdir = os.path.join(build_dir, "logs")
    proof_bmp = os.path.join(build_dir, "ring_gameplay_proof.bmp")
    proof_png = os.path.join(build_dir, "ring_gameplay_proof.png")

    env = dict(os.environ)
    env["ND_RING"] = args.ring
    env["ND_DIAG"] = "1"
    env["ND_XDK_FILTER"] = args.filter
    env["ND_GUEST_THROTTLE"] = args.throttle
    env["ND_AUTO_NAV"] = "1"

    before = set(os.listdir(logdir)) if os.path.isdir(logdir) else set()
    proc = subprocess.Popen(
        [exe, "--game_data_root", "assets", "--cache_path", "cache"],
        cwd=ROOT,
        env=env,
    )
    print(f"PID={proc.pid}; waiting for game window...")
    hwnd = None
    for _ in range(60):
        time.sleep(0.5)
        hwnd = find_hwnd(proc.pid)
        if hwnd:
            break
    if not hwnd:
        close_process(proc)
        print("ERROR: game window not found")
        return 1
    w, h = hwnd[1], hwnd[2]
    print(f"window {w}x{h}")

    streak = 0
    gameplay_at = None
    last_press = 0.0
    t0 = time.time()
    while time.time() - t0 < args.max_wait:
        raw = grab_raw(*hwnd)
        frac = non_black(raw)
        tag = "GAMEPLAY?" if frac > args.threshold else "menu/loading"
        print(f"  t={time.time() - t0:5.1f}s  non-black={frac * 100:5.1f}%  {tag}")
        if frac > args.threshold:
            streak += 1
            if streak >= 3:
                gameplay_at = dt.datetime.now()
                save_proof(raw, w, h, proof_bmp, proof_png)
                print(
                    f"  >> 3D gameplay confirmed ({frac * 100:.1f}% non-black) "
                    f"at {gameplay_at:%H:%M:%S}; screenshot saved to {proof_bmp}"
                )
                break
        else:
            streak = 0
            cur_t = time.time()
            if cur_t - t0 >= 4.0 and cur_t - last_press >= 3.0:
                press_enter(hwnd[0])
                last_press = cur_t
        time.sleep(2.0)

    if gameplay_at is None:
        close_process(proc, hwnd[0])
        print("ERROR: 3D gameplay not detected within max-wait")
        return 1

    print(f"  measuring {args.duration:.0f} s of ring telemetry in 3D gameplay...")
    time.sleep(args.duration)
    close_process(proc, hwnd[0])

    after = set(os.listdir(logdir)) if os.path.isdir(logdir) else set()
    new = sorted(after - before)
    if not new:
        print("ERROR: no new log file produced")
        return 1
    log = os.path.join(logdir, new[-1])
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
            rows.append((
                ts, int(m.group(2)), int(m.group(3)), int(m.group(4)),
                int(m.group(5)), int(m.group(6)), int(m.group(7)),
                int(m.group(8)), int(m.group(9)), int(m.group(10)),
                int(m.group(11)), int(m.group(12)), m.group(13),
            ))

    print()
    print(f"Total [RING] windows after confirmation: {len(rows)}")
    # Filter strictly to active 3D scene windows (>400 draws, >200k dwords)
    gameplay_rows = [r for r in rows if r[3] >= 400 and r[2] >= 200000]
    print(f"Verified 3D gameplay [RING] windows (draws>=400, dwords>=200k): {len(gameplay_rows)}")
    active_rows = gameplay_rows if gameplay_rows else rows

    if active_rows:
        print(
            f"{'swap':>6} {'dwords':>7} {'draws':>6} {'resol':>6} {'binM':>5} "
            f"{'binS':>5} {'pred':>5} {'regs':>7} {'msaa':>5} {'pitch':>6}"
        )
        for r in active_rows:
            print(
                f"{r[1]:>6} {r[2]:>7} {r[3]:>6} {r[4]:>6} {r[5]:>5} {r[6]:>5} "
                f"{r[7]:>5} {r[8]:>7} {r[10]:>5} {r[11]:>6}"
            )
        n = len(active_rows)
        avg = lambda i: sum(r[i] for r in active_rows) / n
        print()
        print(
            f"3D GAMEPLAY AVERAGE ({n} windows): draws={avg(3):.1f} resolves={avg(4):.1f} "
            f"bin_mask={avg(5):.1f} bin_select={avg(6):.1f} pred={avg(7):.1f} "
            f"regs={avg(8):.0f} dwords={avg(2):.0f} msaa={avg(10):.1f} pitch={avg(11):.0f}"
        )

    shadow_lines = []
    frame_lines = []
    with open(log, encoding="utf-8", errors="replace") as f:
        for line in f:
            if "[XDK_SHADOW]" in line:
                shadow_lines.append(line.strip())
            if "neverdead: [FRAME]" in line:
                frame_lines.append(line.strip())

    if shadow_lines:
        print("\n--- [XDK_SHADOW] Telemetry ---")
        for sl in shadow_lines[-6:]:
            print(" ", sl)

    if frame_lines:
        print("\n--- [FRAME] Gameplay Telemetry ---")
        for fl in frame_lines[-6:]:
            print(" ", fl)
    return 0


if __name__ == "__main__":
    sys.exit(main())
