"""Deterministic menu test for neverdead.exe.

Launches the game, focuses its window, sends Enter (START) at a fixed time,
captures screenshots at fixed offsets and kills the process.

Usage:
  python tools/nd_menu_test.py --enter-at 6 --shots 8,12,16,20 --out build_clang/shots

No controller emulation: keyboard Enter is what produced the 17:25 menu capture
(file name 04_after_enter_6s.png).
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time
from datetime import datetime

from PIL import Image, ImageGrab

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

VK_RETURN = 0x0D
KEYEVENTF_KEYUP = 0x0002
SW_RESTORE = 9
HWND_TOPMOST = -1
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040


def find_window(pid: int) -> int:
    """Ventana visible mas grande del proceso del juego.

    Antes se buscaba por titulo ("neverdead") y el Explorador de Windows con la
    carpeta neverdead_project abierta ganaba la busqueda: se enfocaba y se
    capturaba el Explorador, no el juego, y el Enter se enviaba a Explorer.
    """
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        owner = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value != pid:
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        class RECT(ctypes.Structure):
            _fields_ = [("left", wt.LONG), ("top", wt.LONG),
                        ("right", wt.LONG), ("bottom", wt.LONG)]
        r = RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        area = (r.right - r.left) * (r.bottom - r.top)
        found.append((area, hwnd))
        return True

    user32.EnumWindows(cb, 0)
    found.sort(reverse=True)
    return found[0][1] if found else 0


def focus(hwnd: int) -> None:
    user32.ShowWindow(hwnd, SW_RESTORE)
    # Windows bloquea SetForegroundWindow a procesos en segundo plano. El truco
    # estándar es adjuntar el hilo de entrada al de la ventana en primer plano y
    # pulsar/soltar ALT para desbloquear el permiso de foreground.
    user32.keybd_event(0x12, 0, 0, 0)
    user32.keybd_event(0x12, 0, 2, 0)
    fg = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    cur_thread = kernel32.GetCurrentThreadId()
    attached = False
    if fg_thread and fg_thread != cur_thread:
        attached = bool(user32.AttachThreadInput(cur_thread, fg_thread, True))
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    user32.SetFocus(hwnd)
    # Sin esto, una ventana del Explorador abierta encima del juego fullscreen
    # aparece en las capturas: el overlay mide el juego pero el PNG muestra el
    # escritorio. La ventana del juego se cierra al terminar la corrida.
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
    if attached:
        user32.AttachThreadInput(cur_thread, fg_thread, False)
    time.sleep(0.25)


def grab_window(hwnd: int, path: str) -> bool:
    """Captura la ventana con PrintWindow(PW_RENDERFULLCONTENT).

    ImageGrab.grab() copia el escritorio compuesto: si otra ventana tapa al
    juego (p. ej. el Explorador abierto encima de un juego borderless), el PNG
    sale con el escritorio y el overlay del juego, no la escena. PrintWindow
    pide el contenido de la ventana a DWM y funciona aunque este tapada.
    """
    class RECT(ctypes.Structure):
        _fields_ = [("left", wt.LONG), ("top", wt.LONG),
                    ("right", wt.LONG), ("bottom", wt.LONG)]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                    ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                    ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                    ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                    ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]

    rect = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return False
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w <= 0 or h <= 0:
        return False

    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    ok = bool(user32.PrintWindow(hwnd, mem, 2))  # PW_RENDERFULLCONTENT

    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(bmi)
    bmi.biWidth = w
    bmi.biHeight = -h  # top-down
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bmi.biCompression = 0  # BI_RGB
    buf = ctypes.create_string_buffer(w * h * 4)
    got = gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bmi), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    if not ok or got == 0:
        return False

    im = Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)
    if im.convert("L").getextrema()[1] == 0:  # todo negro: DWM no dio el frame
        return False
    im.save(path)
    return True


def press_enter(hwnd: int = 0) -> None:
    if hwnd:
        user32.PostMessageW(hwnd, 0x0100, VK_RETURN, 0x001C0001)  # WM_KEYDOWN
    user32.keybd_event(VK_RETURN, 0, 0, 0)
    time.sleep(0.08)
    if hwnd:
        user32.PostMessageW(hwnd, 0x0101, VK_RETURN, 0xC01C0001)  # WM_KEYUP
    user32.keybd_event(VK_RETURN, 0, KEYEVENTF_KEYUP, 0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default="build_clang/neverdead.exe")
    ap.add_argument("--assets", default="assets")
    ap.add_argument("--enter-at", default="6", help="segundos separados por coma")
    ap.add_argument("--shots", default="8,12,16,20")
    ap.add_argument("--out", default="build_clang/shots")
    ap.add_argument("--timeout", type=float, default=30.0)
    args = ap.parse_args()

    shots = [float(x) for x in args.shots.split(",") if x.strip()]
    enters = sorted(float(x) for x in args.enter_at.split(",") if x.strip())
    os.makedirs(args.out, exist_ok=True)
    stamp = datetime.now().strftime("%H%M%S")

    env = os.environ.copy()
    assets = os.path.abspath(args.assets)
    proc = subprocess.Popen(
        [os.path.abspath(args.exe), "--game_data_root", assets],
        cwd=os.path.dirname(os.path.abspath(args.exe)) or None,
        env=env,
    )
    t0 = time.time()
    hwnd = 0
    while time.time() - t0 < 10 and not hwnd:
        hwnd = find_window(proc.pid)
        time.sleep(0.2)

    if not hwnd:
        print("WINDOW_NOT_FOUND", flush=True)
    else:
        print(f"WINDOW 0x{hwnd:X}", flush=True)

    entered = 0
    shot_i = 0
    while time.time() - t0 < args.timeout and proc.poll() is None:
        elapsed = time.time() - t0
        if entered < len(enters) and elapsed >= enters[entered]:
            if hwnd:
                focus(hwnd)
            fg = user32.GetForegroundWindow()
            press_enter()
            print(f"ENTER at {elapsed:.2f}s (#{entered + 1}) fg=0x{fg:X} "
                  f"hwnd=0x{hwnd:X} enfocado={fg == hwnd}", flush=True)
            entered += 1
        if shot_i < len(shots) and elapsed >= shots[shot_i]:
            if hwnd:
                focus(hwnd)
            path = os.path.join(args.out, f"run_{stamp}_{shots[shot_i]:04.0f}s.png")
            try:
                if not (hwnd and grab_window(hwnd, path)):
                    ImageGrab.grab().save(path)
                    print(f"SHOT {path} (grab escritorio)", flush=True)
                else:
                    print(f"SHOT {path}", flush=True)
            except Exception as exc:  # pragma: no cover
                print(f"SHOT_FAIL {exc}", flush=True)
            shot_i += 1
        time.sleep(0.05)

    rc = proc.poll()
    if rc is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        rc = proc.returncode
    print(f"EXIT {rc}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
