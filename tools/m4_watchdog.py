"""
m4_watchdog - watchdog parametrizable (M4, swe_native_3d_v2).

COPIA MINIMA de run_game_watchdog_v2.py (NO se toca el original): unicamente
parametriza el directorio de build, el ejecutable y el fichero de estado, para
poder medir el binario del experimento E-Delta (arbol COPIA) con el mismo
protocolo que el producto: 20 s, lectura del log del disco, sin inventar nada.

    python tools/m4_watchdog.py --build-dir DIR [--timeout 20] [--tag NAME]
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent


def log_event(message: str, level: str = "ENGINE", path: Path | None = None) -> None:
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] {message}"
    print(line, flush=True)
    if path:
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build-dir", required=True)
    ap.add_argument("--timeout", type=int, default=20)
    ap.add_argument("--tag", default="m4")
    ap.add_argument("--status-file", default=None)
    args = ap.parse_args()

    build = Path(args.build_dir)
    exe = build / "neverdead.exe"
    assets = PROJECT_DIR / "assets"
    status_file = Path(args.status_file) if args.status_file else build / f"vibe_status_{args.tag}.json"
    watch_log = build / f"vibe_watch_{args.tag}.log"
    if not exe.is_file():
        print(f"no existe {exe}", file=sys.stderr)
        return 2

    it = 1
    if status_file.exists():
        try:
            it = int(json.loads(status_file.read_text(encoding="utf-8")).get("iteration", 0)) + 1
        except Exception:
            it = 1

    log_event(f"[{args.tag}] arranque de {exe} (watchdog {args.timeout}s, iter #{it})",
              path=watch_log)
    status = {
        "timestamp": datetime.datetime.now().isoformat(), "iteration": it,
        "state": "RUNNING_TEST", "harness_exe": str(exe), "log_file": str(watch_log),
    }
    status_file.write_text(json.dumps(status, indent=2), encoding="utf-8")

    env = os.environ.copy()
    env["SPDLOG_LEVEL"] = "trace"
    env["REXGLUE_LOG_LEVEL"] = "trace"
    cmd = [str(exe), "--game_data_root", str(assets)]
    start = time.time()
    proc = subprocess.Popen(cmd, cwd=str(build), stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=env, text=True, bufsize=1,
                            encoding="utf-8", errors="replace")
    try:
        out, err = proc.communicate(timeout=args.timeout)
        rc = proc.returncode
        log_event(f"[{args.tag}] el motor se detuvo con codigo {rc} "
                  f"(0x{rc & 0xFFFFFFFF:08X}) en {time.time()-start:.1f}s", path=watch_log)
        if out:
            for l in out.splitlines()[-8:]:
                log_event(f"STDOUT: {l}", "STDOUT", path=watch_log)
        if err:
            for l in err.splitlines()[-8:]:
                log_event(f"STDERR: {l}", "STDERR", path=watch_log)
        status.update({"state": "STOPPED", "exit_code": rc,
                       "exit_hex": f"0x{rc & 0xFFFFFFFF:08X}",
                       "elapsed_s": round(time.time() - start, 2)})
    except subprocess.TimeoutExpired:
        log_event(f"[{args.tag}] watchdog alcanzado ({args.timeout}s): el motor sigue vivo",
                  "SURVIVED", path=watch_log)
        proc.kill()
        out, err = proc.communicate()
        status.update({"state": "SURVIVED", "exit_code": None, "exit_hex": None,
                       "elapsed_s": round(time.time() - start, 2)})
    status_file.write_text(json.dumps(status, indent=2), encoding="utf-8")
    print(json.dumps(status, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
