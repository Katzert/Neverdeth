"""
Runner script for 35s verification test with real-time nvidia-smi GPU logging
and engine log extraction.
"""
import os
import subprocess
import time
import glob
import sys

PROJECT_DIR = r"C:\Users\katze\.gemini\antigravity-ide\scratch\vibe-recomp\neverdead_project"
BUILD_DIR = os.path.join(PROJECT_DIR, "build_clang")
ASSETS_DIR = os.path.join(PROJECT_DIR, "assets")
EXE_PATH = os.path.join(BUILD_DIR, "neverdead.exe")
GPU_CSV = os.path.join(BUILD_DIR, "logs", "gpu_power.csv")

def main():
    print("=== STARTING 35S VERIFICATION TEST ===")
    
    # Clean previous gpu_power.csv
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
    print(f"nvidia-smi started (PID {smi_proc.pid}) -> logging to {GPU_CSV}")

    # Launch neverdead.exe
    env = os.environ.copy()
    env["SPDLOG_LEVEL"] = "info"
    env["REXGLUE_LOG_LEVEL"] = "info"

    game_cmd = [EXE_PATH, "--game_data_root", ASSETS_DIR]
    print(f"Launching neverdead.exe for 35s...")
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
    try:
        # Run for 35 seconds
        while time.time() - t0 < 35:
            time.sleep(1)
            # check if game exited prematurely
            if game_proc.poll() is not None:
                print(f"Game process exited prematurely with code {game_proc.returncode}!")
                break
    finally:
        print("Stopping game and nvidia-smi...")
        if game_proc.poll() is None:
            game_proc.terminate()
            try:
                game_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                game_proc.kill()
        
        if smi_proc.poll() is None:
            smi_proc.terminate()
            try:
                smi_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                smi_proc.kill()

    print(f"Run completed in {time.time() - t0:.2f} seconds.")

    # Find the newest log file in build_clang/logs
    log_files = glob.glob(os.path.join(BUILD_DIR, "logs", "neverdead_*.log"))
    if log_files:
        log_files.sort(key=lambda x: os.path.getmtime(x))
        newest_log = log_files[-1]
        print(f"Newest engine log: {newest_log}")
        with open(newest_log, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        print(f"Total lines in log: {len(lines)}")
        
        # Print relevant GATE, VIDEO, and FRAME lines
        print("\n=== RELEVANT ENGINE LOG LINES ===")
        for line in lines:
            line_str = line.strip()
            if any(k in line_str for k in ["[PAINT-GATE]", "[VIDEO]", "[FPS60]", "[FRAME]", "[RTV-FILTER]", "[ASYNC-PSO]"]):
                print(line_str)

    # Read and print GPU CSV
    if os.path.exists(GPU_CSV):
        print("\n=== NVIDIA-SMI REAL TELEMETRY ===")
        with open(GPU_CSV, "r", encoding="utf-8", errors="replace") as f:
            csv_lines = [l.strip() for l in f.readlines() if l.strip()]
        for l in csv_lines:
            print(l)

if __name__ == "__main__":
    main()
