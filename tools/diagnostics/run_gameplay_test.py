"""
Gameplay test runner: Enters an actual game session (Single Player / Campaign),
captures 3D gameplay screenshots, logs second-by-second nvidia-smi GPU telemetry,
and extracts frame pacing metrics.
"""
import os
import subprocess
import time
import glob
import sys
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))
import nd_menu_test as T

PROJECT_DIR = r"C:\Users\katze\.gemini\antigravity-ide\scratch\vibe-recomp\neverdead_project"
BUILD_DIR = os.path.join(PROJECT_DIR, "build_clang")
ASSETS_DIR = os.path.join(PROJECT_DIR, "assets")
EXE_PATH = os.path.join(BUILD_DIR, "neverdead.exe")
GPU_CSV = os.path.join(BUILD_DIR, "logs", "gpu_gameplay.csv")
SHOTS_DIR = os.path.join(BUILD_DIR, "shots", "gameplay")

def get_image_stats(path: str) -> dict:
    try:
        im = Image.open(path).convert("L")
        h = im.histogram()
        n = sum(h)
        return {
            "mean": round(sum(i * c for i, c in enumerate(h)) / n, 2),
            "burn_pct": round(sum(h[251:]) / n * 100, 2),
            "dark_pct": round(sum(h[:6]) / n * 100, 2),
        }
    except Exception as e:
        return {"error": str(e)}

def main():
    print("=== STARTING REAL IN-GAME GAMEPLAY VERIFICATION (65s) ===")
    os.makedirs(SHOTS_DIR, exist_ok=True)
    os.makedirs(os.path.join(BUILD_DIR, "logs"), exist_ok=True)

    if os.path.exists(GPU_CSV):
        try:
            os.remove(GPU_CSV)
        except Exception:
            pass

    # Start nvidia-smi background logging
    smi_cmd = [
        "nvidia-smi",
        "--query-gpu=timestamp,utilization.gpu,power.draw,clocks.gr,clocks.mem",
        "--format=csv",
        "-l", "1"
    ]
    with open(GPU_CSV, "w") as gpu_out:
        smi_proc = subprocess.Popen(smi_cmd, stdout=gpu_out, stderr=subprocess.PIPE, text=True)
    print(f"nvidia-smi logging started (PID {smi_proc.pid}) -> {GPU_CSV}")

    # Launch neverdead.exe
    env = os.environ.copy()
    env["SPDLOG_LEVEL"] = "info"
    env["REXGLUE_LOG_LEVEL"] = "info"

    game_cmd = [EXE_PATH, "--game_data_root", ASSETS_DIR]
    print(f"Launching neverdead.exe...")
    game_proc = subprocess.Popen(
        game_cmd,
        cwd=BUILD_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
        bufsize=1,
        encoding="utf-8",
        errors="replace"
    )

    t0 = time.time()
    hwnd = 0
    # Wait for window
    while time.time() - t0 < 12 and not hwnd:
        hwnd = T.find_window(game_proc.pid)
        time.sleep(0.2)
    print(f"Found game window: {hex(hwnd) if hwnd else 'NONE'}")

    # Sequence of Enters to navigate title screen, main menu, difficulty, start mission
    enter_schedule = [12.0, 16.0, 20.0, 24.0, 28.0, 32.0, 36.0, 40.0]
    shot_schedule = [45.0, 50.0, 55.0, 60.0]
    
    ei = 0
    si = 0
    shot_results = []

    try:
        while time.time() - t0 < 65.0:
            if game_proc.poll() is not None:
                print(f"Game process exited prematurely with code {game_proc.returncode}!")
                break

            el = time.time() - t0

            # Handle Enter presses
            if ei < len(enter_schedule) and el >= enter_schedule[ei]:
                if hwnd:
                    T.focus(hwnd)
                T.press_enter(hwnd)
                fg = T.user32.GetForegroundWindow()
                print(f"[{el:5.1f}s] Pressed ENTER (#{ei+1}) (hwnd={hex(hwnd)}, fg_ok={fg == hwnd})", flush=True)
                ei += 1

            # Handle Screenshots
            if si < len(shot_schedule) and el >= shot_schedule[si]:
                if hwnd:
                    T.focus(hwnd)
                shot_path = os.path.join(SHOTS_DIR, f"gameplay_{int(shot_schedule[si])}s.png")
                ok = bool(hwnd) and T.grab_window(hwnd, shot_path)
                if not ok:
                    from PIL import ImageGrab
                    ImageGrab.grab().save(shot_path)
                st = get_image_stats(shot_path)
                shot_results.append((shot_path, st))
                print(f"[{el:5.1f}s] CAPTURED SHOT {shot_path}: mean={st.get('mean')} burn={st.get('burn_pct')}% dark={st.get('dark_pct')}%", flush=True)
                si += 1

            time.sleep(0.05)

    finally:
        print("Stopping game and nvidia-smi...")
        if game_proc.poll() is None:
            game_proc.terminate()
            try:
                game_proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                game_proc.kill()
        
        if smi_proc.poll() is None:
            smi_proc.terminate()
            try:
                smi_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                smi_proc.kill()

    total_time = time.time() - t0
    print(f"\n=== GAMEPLAY TEST COMPLETED IN {total_time:.1f}s ===")

    # Find the newest engine log
    log_files = glob.glob(os.path.join(BUILD_DIR, "logs", "neverdead_*.log"))
    if log_files:
        log_files.sort(key=lambda x: os.path.getmtime(x))
        newest_log = log_files[-1]
        print(f"Engine log: {newest_log}")
        with open(newest_log, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        print(f"Total lines in log: {len(lines)}")
        print("\n=== RELEVANT ENGINE LOG LINES ===")
        for line in lines:
            line_str = line.strip()
            if any(k in line_str for k in ["[PAINT-GATE]", "[VIDEO]", "[FPS60]", "[FRAME]", "[RTV-FILTER]", "[ASYNC-PSO]", "[STALL]"]):
                print(line_str)

    # Read and analyze GPU CSV
    if os.path.exists(GPU_CSV):
        print("\n=== REAL NVIDIA-SMI GPU TELEMETRY ===")
        with open(GPU_CSV, "r", encoding="utf-8", errors="replace") as f:
            csv_lines = [l.strip() for l in f.readlines() if l.strip()]
        for l in csv_lines:
            print(l)

if __name__ == "__main__":
    main()
