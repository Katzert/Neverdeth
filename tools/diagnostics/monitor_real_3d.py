"""
Passive Real 3D Gameplay Monitor for NeverDead PC.
Monitors nvidia-smi GPU telemetry and captures game screenshots every 5 seconds
while the user plays in the 3D Asylum / Hospital Corridor.
"""
import os
import subprocess
import time
import glob
import sys
from PIL import Image

PROJECT_DIR = r"C:\Users\katze\.gemini\antigravity-ide\scratch\vibe-recomp\neverdead_project"
BUILD_DIR = os.path.join(PROJECT_DIR, "build_clang")
ASSETS_DIR = os.path.join(PROJECT_DIR, "assets")
EXE_PATH = os.path.join(BUILD_DIR, "neverdead.exe")
GPU_CSV = os.path.join(BUILD_DIR, "logs", "gpu_real_3d.csv")
SHOTS_DIR = os.path.join(BUILD_DIR, "shots", "real_3d")

sys.path.insert(0, os.path.join(PROJECT_DIR, "tools"))
import nd_menu_test as T

def find_running_pid():
    try:
        out = subprocess.check_output('tasklist /fi "imagename eq neverdead.exe" /fo csv /nh', shell=True, text=True)
        for line in out.strip().splitlines():
            parts = [p.strip(' "') for p in line.split(',')]
            if len(parts) >= 2 and parts[0].lower() == 'neverdead.exe':
                try:
                    return int(parts[1])
                except ValueError:
                    pass
    except Exception:
        pass
    return None

def get_stats(path):
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

def parse_gpu_csv(csv_path):
    gpu_utils = []
    pwr_draws = []
    try:
        with open(csv_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 3 and "%" in parts[1]:
                    try:
                        u = float(parts[1].replace("%", "").strip())
                        gpu_utils.append(u)
                    except ValueError:
                        pass
                    try:
                        p = float(parts[2].replace("W", "").strip())
                        pwr_draws.append(p)
                    except ValueError:
                        pass
    except Exception:
        pass
    return {
        "avg_gpu_util": round(sum(gpu_utils) / len(gpu_utils), 1) if gpu_utils else 0.0,
        "max_gpu_util": round(max(gpu_utils), 1) if gpu_utils else 0.0,
        "avg_power_w": round(sum(pwr_draws) / len(pwr_draws), 1) if pwr_draws else 0.0,
        "samples": len(gpu_utils),
    }

def main():
    duration = 120.0 # 120 seconds default
    if len(sys.argv) > 1:
        duration = float(sys.argv[1])

    os.makedirs(SHOTS_DIR, exist_ok=True)
    os.makedirs(os.path.join(BUILD_DIR, "logs"), exist_ok=True)

    if os.path.exists(GPU_CSV):
        try:
            os.remove(GPU_CSV)
        except Exception:
            pass

    # Start nvidia-smi
    smi_cmd = [
        "nvidia-smi",
        "--query-gpu=timestamp,utilization.gpu,power.draw,clocks.gr,clocks.mem",
        "--format=csv",
        "-l", "1"
    ]
    with open(GPU_CSV, "w") as gpu_out:
        smi_proc = subprocess.Popen(smi_cmd, stdout=gpu_out, stderr=subprocess.PIPE, text=True)
    print(f"nvidia-smi logging started (PID {smi_proc.pid}) -> {GPU_CSV}", flush=True)

    # Check if game is already running
    existing_pid = find_running_pid()
    spawned_by_us = False
    game_proc = None

    if existing_pid:
        print(f"Detected running neverdead.exe (PID {existing_pid}). Attaching monitor...", flush=True)
        game_pid = existing_pid
    else:
        env = os.environ.copy()
        env["SPDLOG_LEVEL"] = "info"
        env["REXGLUE_LOG_LEVEL"] = "info"
        print("Launching neverdead.exe for manual gameplay...", flush=True)
        game_proc = subprocess.Popen([EXE_PATH, "--game_data_root", ASSETS_DIR], cwd=BUILD_DIR, env=env)
        game_pid = game_proc.pid
        spawned_by_us = True

    t0 = time.time()
    hwnd = 0
    while time.time() - t0 < 10 and not hwnd:
        hwnd = T.find_window(game_pid)
        time.sleep(0.2)

    print(f"Game window handle: {hex(hwnd) if hwnd else 'NONE'}", flush=True)
    print(f"=== MONITOR ACTIVE FOR UP TO {duration:.0f} SECONDS ===", flush=True)
    print("User can play freely in the 3D Hospital Corridor now!", flush=True)

    last_shot = 0
    shot_idx = 0
    stats_history = []

    try:
        while time.time() - t0 < duration:
            # Check if game is still alive
            if spawned_by_us and game_proc.poll() is not None:
                print(f"Game closed by user (returncode={game_proc.returncode})", flush=True)
                break
            elif not spawned_by_us and find_running_pid() is None:
                print("Game closed by user externally.", flush=True)
                break

            el = time.time() - t0
            if el - last_shot >= 5.0:
                last_shot = el
                shot_idx += 1
                shot_path = os.path.join(SHOTS_DIR, f"real_3d_{int(el):03d}s.png")
                ok = False
                if hwnd:
                    ok = T.grab_window(hwnd, shot_path)
                if not ok:
                    from PIL import ImageGrab
                    try:
                        ImageGrab.grab().save(shot_path)
                        ok = True
                    except Exception:
                        pass
                if ok:
                    st = get_stats(shot_path)
                    stats_history.append((el, st))
                    print(f"[{el:5.1f}s] Screenshot: real_3d_{int(el):03d}s.png -> Mean Luminance: {st.get('mean')} | Burned: {st.get('burn_pct')}% | Shadows: {st.get('dark_pct')}%", flush=True)

            time.sleep(0.5)

    finally:
        print("Finalizing monitor and closing background tools...", flush=True)
        if spawned_by_us and game_proc and game_proc.poll() is None:
            # Let the game stay open if user is playing, or terminate if timeout
            print("Monitor duration finished.", flush=True)

        if smi_proc and smi_proc.poll() is None:
            smi_proc.terminate()
            try:
                smi_proc.wait(timeout=2)
            except Exception:
                smi_proc.kill()

    # Telemetry report
    print("\n================== TELEMETRY REPORT ==================", flush=True)
    gpu_stats = parse_gpu_csv(GPU_CSV)
    print(f"Total Monitoring Time: {time.time() - t0:.1f}s")
    print(f"GPU Samples Recorded:  {gpu_stats['samples']}")
    print(f"Average GPU Usage:     {gpu_stats['avg_gpu_util']}% (Peak: {gpu_stats['max_gpu_util']}%)")
    print(f"Average Power Draw:    {gpu_stats['avg_power_w']} W")
    if stats_history:
        valid_means = [s['mean'] for _, s in stats_history if 'mean' in s]
        valid_burns = [s['burn_pct'] for _, s in stats_history if 'burn_pct' in s]
        avg_mean = round(sum(valid_means) / len(valid_means), 2) if valid_means else 0
        avg_burn = round(sum(valid_burns) / len(valid_burns), 2) if valid_burns else 0
        print(f"Average Luminance:     {avg_mean} (Target: 50 - 75)")
        print(f"Average Burned Pixels: {avg_burn}% (Target: < 1%)")
    print("======================================================", flush=True)

if __name__ == "__main__":
    main()
