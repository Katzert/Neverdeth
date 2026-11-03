#!/usr/bin/env python3
"""Mide GPU real (nvidia-smi) + FPS en la escena del hospital para 3 casos.

Caso 1: 30 FPS (defecto) + Perfil Bajo Consumo (16 flags OFF)
Caso 2: 30 FPS (defecto) + Calidad Original (todo ON)
Caso 3: Ilimitado + Calidad Original

Uso: python tools/bench_gpu.py 1|2|3
"""
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

u32 = ctypes.windll.user32
u32.SetProcessDPIAware()
WM_KEYDOWN, WM_KEYUP, VK_F1 = 0x0100, 0x0101, 0x70
WM_COMMAND, CBN_SELCHANGE = 0x0111, 0x0001
BM_CLICK, CB_SETCURSEL = 0x00F5, 0x014E
ID_COMBO_FPS, ID_PRESET_LOW, ID_PRESET_HIGH, ID_CLOSE = 1002, 1032, 1033, 1030


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
        r = wt.RECT()
        u32.GetClientRect(h, ctypes.byref(r))
        a = (r.right - r.left) * (r.bottom - r.top)
        if a > area:
            area, best = a, h
        return True

    u32.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)(cb), 0)
    return best


def open_menu(hwnd):
    u32.PostMessageW(hwnd, WM_KEYDOWN, VK_F1, 0)
    time.sleep(0.05)
    u32.PostMessageW(hwnd, WM_KEYUP, VK_F1, 0)
    for _ in range(20):
        time.sleep(0.3)
        cfg = u32.FindWindowW("NeverDeadConfigClass", None)
        if cfg and u32.IsWindowVisible(cfg):
            return cfg
    return None


def click(cfg, cid):
    h = u32.GetDlgItem(cfg, cid)
    if not h:
        print(f"AVISO: control {cid} no encontrado")
        return False
    u32.SendMessageW(h, BM_CLICK, 0, 0)
    time.sleep(0.4)
    return True


def set_fps_combo(cfg, index):
    h = u32.GetDlgItem(cfg, ID_COMBO_FPS)
    if not h:
        print("AVISO: combo FPS no encontrado")
        return False
    u32.SendMessageW(h, CB_SETCURSEL, index, 0)
    # notificar al padre como si el usuario hubiera elegido
    u32.SendMessageW(cfg, WM_COMMAND, (CBN_SELCHANGE << 16) | ID_COMBO_FPS, h)
    time.sleep(0.4)
    return True


def smi_sample():
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,utilization.memory,power.draw,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5).stdout.strip().splitlines()[0]
    except Exception as e:  # noqa: BLE001
        print("nvidia-smi fallo:", e)
        return None
    parts = [p.strip().replace(",", ".") for p in out.split(",")]
    try:
        return tuple(float(p) for p in parts[:4])
    except ValueError:
        return None


def main():
    case = sys.argv[1] if len(sys.argv) > 1 else "1"
    cfg_map = {
        "1": ("30 FPS + Bajo Consumo", 30, "low"),
        "2": ("30 FPS + Calidad Original", 30, "high"),
        "3": ("Ilimitado + Calidad Original", 0, "high"),
    }
    label, fps, preset = cfg_map[case]

    env = dict(os.environ)
    env["ND_DIAG"] = "1"
    before = set(os.listdir(LOGDIR)) if os.path.isdir(LOGDIR) else set()
    proc = subprocess.Popen([EXE, "--game_data_root", "assets", "--cache_path", "cache"],
                            cwd=ROOT, env=env)
    t0 = time.time()
    hwnd = None
    while time.time() - t0 < 25 and hwnd is None:
        time.sleep(1)
        hwnd = game_window(proc.pid)
    if not hwnd:
        print("ERROR: sin ventana")
        proc.terminate()
        return 1
    print(f"[{case}] {label} | ventana en {time.time()-t0:.0f}s, esperando gameplay (45 s)")
    while time.time() - t0 < 45:
        time.sleep(1)

    cfg = open_menu(hwnd)
    if not cfg:
        print("ERROR: menu no abierto")
        proc.terminate()
        return 1
    if fps == 0:
        set_fps_combo(cfg, 0)   # Ilimitado
    else:
        set_fps_combo(cfg, 2)   # 30 FPS
    click(cfg, ID_PRESET_LOW if preset == "low" else ID_PRESET_HIGH)
    click(cfg, ID_CLOSE)
    time.sleep(3.0)

    # log de esta corrida
    log = None
    for _ in range(10):
        news = sorted((set(os.listdir(LOGDIR)) if os.path.isdir(LOGDIR) else set()) - before)
        if news:
            log = os.path.join(LOGDIR, news[-1])
            break
        time.sleep(1)

    gpu, mem, watt, temp = [], [], [], []
    t_start = dt.datetime.now()
    for _ in range(30):  # 15 s a 500 ms
        s = smi_sample()
        if s:
            gpu.append(s[0]); mem.append(s[1]); watt.append(s[2]); temp.append(s[3])
        time.sleep(0.5)
    t_end = dt.datetime.now()

    def avg(v):
        return sum(v) / len(v) if v else float("nan")

    rx = re.compile(r"\[FRAME\] (\d+) frames avg=([\d.]+) ms \(([\d.]+) FPS\)")
    frames, ms = [], []
    if log and os.path.exists(log):
        with open(log, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = rx.search(line)
                if not m:
                    continue
                ts = re.search(r"\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+)\]", line)
                if not ts:
                    continue
                t = dt.datetime.strptime(ts.group(1), "%Y-%m-%d %H:%M:%S.%f")
                if t_start <= t <= t_end:
                    frames.append(int(m.group(1)))
                    ms.append(float(m.group(2)))

    avg_ms = sum(ms) / len(ms) if ms else float("nan")
    print(f"[{case}] {label}")
    print(f"    GPU  : {avg(gpu):5.1f} %  (mem {avg(mem):5.1f} %)")
    print(f"    Watt : {avg(watt):5.1f} W  (temp {avg(temp):4.1f} C)")
    print(f"    Frame: {avg_ms:6.2f} ms  -> {1000.0/avg_ms if avg_ms == avg_ms else float('nan'):5.1f} FPS")
    print(f"    muestras: {len(gpu)} smi / {len(ms)} ventanas [FRAME]")

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
