"""
NeverDead Native Engine Boot Watchdog (variante v2) — M3.3 de swe_native_3d_v2.

COPIA MINIMA de run_game_watchdog.py: NO se toca el original.  Lo UNICO que
cambia es el directorio de build (build_clang_v2), el ejecutable, el fichero de
estado y el fichero de log, de forma que el binario v2 se pueda medir con el
mismo protocolo (20 s, auto-incremento de iteracion, logs del motor en
<build>/logs/neverdead_NNN.log).
"""
import datetime
import json
import os
import subprocess
import sys
import time

PROJECT_DIR = r"C:\Users\katze\.gemini\antigravity-ide\scratch\vibe-recomp\neverdead_project"
BUILD_DIR = os.path.join(PROJECT_DIR, "build_clang_v2")
ASSETS_DIR = os.path.join(PROJECT_DIR, "assets")
STATUS_FILE = r"C:\Users\katze\.gemini\antigravity-ide\scratch\vibe-recomp\vibe_status_v2.json"
LOG_FILE = r"C:\Users\katze\.gemini\antigravity-ide\scratch\vibe-recomp\vibe_watch_v2.log"
EXE_PATH = os.path.join(BUILD_DIR, "neverdead.exe")


def get_next_iteration():
    """Lee la iteracion previa persistida y retorna prev + 1."""
    try:
        if os.path.exists(STATUS_FILE):
            with open(STATUS_FILE, "r", encoding="utf-8", errors="replace") as f:
                st = json.load(f)
                return int(st.get("iteration", 0)) + 1
    except Exception:
        pass
    return 1


def log_event(message, level="ENGINE"):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] {message}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def update_status(iteration, state, stub, error_code, details):
    status = {
        "timestamp": datetime.datetime.now().isoformat(),
        "iteration": iteration,
        "state": state,
        "current_stub": stub,
        "last_error_code": error_code,
        "details": details,
        "harness_exe": EXE_PATH,
        "log_file": LOG_FILE,
    }
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2)


def main():
    timeout = 10
    if len(sys.argv) > 1:
        try:
            timeout = int(sys.argv[1])
        except ValueError:
            pass

    it = get_next_iteration()
    log_event(f"[M3.3 v2] Iniciando arranque de neverdead.exe (Watchdog: {timeout}s, Iteracion #{it})...")
    update_status(it, "RUNNING_TEST", "neverdead.exe --game_data_root ../assets", "0x00000000", "Iniciando motor ReXGlue nativo (arbol v2)...")

    env = os.environ.copy()
    env["SPDLOG_LEVEL"] = "trace"
    env["REXGLUE_LOG_LEVEL"] = "trace"

    cmd = [EXE_PATH, "--game_data_root", ASSETS_DIR]

    start_time = time.time()
    proc = subprocess.Popen(
        cmd,
        cwd=BUILD_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
        bufsize=1,
        encoding="utf-8",
        errors="replace",
    )

    stdout_lines = []
    stderr_lines = []

    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        rc = proc.returncode
        log_event(f"[M3.3 v2] Motor detenido con codigo de salida: {rc}")
        if stdout:
            for l in stdout.splitlines()[-10:]:
                log_event(f"STDOUT: {l}", "STDOUT")
        if stderr:
            for l in stderr.splitlines()[-10:]:
                log_event(f"STDERR: {l}", "STDERR")

        if rc == 0:
            update_status(it, "COMPLETED", "Ejecucion finalizada limpiamente", "0x00000000", "Arranque y salida limpios")
        else:
            update_status(it, "ANALYZING_CRASH", f"Exit code {rc}", f"EXIT_{rc}", stderr[:60] if stderr else f"rc={rc}")
        return rc
    except subprocess.TimeoutExpired:
        log_event(f"[M3.3 v2] Watchdog alcanzado ({timeout}s). El motor se mantiene en ejecucion activa sin caidas!", "SURVIVED")
        update_status(it, "SURVIVED", f"Motor vivo y ejecutando ({timeout}s)", "0x00000000", "Secuencia de arranque superada sin crashes")
        proc.kill()
        stdout, stderr = proc.communicate()
        if stdout:
            for l in stdout.splitlines()[-5:]:
                log_event(f"STDOUT: {l}", "STDOUT")
        if stderr:
            for l in stderr.splitlines()[-5:]:
                log_event(f"STDERR: {l}", "STDERR")
        return 0


if __name__ == "__main__":
    sys.exit(main())
