#!/usr/bin/env python3
"""Harness de sesion para neverdead: captura frames, inyecta teclas y lee globals.

Uso:
    python tools/nd_session.py <script>

<script> es una lista separada por ';' de acciones:
    w<segundos>       esperar
    k<KEY>            pulsar tecla (SendInput, down+up)
    h<KEY>            mantener tecla (down, sin up)
    u<KEY>            soltar tecla
    c<etiqueta>       capturar frame a shots/<etiqueta>.png
    s                 volcar globals del guest
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.environ.get("ND_EXE") or os.path.join(ROOT, "build_clang", "neverdead.exe")
CWD = os.path.join(ROOT, "build_clang")
ASSETS = os.path.join(ROOT, "assets")
SHOTS = os.path.join(ROOT, "build_clang", "shots")
GUEST_BASE = 0x200000000

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
k32 = ctypes.windll.kernel32

WATCH = [
    ("eng@82B312F8", 0x82B312F8, "u32"),
    ("sub@82B312FC", 0x82B312FC, "u32"),
    ("done@82B312DE", 0x82B312DE, "u8"),
    ("fe@82B32084", 0x82B32084, "u32"),
    ("f8318@82B3207E", 0x82B3207E, "u8"),
    ("f8336@82B32090", 0x82B32090, "u8"),
    ("f8338@82B32092", 0x82B32092, "u8"),
    ("obj8328@82B32088", 0x82B32088, "u32"),
    ("t8352@82B320A0", 0x82B320A0, "f32"),
    ("g8072@82B31F88", 0x82B31F88, "u32"),
    ("g8076@82B31F8C", 0x82B31F8C, "u32"),
    ("g8080@82B31F90", 0x82B31F90, "u32"),
    ("g18396@82B347DC", 0x82B347DC, "u32"),
    ("g18828@82B3498C", 0x82B3498C, "u32"),
]

KEYS = {
    "ENTER": 0x0D, "SPACE": 0x20, "ESC": 0x1B, "BACK": 0x08, "TAB": 0x09,
    "A": 0x41, "B": 0x42, "C": 0x43, "D": 0x44, "E": 0x45, "F": 0x46,
    "G": 0x47, "H": 0x48, "I": 0x49, "J": 0x4A, "K": 0x4B, "L": 0x4C,
    "M": 0x4D, "N": 0x4E, "O": 0x4F, "P": 0x50, "Q": 0x51, "R": 0x52,
    "S": 0x53, "T": 0x54, "U": 0x55, "V": 0x56, "W": 0x57, "X": 0x58,
    "Y": 0x59, "Z": 0x5A, "F1": 0x70, "UP": 0x26, "DOWN": 0x28,
    "LEFT": 0x25, "RIGHT": 0x27,
}

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.POINTER(wt.ULONG))]


class INPUT(ctypes.Structure):
    class _I(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT)]
    _anonymous_ = ("i",)
    _fields_ = [("type", wt.DWORD), ("i", _I)]


def key_event(vk, up):
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.ki.wVk = vk
    inp.ki.wScan = user32.MapVirtualKeyW(vk, 0)
    inp.ki.dwFlags = KEYEVENTF_KEYUP if up else 0
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def find_window():
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, lparam):
        n = user32.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if "neverdead" in buf.value:
                found.append((hwnd, buf.value))
        return True

    user32.EnumWindows(cb, 0)
    return found[0] if found else (None, None)


def capture(hwnd, path):
    rect = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    w, h = rect.right, rect.bottom
    if w <= 0 or h <= 0:
        return False
    hdc = user32.GetDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    ok = user32.PrintWindow(hwnd, mem, 2)  # PW_RENDERFULLCONTENT
    if not ok:
        ok = gdi32.BitBlt(mem, 0, 0, w, h, hdc, 0, 0, 0x00CC0020)
    gdi32.GdiFlush()

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                    ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                    ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                    ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                    ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]

    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(bi)
    bi.biWidth = w
    bi.biHeight = -h
    bi.biPlanes = 1
    bi.biBitCount = 32
    bi.biCompression = 0
    buf = ctypes.create_string_buffer(w * h * 4)
    got = gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    if not got:
        return False
    from PIL import Image
    im = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
    im.save(path)
    return True


def read_globals(handle):
    out = []
    for name, va, kind in WATCH:
        n = 1 if kind == "u8" else 4
        buf = (ctypes.c_ubyte * n)()
        got = ctypes.c_size_t()
        if not k32.ReadProcessMemory(handle, ctypes.c_void_p(GUEST_BASE + va), buf, n, ctypes.byref(got)):
            out.append(f"{name}=??")
            continue
        if kind == "u8":
            out.append(f"{name}={buf[0]}")
        else:
            raw = bytes(buf)
            if kind == "f32":
                out.append(f"{name}={ctypes.c_float.from_buffer_copy(raw).value:.3f}")
            else:
                out.append(f"{name}=0x{int.from_bytes(raw, 'little'):08X}")
    return "  ".join(out)


def main():
    script = sys.argv[1] if len(sys.argv) > 1 else "w12;kENTER;w2;c"
    os.makedirs(SHOTS, exist_ok=True)
    p = subprocess.Popen([EXE, "--game_data_root", ASSETS], cwd=CWD,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    handle = k32.OpenProcess(0x0410, False, p.pid)
    print(f"PID {p.pid}", flush=True)
    hwnd = None
    t0 = time.time()

    def focus():
        nonlocal hwnd
        hwnd = hwnd or find_window()[0]
        if hwnd:
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
            user32.BringWindowToTop(hwnd)
        return hwnd
    try:
        for step in script.split(";"):
            step = step.strip()
            if not step:
                continue
            op, arg = step[0], step[1:]
            if op == "w":
                time.sleep(float(arg))
            elif op == "k":
                focus()
                time.sleep(0.15)
                vk = KEYS[arg.upper()]
                key_event(vk, False)
                time.sleep(0.06)
                key_event(vk, True)
                print(f"[{time.time()-t0:5.1f}s] key {arg}", flush=True)
            elif op == "h":
                focus()
                key_event(KEYS[arg.upper()], False)
                print(f"[{time.time()-t0:5.1f}s] hold {arg}", flush=True)
            elif op == "u":
                key_event(KEYS[arg.upper()], True)
                print(f"[{time.time()-t0:5.1f}s] release {arg}", flush=True)
            elif op == "c":
                hwnd = hwnd or find_window()[0]
                path = os.path.join(SHOTS, f"{arg or int(time.time()*1000):}.png")
                print(f"[{time.time()-t0:5.1f}s] capture {path} hwnd={hwnd}", flush=True)
                if hwnd:
                    print("  ok" if capture(hwnd, path) else "  FAILED", flush=True)
            elif op == "s":
                print(f"[{time.time()-t0:5.1f}s] {read_globals(handle)}", flush=True)
    finally:
        if p.poll() is None:
            p.kill()
        p.wait()
        k32.CloseHandle(handle)
        print("done")


if __name__ == "__main__":
    main()
