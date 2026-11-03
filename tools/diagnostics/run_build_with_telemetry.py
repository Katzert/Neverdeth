"""
Vibe-Recomp Real-time Build Telemetry Feeder
Ejecuta la compilación con Ninja y Clang, capturando la salida en streaming
y actualizando vibe_status.json y vibe_watch.log en tiempo real.
Auto-incremento dinámico de iteración (+1 por cada compilación).
"""
import datetime
import io
import json
import os
import re
import subprocess
import sys
import time

HARNESS_DIR = r"C:\Users\katze\.gemini\antigravity-ide\scratch\vibe-recomp"
PROJECT_DIR = os.path.join(HARNESS_DIR, "neverdead_project")
STATUS_FILE = os.path.join(HARNESS_DIR, "vibe_status.json")
LOG_FILE = os.path.join(HARNESS_DIR, "vibe_watch.log")

VCVARS = r"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvarsall.bat"
CMAKE = r"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
LLVM_BIN = r"C:\Program Files\LLVM\bin"
NINJA_DIR = r"C:\Users\katze\AppData\Local\Programs\Python\Python312\Scripts"
SDK = r"C:/Users/katze/.gemini/antigravity-ide/scratch/vibe-recomp/rexglue_sdk/win-amd64"

def get_next_iteration():
    """Lee la iteración previa persistida y retorna prev + 1."""
    try:
        if os.path.exists(STATUS_FILE):
            with open(STATUS_FILE, "r", encoding="utf-8", errors="replace") as f:
                st = json.load(f)
                return int(st.get("iteration", 20)) + 1
    except Exception:
        pass
    return 21

def log_event(message, level="BUILD"):
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
        "harness_exe": os.path.join(PROJECT_DIR, "build_clang", "neverdead.exe"),
        "log_file": LOG_FILE
    }
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2)

def main():
    it = get_next_iteration()
    log_event(f"Iniciando build feeder con LLVM Clang y Ninja (Iteración #{it})...")
    update_status(it, "RECOMPILING_CLANG", "Configurando entorno de compilacion", "0x00000000", "Iniciando Clang 23.1 y Ninja")

    # Comando batch que inicializa vcvarsall y ejecuta ninja
    build_clang_dir = os.path.join(PROJECT_DIR, "build_clang")
    cmd = (
        f'@call "{VCVARS}" x64 >nul 2>&1 && '
        f'set "PATH={LLVM_BIN};{NINJA_DIR};%PATH%" && '
        f'cd /d "{PROJECT_DIR}" && '
        f'"{CMAKE}" --build build_clang --config Release'
    )

    proc = subprocess.Popen(
        cmd,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        encoding="utf-8",
        errors="replace"
    )

    pattern = re.compile(r"\[(\d+)/(\d+)\]\s+(.*)")

    last_update = time.time()
    for line in iter(proc.stdout.readline, ""):
        line_str = line.strip()
        if not line_str:
            continue
        
        m = pattern.search(line_str)
        if m:
            curr = int(m.group(1))
            total = int(m.group(2))
            rest = m.group(3)
            # Extraer el nombre del archivo
            target = rest.split("/")[-1] if "/" in rest else rest
            pct = (curr / total) * 100
            
            update_status(
                it,
                "RECOMPILING_CLANG",
                f"[{curr}/{total}] {target[:40]}",
                "0x00000000",
                f"Progreso Clang: {curr}/{total} ({pct:.1f}%)"
            )
            log_event(f"[{curr}/{total}] ({pct:.1f}%) {target[:50]}", "NINJA")
            last_update = time.time()
        else:
            if "error:" in line_str.lower():
                log_event(line_str, "ERROR")
                update_status(it, "RECOMPILING_CLANG", "Error de compilacion detectado", "CLANG_ERROR", line_str[:60])
            elif "warning:" in line_str.lower():
                pass
            else:
                if time.time() - last_update > 2.0:
                    update_status(it, "RECOMPILING_CLANG", line_str[:40], "0x00000000", "Compilando con Clang...")
                    last_update = time.time()

    proc.stdout.close()
    rc = proc.wait()

    if rc == 0:
        log_event("COMPILACION EXITOSA! Binario neverdead.exe generado.", "BUILD_OK")
        update_status(it, "SURVIVED", "neverdead.exe generado exitosamente", "0x00000000", "Binario x86-64 listo para arranque")
    else:
        log_event(f"Compilacion finalizada con errores (rc={rc}).", "ERROR")
        update_status(it, "ANALYZING_CRASH", f"Fallo de compilacion (rc={rc})", f"EXIT_{rc}", "Revisar log de compilacion")

    return rc

if __name__ == "__main__":
    sys.exit(main())
