"""Captura determinista de gameplay para comparar rutas de render.

Lanza el juego, enfoca su ventana (por PID, no por titulo: el Explorador con la
carpeta neverdead_project abierta tambien contiene "neverdead" en el titulo),
pulsa START en tiempos fijos para llegar al gameplay y captura con PrintWindow
(funciona aunque otra ventana tape al juego).

    ND_RTV_EXP_BIAS=-3 python tools/nd_capture.py --tag rtv_m3 \
        --enters 12,20,28,36 --shots 40,45,50 --timeout 54

La ruta de render se elige en build_clang/neverdead.toml
(render_target_path_d3d12 = "rov" | "rtv").
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nd_menu_test as T  # noqa: E402


def stats(path: str) -> dict:
    im = Image.open(path).convert("L")
    h = im.histogram()
    n = sum(h)
    return {
        "file": os.path.basename(path),
        "size": list(im.size),
        "mean": round(sum(i * c for i, c in enumerate(h)) / n, 2),
        "burn_pct": round(sum(h[251:]) / n * 100, 2),
        "dark_pct": round(sum(h[:6]) / n * 100, 2),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default="build_clang/neverdead.exe")
    ap.add_argument("--assets", default="assets")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--enters", default="12,20,28,36")
    ap.add_argument("--shots", default="40,45,50")
    ap.add_argument("--timeout", type=float, default=54.0)
    ap.add_argument("--out-root", default="build_clang/shots/gate2")
    args = ap.parse_args()

    enters = sorted(float(x) for x in args.enters.split(",") if x.strip())
    shots = sorted(float(x) for x in args.shots.split(",") if x.strip())
    out = os.path.join(args.out_root, args.tag)
    os.makedirs(out, exist_ok=True)

    exe = os.path.abspath(args.exe)
    proc = subprocess.Popen([exe, "--game_data_root", os.path.abspath(args.assets)],
                            cwd=os.path.dirname(exe), env=os.environ.copy())
    t0 = time.time()
    hwnd = 0
    while time.time() - t0 < 10 and not hwnd:
        hwnd = T.find_window(proc.pid)
        time.sleep(0.2)
    print("hwnd", hex(hwnd) if hwnd else 0, flush=True)

    stamp = time.strftime("%H%M%S")
    ei = si = 0
    results = []
    while time.time() - t0 < args.timeout and proc.poll() is None:
        el = time.time() - t0
        if ei < len(enters) and el >= enters[ei]:
            if hwnd:
                T.focus(hwnd)
            T.press_enter(hwnd)
            print("ENTER %.1fs fg_ok=%s" % (el, T.user32.GetForegroundWindow() == hwnd), flush=True)
            ei += 1
        if si < len(shots) and el >= shots[si]:
            if hwnd:
                T.focus(hwnd)
            path = os.path.join(out, "%s_%s_%03.0fs.png" % (args.tag, stamp, shots[si]))
            ok = bool(hwnd) and T.grab_window(hwnd, path)
            if not ok:
                from PIL import ImageGrab
                ImageGrab.grab().save(path)
            results.append(stats(path))
            print("SHOT", path, "mean=%.1f burn=%.2f%%" % (results[-1]["mean"],
                                                           results[-1]["burn_pct"]), flush=True)
            si += 1
        time.sleep(0.05)

    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    meta = {"tag": args.tag, "bias": os.environ.get("ND_RTV_EXP_BIAS"),
            "enters": enters, "shots": shots, "frames": results}
    with open(os.path.join(out, "stats.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    print(json.dumps(meta, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
