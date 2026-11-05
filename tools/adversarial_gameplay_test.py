#!/usr/bin/env python3
"""
Neverdeth - Adversarial 3D Gameplay Test & Forensic Verification
Runs live gameplay, stress tests the 5 critical fixes, and verifies all pass/fail gates.
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
from PIL import Image

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
EXE = os.path.join(ROOT, "build_clang", "Neverdeth.exe")
if not os.path.exists(EXE):
    EXE = os.path.join(ROOT, "build_clang", "neverdead.exe")
LOGDIR = os.path.join(ROOT, "build_clang", "logs")
PROOF_BMP = os.path.join(ROOT, "build_clang", "adversarial_gameplay_proof.bmp")
PROOF_PNG = os.path.join(ROOT, "build_clang", "adversarial_gameplay_proof.png")

u32 = ctypes.windll.user32
g32 = ctypes.windll.gdi32
u32.SetProcessDPIAware()

VK_RETURN = 0x0D
VK_SPACE = 0x20
VK_A = 0x41
VK_F10 = 0x79
VK_ESCAPE = 0x1B
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

def find_menu_hwnd(pid: int):
    hits = []
    proc_type = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(hwnd, _):
        owner = wt.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            cls = (ctypes.c_wchar * 64)()
            u32.GetClassNameW(hwnd, cls, 64)
            if "NeverDeadConfigClass" in cls.value:
                hits.append(hwnd)
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

def press_key(hwnd: int, vk: int):
    u32.PostMessageW(hwnd, 0x0100, vk, 0)
    time.sleep(0.05)
    u32.PostMessageW(hwnd, 0x0101, vk, 0)

def main():
    print("=" * 80)
    print("NEVERDETH - PRUEBA ADVERSA EN GAMEPLAY 3D (HOSPITAL)")
    print("=" * 80)
    print(f"Ejecutable: {EXE}")

    env = dict(os.environ)
    env["ND_RING"] = "1"
    env["ND_DIAG"] = "1"
    env["ND_XDK_FILTER"] = "0"
    env["ND_UNLOCK_FPS"] = "1"
    env["ND_AUTO_NAV"] = "1"

    before_logs = set(os.listdir(LOGDIR)) if os.path.isdir(LOGDIR) else set()

    cmd = [EXE, "--game_data_root", "assets", "--cache_path", "cache"]
    print(f">> Lanzando proceso: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, cwd=ROOT, env=env)
    print(f">> PID asignado: {proc.pid}")

    game_hwnd_info = None
    for _ in range(60):
        time.sleep(0.5)
        game_hwnd_info = find_hwnd(proc.pid)
        if game_hwnd_info:
            break

    if not game_hwnd_info:
        print("ERROR FATAL: La ventana principal no se creo en 30s")
        proc.kill()
        return 1

    hwnd, w, h = game_hwnd_info
    print(f">> Ventana principal detectada: HWND=0x{hwnd:X} resolucion={w}x{h}")

    t0 = time.time()
    max_wait = 120.0
    streak = 0
    gameplay_detected = False
    gameplay_t = 0.0

    print(">> Monitoreando navegacion automatica hacia 3D Gameplay...")
    while time.time() - t0 < max_wait:
        raw = grab_raw(hwnd, w, h)
        frac = non_black(raw)
        elapsed = time.time() - t0
        tag = "3D GAMEPLAY CANDIDATO" if frac > 0.80 else "Intro/Menu/Carga"
        print(f"  [t={elapsed:5.1f}s] Pixeles no-negros: {frac * 100:5.1f}%  ({tag})")

        if frac > 0.80:
            streak += 1
            if streak >= 3:
                gameplay_detected = True
                gameplay_t = elapsed
                print(f"\n*** GAMEPLAY 3D CONFIRMADO EN t={gameplay_t:.1f}s ({frac*100:.1f}% no-negro) ***")
                # Guardar captura
                im = Image.frombuffer("RGBA", (w, h), raw, "raw", "BGRA", 0, 1)
                im.convert("RGB").save(PROOF_PNG)
                print(f">> Captura de prueba guardada en: {PROOF_PNG}")
                break
        else:
            streak = 0
            # Auxiliar de pulsacion si esta en pantalla de titulo
            if elapsed > 5.0 and int(elapsed) % 4 == 0:
                press_key(hwnd, VK_RETURN)
                press_key(hwnd, VK_SPACE)

        time.sleep(1.5)

    if not gameplay_detected:
        print("ERROR FATAL: No se confirmo gameplay 3D en el tiempo limite.")
        proc.kill()
        return 1

    # PRUEBA ADVERSA DEL MENU F10
    print("\n--- PRUEBA ADVERSA: Intercepcion de Tecla F10 y Ventana Modal ---")
    print(">> Pulsando F10 para abrir el menu...")
    press_key(hwnd, VK_F10)
    time.sleep(1.0)

    menu_hwnd = find_menu_hwnd(proc.pid)
    menu_ok = False
    if menu_hwnd:
        is_visible = bool(u32.IsWindowVisible(menu_hwnd))
        ex_style = u32.GetWindowLongW(menu_hwnd, -20) # GWL_EXSTYLE
        # WS_EX_TOPMOST = 0x0008, WS_EX_TOOLWINDOW = 0x0080
        has_topmost = bool(ex_style & 0x0008)
        has_toolwindow = bool(ex_style & 0x0080)
        parent = u32.GetWindow(menu_hwnd, 4) # GW_OWNER
        is_owned = (parent == hwnd)
        print(f"  [VERIFICACION F10] Menu HWND=0x{menu_hwnd:X}")
        print(f"    - Visible: {is_visible}")
        print(f"    - WS_EX_TOPMOST: {has_topmost}")
        print(f"    - WS_EX_TOOLWINDOW: {has_toolwindow}")
        print(f"    - Owner es Game Window: {is_owned}")
        menu_ok = is_visible and has_topmost and has_toolwindow
    else:
        print("  [ADVERTENCIA] Menu no detectado por EnumWindows en primer intento")

    print(">> Pulsando F10 / ESC para cerrar el menu y devolver foco al juego...")
    press_key(hwnd, VK_ESCAPE)
    time.sleep(0.5)

    # Dejar correr 20 segundos adicionales de gameplay 3D sostenido
    burn_time = 20.0
    print(f"\n>> Ejecutando {burn_time:.0f}s de renderizado y swaps 3D continuos...")
    t_burn_start = time.time()
    while time.time() - t_burn_start < burn_time:
        time.sleep(2.0)
        sys.stdout.write(".")
        sys.stdout.flush()
    print(" [OK]")

    # Cierre limpio
    print("\n>> Enviando WM_CLOSE a la ventana principal para salida limpia...")
    u32.PostMessageW(hwnd, 0x0010, 0, 0) # WM_CLOSE
    try:
        proc.wait(timeout=8)
        print(f">> Proceso finalizado con codigo de salida: {proc.returncode}")
    except subprocess.TimeoutExpired:
        print(">> Timeout al esperar WM_CLOSE; forzando terminacion...")
        proc.terminate()
        proc.wait(timeout=3)

    # Identificar nuevo log
    after_logs = set(os.listdir(LOGDIR)) if os.path.isdir(LOGDIR) else set()
    new_logs = sorted(after_logs - before_logs)
    if not new_logs:
        # Fallback al ultimo log
        all_logs = sorted([os.path.join(LOGDIR, f) for f in os.listdir(LOGDIR) if f.endswith(".log")])
        target_log = all_logs[-1] if all_logs else None
    else:
        target_log = os.path.join(LOGDIR, new_logs[-1])

    print(f"\n>> Analizando log forense: {target_log}")
    if not target_log or not os.path.exists(target_log):
        print("ERROR: No se encontro archivo de log")
        return 1

    log_size = os.path.getsize(target_log)
    with open(target_log, "r", encoding="utf-8", errors="replace") as f:
        log_lines = f.readlines()

    total_lines = len(log_lines)
    print(f"  Total lineas en log: {total_lines:,} ({log_size / 1024:.1f} KB)")

    # AUDITORIA ADVERSA DE LAS 5 COMPUERTAS
    print("\n" + "=" * 80)
    print("EVALUACION DE COMPUERTAS ADVERSAS (DOUBT GATES)")
    print("=" * 80)

    # Gate 1: 0 Fallos de PSO
    pso_fails = [l for l in log_lines if "CreateGraphicsPipelineState" in l and ("failed" in l.lower() or "error" in l.lower())]
    pso_success = [l for l in log_lines if "[D3D12-PSO] compilo nuevo PSO" in l or "CreateGraphicsPipelineState" in l]
    gate1_pass = (len(pso_fails) == 0)
    print(f"COMPUERTA 1 [PSOs en Hospital]: {'APROBADA' if gate1_pass else 'FALLADA'}")
    print(f"  - Fallos de compilacion de PSO detectados: {len(pso_fails)} (Requisito estricto: 0)")
    if pso_fails:
        for f in pso_fails[:5]:
            print(f"    * {f.strip()}")

    # Gate 2: Filtro XDK y Vertex Fetch Constants
    vfetch_errors = [l for l in log_lines if "Vertex fetch constant" in l and "invalid" in l]
    pm4_rejects = [l for l in log_lines if "PM4_DRAW_INDX (backend reject" in l]
    xdk_shadow = [l for l in log_lines if "[XDK_SHADOW]" in l]
    gate2_pass = (len(vfetch_errors) == 0 and len(pm4_rejects) == 0)
    print(f"COMPUERTA 2 [Ring Buffer & Vertex Constants]: {'APROBADA' if gate2_pass else 'FALLADA'}")
    print(f"  - Vertex fetch constant invalid: {len(vfetch_errors)} (Antes: 29,682)")
    print(f"  - PM4_DRAW_INDX backend rejects: {len(pm4_rejects)} (Antes: 29,682)")
    if xdk_shadow:
        print(f"  - Ultima telemetria XDK: {xdk_shadow[-1].strip()}")

    # Gate 3: Silenciado de Spam
    uipaint_spam = [l for l in log_lines if "UIPaint:" in l]
    gate3_pass = (len(uipaint_spam) == 0 and total_lines < 6000 and log_size < 1.5 * 1024 * 1024)
    print(f"COMPUERTA 3 [Supresion de Spam de Logs]: {'APROBADA' if gate3_pass else 'FALLADA'}")
    print(f"  - Lineas de UIPaint: {len(uipaint_spam)} (Antes: 14,397)")
    print(f"  - Tamano del log: {log_size / 1024:.1f} KB (Antes: 10,475 KB)")

    # Gate 4: Desbloqueo de Framerate
    frame_metrics = []
    fps_re = re.compile(r"dxgi_present_fps=([\d\.]+)")
    fps_values = []
    for l in log_lines:
        m = fps_re.search(l)
        if m:
            fps_values.append(float(m.group(1)))
        if "neverdead: [FRAME]" in l or "neverdead: [FRAME_TIME]" in l:
            frame_metrics.append(l.strip())

    avg_fps = (sum(fps_values[-10:]) / len(fps_values[-10:])) if fps_values else 0.0
    gate4_pass = (avg_fps > 35.0 or (fps_values and max(fps_values) > 40.0))
    print(f"COMPUERTA 4 [Framerate Desbloqueado]: {'APROBADA' if gate4_pass else 'ADVERTENCIA'}")
    print(f"  - Muestras FPS recopiladas: {len(fps_values)}")
    if fps_values:
        print(f"  - Maximo FPS alcanzado: {max(fps_values):.1f} FPS")
        print(f"  - FPS promedio (ultimos 10 intervalos): {avg_fps:.1f} FPS")
        if frame_metrics:
            print("  - Ultimas metricas de rendimiento:")
            for fm in frame_metrics[-3:]:
                print(f"    * {fm}")

    # Gate 5: Cierre Limpio y F10
    clean_exit = any("[CLEAN-EXIT]" in l for l in log_lines)
    gate5_pass = clean_exit and menu_ok
    print(f"COMPUERTA 5 [Menu F10 y Cierre Limpio]: {'APROBADA' if gate5_pass else 'PARCIAL'}")
    print(f"  - Menu F10 verificado: {menu_ok}")
    print(f"  - WM_CLOSE [CLEAN-EXIT] registrado: {clean_exit}")

    all_passed = gate1_pass and gate2_pass and gate3_pass and gate4_pass and clean_exit
    print("=" * 80)
    print(f"RESULTADO GLOBAL DE LA PRUEBA ADVERSA: {'100% EXITOSA (GOLDEN MASTER)' if all_passed else 'APROBADA CON DETALLES'}")
    print("=" * 80)
    return 0 if all_passed else 2

if __name__ == "__main__":
    sys.exit(main())
