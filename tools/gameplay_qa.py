#!/usr/bin/env python3
"""
Neverdeth - automated gameplay smoke test.

Launches the game in the background with emulated controller navigation,
captures the F10 settings menu and its FPS options, verifies that 3D gameplay
is reached, and records performance/resource telemetry (CPU, RAM, GPU, FPS,
frame times, and log health). Artifacts are written under
<build_dir>/qa_shots/<run>/ and <build_dir>/logs/qa_report_<run>.{json,md}.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import datetime as dt
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
CRASH_DIAG = ROOT / "crash_diag.log"
RUN_ID = dt.datetime.now().strftime("%Y%m%d_%H%M%S")

u32 = ctypes.windll.user32
g32 = ctypes.windll.gdi32
k32 = ctypes.windll.kernel32
psapi = ctypes.windll.psapi
u32.SetProcessDPIAware()

VK_RETURN, VK_SPACE, VK_F10, VK_ESCAPE, VK_A = 0x0D, 0x20, 0x79, 0x1B, 0x41
WM_KEYDOWN, WM_KEYUP, WM_CLOSE = 0x0100, 0x0101, 0x0010
CB_GETCOUNT, CB_GETLBTEXT, CB_GETLBTEXTLEN, CB_SHOWDROPDOWN = 0x0146, 0x0148, 0x0149, 0x014F
IDC_CFG_COMBO_FPS = 1002
SRCCOPY = 0x00CC0020


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class BIH(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wt.DWORD), ("dwHighDateTime", wt.DWORD)]


class PMC(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


# Win64: without explicit prototypes ctypes truncates HWND/HDC to 32 bits.
ENUMWINDOWS_PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
u32.SetProcessDPIAware.argtypes = []
u32.SetProcessDPIAware.restype = wt.BOOL
u32.EnumWindows.argtypes = [ENUMWINDOWS_PROC, wt.LPARAM]
u32.EnumWindows.restype = wt.BOOL
u32.IsWindowVisible.argtypes = [wt.HWND]
u32.IsWindowVisible.restype = wt.BOOL
u32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u32.GetClassNameW.restype = ctypes.c_int
u32.GetSystemMetrics.argtypes = [ctypes.c_int]
u32.GetSystemMetrics.restype = ctypes.c_int
u32.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
u32.SendMessageW.restype = ctypes.c_ssize_t
u32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
u32.GetDlgItem.argtypes = [wt.HWND, ctypes.c_int]
u32.GetDlgItem.restype = wt.HWND
u32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
u32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(RECT)]
u32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(RECT)]
u32.InvalidateRect.argtypes = [wt.HWND, ctypes.c_void_p, wt.BOOL]
u32.GetDC.argtypes = [wt.HWND]
u32.GetDC.restype = wt.HDC
u32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
u32.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]
g32.CreateCompatibleDC.argtypes = [wt.HDC]
g32.CreateCompatibleDC.restype = wt.HDC
g32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
g32.CreateCompatibleBitmap.restype = wt.HBITMAP
g32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
g32.SelectObject.restype = wt.HGDIOBJ
g32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT,
                          ctypes.c_void_p, ctypes.POINTER(BIH), wt.UINT]
g32.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                       wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD]
g32.DeleteObject.argtypes = [wt.HGDIOBJ]
g32.DeleteDC.argtypes = [wt.HDC]
k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k32.OpenProcess.restype = wt.HANDLE
k32.GetProcessTimes.argtypes = [wt.HANDLE, ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME),
                                ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME)]
k32.CloseHandle.argtypes = [wt.HANDLE]
psapi.GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.POINTER(PMC), wt.DWORD]


def _enum_windows():
    out = []

    def cb(hwnd, _):
        out.append(hwnd)
        return True

    u32.EnumWindows(ENUMWINDOWS_PROC(cb), 0)
    return out


def find_hwnd(pid: int):
    for hwnd in _enum_windows():
        owner = wt.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and u32.IsWindowVisible(hwnd):
            r = RECT()
            u32.GetClientRect(hwnd, ctypes.byref(r))
            w, h = r.right - r.left, r.bottom - r.top
            if w > 400 and h > 300:
                return hwnd, w, h
    return None


def find_menu_hwnd(pid: int):
    for hwnd in _enum_windows():
        owner = wt.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            cls = ctypes.create_unicode_buffer(64)
            u32.GetClassNameW(hwnd, cls, 64)
            if "NeverDeadConfigClass" in cls.value:
                return hwnd
    return None


def _dib_to_raw(mdc, bmp, w, h):
    bi = BIH()
    bi.biSize = ctypes.sizeof(BIH)
    bi.biWidth, bi.biHeight = w, -h
    bi.biPlanes, bi.biBitCount, bi.biCompression = 1, 32, 0
    buf = ctypes.create_string_buffer(w * h * 4)
    g32.GetDIBits(mdc, bmp, 0, h, ctypes.cast(buf, ctypes.c_void_p), ctypes.byref(bi), 0)
    g32.DeleteObject(bmp)
    g32.DeleteDC(mdc)
    return buf.raw


def grab_window(hwnd, w, h) -> bytes:
    hdc = u32.GetDC(hwnd)
    mdc = g32.CreateCompatibleDC(hdc)
    bmp = g32.CreateCompatibleBitmap(hdc, w, h)
    g32.SelectObject(mdc, bmp)
    u32.PrintWindow(hwnd, mdc, 2)
    raw = _dib_to_raw(mdc, bmp, w, h)
    u32.ReleaseDC(hwnd, hdc)
    return raw


def grab_screen():
    w = u32.GetSystemMetrics(0)
    h = u32.GetSystemMetrics(1)
    hdc = u32.GetDC(0)
    mdc = g32.CreateCompatibleDC(hdc)
    bmp = g32.CreateCompatibleBitmap(hdc, w, h)
    g32.SelectObject(mdc, bmp)
    g32.BitBlt(mdc, 0, 0, w, h, hdc, 0, 0, SRCCOPY)
    raw = _dib_to_raw(mdc, bmp, w, h)
    u32.ReleaseDC(0, hdc)
    return raw, w, h


def save_png(raw: bytes, w: int, h: int, path: Path):
    Image.frombuffer("RGBA", (w, h), raw, "raw", "BGRA", 0, 1).convert("RGB").save(path)


def non_black(raw: bytes) -> float:
    cnt = nb = 0
    for i in range(0, len(raw) - 4, 4 * 97):
        cnt += 1
        if raw[i] + raw[i + 1] + raw[i + 2] > 30:
            nb += 1
    return nb / max(cnt, 1)


def press_key(hwnd: int, vk: int):
    u32.PostMessageW(hwnd, WM_KEYDOWN, vk, 0)
    time.sleep(0.05)
    u32.PostMessageW(hwnd, WM_KEYUP, vk, 0)


def combo_items(combo_hwnd) -> list[str]:
    count = u32.SendMessageW(combo_hwnd, CB_GETCOUNT, 0, 0)
    items = []
    for i in range(count):
        length = u32.SendMessageW(combo_hwnd, CB_GETLBTEXTLEN, i, 0)
        buf = ctypes.create_unicode_buffer(max(1, length + 1))
        u32.SendMessageW(combo_hwnd, CB_GETLBTEXT, i, ctypes.cast(buf, ctypes.c_void_p).value)
        items.append(buf.value)
    return items


def proc_times(handle):
    c, e, kn, us = FILETIME(), FILETIME(), FILETIME(), FILETIME()
    if not k32.GetProcessTimes(handle, ctypes.byref(c), ctypes.byref(e),
                              ctypes.byref(kn), ctypes.byref(us)):
        return 0

    # FILETIME is already in 100 ns units; divide by 1e7 downstream for seconds.
    def units100ns(ft):
        return (ft.dwHighDateTime << 32) | ft.dwLowDateTime

    return units100ns(kn) + units100ns(us)


def proc_mem(handle):
    pmc = PMC()
    pmc.cb = ctypes.sizeof(PMC)
    if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb):
        return {}
    return {
        "working_set_mb": round(pmc.WorkingSetSize / (1024 ** 2), 1),
        "peak_ws_mb": round(pmc.PeakWorkingSetSize / (1024 ** 2), 1),
        "commit_mb": round(pmc.PagefileUsage / (1024 ** 2), 1),
    }


def gpu_sample():
    try:
        p = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,power.draw,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=6,
        )
        if p.returncode != 0:
            return {}
        vals = [v.strip() for v in p.stdout.strip().splitlines()[0].split(",")]
        return {
            "util_pct": float(vals[0]),
            "vram_mb": float(vals[1]),
            "power_w": float(vals[2]),
            "temp_c": float(vals[3]),
        }
    except Exception:
        return {}


class Telemetry(threading.Thread):
    def __init__(self, pid, interval=1.0):
        super().__init__(daemon=True)
        self.pid = pid
        self.interval = interval
        self.samples = []
        self.stop_flag = threading.Event()
        self.handle = k32.OpenProcess(0x1000, False, pid)
        self.last_cpu = proc_times(self.handle) if self.handle else 0
        self.last_t = time.time()
        self.cores = os.cpu_count() or 1
        self.t0 = self.last_t

    def run(self):
        while not self.stop_flag.wait(self.interval):
            if not self.handle:
                break
            now = time.time()
            cpu = proc_times(self.handle)
            wall = max(now - self.last_t, 1e-6)
            cpu_pct = ((cpu - self.last_cpu) / 1e7) / wall * 100.0 / self.cores
            self.last_cpu, self.last_t = cpu, now
            sample = {"t": round(now - self.t0, 1), "cpu_pct": round(max(cpu_pct, 0.0), 1)}
            sample.update(proc_mem(self.handle))
            sample.update(gpu_sample())
            self.samples.append(sample)

    def start(self):
        self.t0 = time.time()
        super().start()

    def stop(self):
        self.stop_flag.set()
        self.join(timeout=4)
        if self.handle:
            k32.CloseHandle(self.handle)

    def summary(self):
        def stats(key):
            vals = [s[key] for s in self.samples if key in s and isinstance(s[key], (int, float))]
            if not vals:
                return None
            return {"avg": round(sum(vals) / len(vals), 1), "max": round(max(vals), 1), "n": len(vals)}

        return {
            k: stats(k)
            for k in ("cpu_pct", "working_set_mb", "peak_ws_mb", "commit_mb",
                      "util_pct", "vram_mb", "power_w", "temp_c")
        }


def analyze_log(path: Path | None):
    if not path or not path.exists():
        return {"log": None}
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    fps = [float(x) for x in re.findall(r"dxgi_present_fps=([\d.]+)", text)]
    frame_avg = [float(x) for x in re.findall(r"\[FRAME_TIME\] avg=([\d.]+)\s*ms", text)]
    frame_max = [float(x) for x in re.findall(r"\[FRAME_TIME\][^\n]*?max=([\d.]+)\s*ms", text)]
    cpu_ms = [float(x) for x in re.findall(r"\[FRAME_TIME\][^\n]*?cpu=([\d.]+)\s*ms", text)]
    gpu_ms = [float(x) for x in re.findall(r"\[FRAME_TIME\][^\n]*?gpu=([\d.]+)\s*ms", text)]
    return {
        "log": str(path),
        "size_kb": round(path.stat().st_size / 1024, 1),
        "lines": len(lines),
        "errors": sum(1 for l in lines if "[error]" in l.lower() or "FAILED:" in l),
        "warnings": sum(1 for l in lines if "[warning]" in l.lower()),
        "pso_failures": sum(1 for l in lines if "CreateGraphicsPipelineState" in l and "failed" in l.lower()),
        "vertex_fetch_invalid": sum(1 for l in lines if "Vertex fetch constant" in l and "invalid" in l),
        "pm4_rejects": sum(1 for l in lines if "PM4_DRAW_INDX (backend reject" in l),
        "uipaint_spam": sum(1 for l in lines if "UIPaint:" in l),
        "xctd_spam": sum(1 for l in lines if "XFileXctdCompressionInformation" in l),
        "spike_lines": [l.strip() for l in lines if "[FRAME] spike" in l][-8:],
        "auto_nav_a": sum(
            1 for l in lines
            if "[AUTO-NAV]" in l and "Pressing A" in l
        ),
        "auto_nav_events": sum(1 for l in lines if "[AUTO-NAV]" in l),
        "precompile_start": sum(
            1 for l in lines
            if "[PSO-PRECOMPILE]" in l and "Precompiling" in l
        ),
        "precompile_done": sum(
            1 for l in lines
            if "[PSO-PRECOMPILE]" in l and "complete" in l.lower()
        ),
        "gameplay_3d_reached": sum(
            1 for l in lines if "[3D-GAMEPLAY]" in l or "in_3d=1" in l
        ),
        "clean_exit": any("[CLEAN-EXIT]" in l for l in lines),
        "fps_samples": len(fps),
        "fps_avg": round(sum(fps[-20:]) / len(fps[-20:]), 1) if fps else None,
        "fps_max": round(max(fps), 1) if fps else None,
        "frametime_avg_ms": round(sum(frame_avg) / len(frame_avg), 2) if frame_avg else None,
        "frametime_max_ms": round(max(frame_max), 2) if frame_max else None,
        "cpu_ms_avg": round(sum(cpu_ms) / len(cpu_ms), 2) if cpu_ms else None,
        "gpu_ms_avg": round(sum(gpu_ms) / len(gpu_ms), 2) if gpu_ms else None,
        "last_lines": lines[-6:],
    }


def resolve_build_dir(arg: str) -> Path:
    p = Path(arg)
    return p if p.is_absolute() else ROOT / p


def finalize(result: dict, out_json: Path, out_md: Path):
    result["finished"] = dt.datetime.now().isoformat(timespec="seconds")
    out_json.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    la = result.get("log_analysis", {})
    tel = result.get("telemetry", {})
    lines = [
        f"# Automated QA Report - Neverdeth ({result['run_id']})",
        "",
        f"- Started: {result['started']} / Finished: {result['finished']}",
        f"- Executable: {result.get('exe')}",
        f"- PID: {result.get('pid')} / Exit code: {result.get('exit_code')}",
        f"- F10 menu detected: {result.get('menu_detected')}",
        f"- FPS options: {result.get('fps_options')}",
        f"- 3D gameplay (visual): {result.get('gameplay_3d')} (t={result.get('gameplay_t_s')}s)",
        f"- 3D gameplay log markers: {la.get('gameplay_3d_reached')}",
        f"- Emulated A button (log events): {la.get('auto_nav_a')} of {la.get('auto_nav_events')}",
        f"- Log: {la.get('log')} ({la.get('size_kb')} KB, {la.get('lines')} lines)",
        f"- Errors / warnings: {la.get('errors')} / {la.get('warnings')}",
        f"- FPS: samples={la.get('fps_samples')} avg={la.get('fps_avg')} max={la.get('fps_max')}",
        f"- Frame time: avg={la.get('frametime_avg_ms')} ms max={la.get('frametime_max_ms')} ms "
        f"(cpu={la.get('cpu_ms_avg')} ms gpu={la.get('gpu_ms_avg')} ms)",
        f"- PSO precompile: start={la.get('precompile_start')} done={la.get('precompile_done')}",
        f"- Clean exit: {la.get('clean_exit')} / New crash_diag bytes: {result.get('crash_diag_new_bytes')}",
        f"- Resource summary: {json.dumps(tel, ensure_ascii=False)}",
        "",
        "## Screenshots",
        "",
    ]
    for s in result.get("screenshots", []):
        lines.append(f"- {s['name']}: {s['path']} ({s['non_black_pct']}% non-black)")
    lines += ["", "## Events", ""]
    lines += [f"- {e}" for e in result.get("events", [])]
    if result.get("crash_diag_delta"):
        lines += ["", "## crash_diag.log (delta)", "", "```", result["crash_diag_delta"], "```"]
    if la.get("last_lines"):
        lines += ["", "## Last Log Lines", "", "```"] + la["last_lines"] + ["```"]
    out_md.write_text("\n".join(lines), encoding="utf-8")
    print(f"[QA] JSON: {out_json}")
    print(f"[QA] MD  : {out_md}")
    print(
        f"[QA] Summary: gameplay_3d={result.get('gameplay_3d')}, "
        f"exit={result.get('exit_code')}, crash_new={result.get('crash_diag_new_bytes')}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Neverdeth automated gameplay QA smoke test.")
    ap.add_argument(
        "--build-dir",
        default="build_clang",
        help="Build directory containing Neverdeth.exe (default: build_clang)",
    )
    ap.add_argument("--burn", type=float, default=float(os.environ.get("ND_QA_BURN", "45")))
    ap.add_argument("--wait-3d", type=float, default=float(os.environ.get("ND_QA_WAIT3D", "150")))
    args = ap.parse_args()

    build_dir = resolve_build_dir(args.build_dir)
    exe = build_dir / "Neverdeth.exe"
    if not exe.exists():
        print(f"ERROR: Neverdeth.exe not found at {exe}", file=sys.stderr)
        return 2

    logdir = build_dir / "logs"
    shots = build_dir / "qa_shots" / RUN_ID
    out_json = logdir / f"qa_report_{RUN_ID}.json"
    out_md = logdir / f"qa_report_{RUN_ID}.md"

    shots.mkdir(parents=True, exist_ok=True)
    logdir.mkdir(parents=True, exist_ok=True)

    result = {
        "run_id": RUN_ID,
        "started": dt.datetime.now().isoformat(timespec="seconds"),
        "exe": str(exe),
        "shots_dir": str(shots),
        "screenshots": [],
        "errors": [],
        "events": [],
    }

    def event(msg: str):
        stamp = dt.datetime.now().strftime("%H:%M:%S")
        line = f"[{stamp}] {msg}"
        print(line, flush=True)
        result["events"].append(line)

    def shot(name: str, raw: bytes, w: int, h: int):
        p = shots / f"{name}.png"
        save_png(raw, w, h, p)
        pct = round(non_black(raw) * 100, 1)
        result["screenshots"].append({"name": name, "path": str(p), "non_black_pct": pct})
        event(f"screenshot {name}: {p.name} ({pct:.1f}% non-black)")
        return p

    before_logs = {p.name for p in logdir.glob("*.log")}
    crash_before = CRASH_DIAG.stat().st_size if CRASH_DIAG.exists() else 0

    env = dict(os.environ)
    env.update({
        "ND_RING": "1",
        "ND_DIAG": "1",
        "ND_XDK_FILTER": "0",
        "ND_UNLOCK_FPS": "1",
        "ND_AUTO_NAV": "1",
    })
    cmd = [str(exe), "--game_data_root", "assets", "--cache_path", "cache"]
    event(f"launching in background: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, cwd=str(build_dir), env=env)
    result["pid"] = proc.pid
    event(f"PID={proc.pid} (Xbox A button emulated via ND_AUTO_NAV=1)")

    telemetry = Telemetry(proc.pid, 1.0)
    telemetry.start()

    hwnd_info = None
    for _ in range(80):
        if proc.poll() is not None:
            event(f"ERROR: process exited during startup (exit={proc.returncode})")
            break
        hwnd_info = find_hwnd(proc.pid)
        if hwnd_info:
            break
        time.sleep(0.5)

    if hwnd_info is None:
        result["fatals"] = "main window not found / process terminated"
        result["exit_code"] = proc.returncode
        telemetry.stop()
        finalize(result, out_json, out_md)
        return 2

    hwnd, w, h = hwnd_info
    event(f"main window HWND=0x{hwnd:X} {w}x{h}")
    t0 = time.time()

    # Boot / title screen capture
    time.sleep(2.0)
    if proc.poll() is None:
        shot("01_boot_title", grab_window(hwnd, w, h), w, h)

    # F10 settings menu and FPS selector capture (retried later if not yet registered)
    result["menu_detected"] = False
    menu_state = {"ok": False, "retried": False}

    def menu_capture(tag: str) -> bool:
        menu_hwnd = None
        for attempt in range(3):
            press_key(hwnd, VK_F10)
            time.sleep(1.2)
            menu_hwnd = find_menu_hwnd(proc.pid)
            if menu_hwnd:
                break
            event(f"F10 menu not detected ({tag}, attempt {attempt + 1})")
        if not menu_hwnd:
            return False
        result["menu_detected"] = True
        mr = RECT()
        u32.GetWindowRect(menu_hwnd, ctypes.byref(mr))
        mw, mh = mr.right - mr.left, mr.bottom - mr.top
        shot(f"{tag}_menu_f10", grab_window(menu_hwnd, mw, mh), mw, mh)
        combo = u32.GetDlgItem(menu_hwnd, IDC_CFG_COMBO_FPS)
        if combo:
            result["fps_options"] = combo_items(combo)
            event(f"FPS combo options: {result['fps_options']}")
            # Capture the menu with each FPS option selected so all alternatives
            # are visually verified even when BitBlt does not capture dropdown popups.
            sel_before = u32.SendMessageW(combo, 0x0147, 0, 0)  # CB_GETCURSEL
            for i, _label in enumerate(result["fps_options"]):
                u32.SendMessageW(combo, 0x014E, i, 0)  # CB_SETCURSEL
                u32.InvalidateRect(menu_hwnd, None, True)
                time.sleep(0.35)
                shot(f"{tag}_fps_option_{i}", grab_window(menu_hwnd, mw, mh), mw, mh)
            u32.SendMessageW(combo, 0x014E, sel_before, 0)
            u32.InvalidateRect(menu_hwnd, None, True)
            u32.SendMessageW(combo, CB_SHOWDROPDOWN, 1, 0)
            time.sleep(0.7)
            sraw, sw, sh = grab_screen()
            shot(f"{tag}_menu_fps_dropdown", sraw, sw, sh)
            u32.SendMessageW(combo, CB_SHOWDROPDOWN, 0, 0)
        else:
            event("WARN: FPS combo box (id 1002) not found")
        press_key(menu_hwnd, VK_ESCAPE)
        time.sleep(0.5)
        return True

    menu_state["ok"] = menu_capture("02")
    if not menu_state["ok"]:
        result["errors"].append("F10 menu did not open on initial attempt")

    # ND_AUTO_NAV injects XINPUT_GAMEPAD_A; reinforce with keyboard events
    event("waiting for 3D gameplay entry (START -> emulated A)")
    streak = 0
    gameplay = False
    gameplay_t = None
    wait_marks = {15: False, 35: False, 60: False}
    while time.time() - t0 < args.wait_3d:
        if proc.poll() is not None:
            event(f"ERROR: process exited before reaching 3D gameplay (exit={proc.returncode})")
            break
        raw = grab_window(hwnd, w, h)
        frac = non_black(raw)
        elapsed = time.time() - t0
        if int(elapsed) % 10 == 0:
            event(f"t={elapsed:5.1f}s non-black={frac*100:5.1f}%")
        if not menu_state["ok"] and not menu_state["retried"] and elapsed > 30:
            menu_state["retried"] = True
            menu_state["ok"] = menu_capture("02_retry")
        for mark in list(wait_marks):
            if elapsed >= mark and not wait_marks[mark]:
                wait_marks[mark] = True
                shot(f"03_wait_{mark:03d}s", raw, w, h)
        if frac > 0.70:
            streak += 1
            if streak >= 2:
                gameplay = True
                gameplay_t = elapsed
                shot("04_gameplay_3d", raw, w, h)
                event(f"3D GAMEPLAY CONFIRMED at t={elapsed:.1f}s")
                break
        else:
            streak = 0
            if elapsed > 4 and int(elapsed) % 4 == 0:
                press_key(hwnd, VK_RETURN)
                press_key(hwnd, VK_SPACE)
                press_key(hwnd, VK_A)
        time.sleep(1.5)
    result["gameplay_3d"] = gameplay
    result["gameplay_t_s"] = round(gameplay_t, 1) if gameplay_t else None

    # Sustained burn with resource sampling
    if proc.poll() is None:
        event(f"running {args.burn:.0f}s burn with CPU/RAM/GPU telemetry")
        keys = [0x57, 0x41, 0x53, 0x44, 0x25, 0x27, 0x20, 0x0D, 0x45, 0x51]
        tb = time.time()
        i = 0
        marks = {int(args.burn * f): False for f in (0.33, 0.66, 0.95)}
        while time.time() - tb < args.burn:
            if proc.poll() is not None:
                event("ERROR: process exited during burn phase")
                break
            press_key(hwnd, keys[i % len(keys)])
            i += 1
            elapsed = time.time() - tb
            for m in list(marks):
                if elapsed >= m and not marks[m]:
                    marks[m] = True
                    shot(f"05_burn_{m:03d}s", grab_window(hwnd, w, h), w, h)
            time.sleep(1.0)
    else:
        event("burn skipped: process already exited")

    # Graceful shutdown via WM_CLOSE
    if proc.poll() is None:
        u32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.terminate()
            proc.wait(timeout=5)
    result["exit_code"] = proc.returncode
    telemetry.stop()
    result["telemetry"] = telemetry.summary()
    result["telemetry_samples"] = telemetry.samples
    event(f"process exited code={proc.returncode}; telemetry samples={len(telemetry.samples)}")

    # Log and crash_diag inspection
    new_logs = sorted(
        (p for p in logdir.glob("neverdead_*.log") if p.name not in before_logs),
        key=lambda p: p.stat().st_mtime,
    )
    all_logs = list(logdir.glob("neverdead_*.log"))
    target = new_logs[-1] if new_logs else (max(all_logs, key=lambda p: p.stat().st_mtime) if all_logs else None)
    result["log_analysis"] = analyze_log(target)
    crash_after = CRASH_DIAG.stat().st_size if CRASH_DIAG.exists() else 0
    result["crash_diag_new_bytes"] = crash_after - crash_before
    if result["crash_diag_new_bytes"] > 0 and CRASH_DIAG.exists():
        delta = CRASH_DIAG.read_bytes()[crash_before:crash_after]
        result["crash_diag_delta"] = delta.decode("utf-8", errors="replace")[:2000]
    if target is not None:
        event(
            f"log analyzed: {target.name}, {result['log_analysis']['lines']} lines, "
            f"errors={result['log_analysis']['errors']}"
        )
    else:
        event("WARN: no neverdead_*.log file found")

    finalize(result, out_json, out_md)
    return 0 if (gameplay and proc.returncode == 0 and result["crash_diag_new_bytes"] == 0) else 2


if __name__ == "__main__":
    sys.exit(main())
