"""
Passive watcher for user gameplay in NeverDead PC.
Waits for neverdead.exe to be started by the user (via JUGAR_NEVERDEAD.bat),
logs nvidia-smi GPU metrics, captures screenshots every 5 seconds,
and reports final telemetry upon exit.
"""
import os
import subprocess
import time
import glob
import sys
from PIL import Image

PROJECT_DIR = r"C:\Users\katze\.gemini\antigravity-ide\scratch\vibe-recomp\neverdead_project"
BUILD_DIR = os.path.join(PROJECT_DIR, "build_clang")
GPU_CSV = os.path.join(BUILD_DIR, "logs", "gpu_user_gameplay.csv")
SHOTS_DIR = os.path.join(BUILD_DIR, "shots", "real_3d")

sys.path.insert(0, os.path.join(PROJECT_DIR, "tools"))
import nd_menu_test as T

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

def find_game_pid():
    try:
        out = subprocess.check_output(['tasklist', '/FI', 'IMAGENAME eq neverdead.exe', '/FO', 'CSV'], text=True, stderr=subprocess.DEVNULL)
        lines = [l.strip() for l in out.splitlines() if l.strip()]
        if len(lines) > 1 and 'neverdead.exe' in lines[1].lower():
            parts = lines[1].split(',')
            if len(parts) >= 2:
                return int(parts[1].strip('"'))
    except Exception:
        pass
    return None

def is_pid_alive(pid):
    try:
        out = subprocess.check_output(['tasklist', '/FI', f'PID eq {pid}', '/FO', 'CSV'], text=True, stderr=subprocess.DEVNULL)
        return str(pid) in out
    except Exception:
        return False

def main():
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

    print("WAITING FOR USER TO LAUNCH neverdead.exe (via JUGAR_NEVERDEAD.bat)...", flush=True)

    pid = None
    t_wait_start = time.time()
    while time.time() - t_wait_start < 180 and not pid:
        pid = find_game_pid()
        time.sleep(0.5)

    if not pid:
        print("Timeout waiting for game launch (180s).", flush=True)
        if smi_proc.poll() is None:
            smi_proc.terminate()
        return

    print(f"GAME DETECTED! PID: {pid}", flush=True)
    t_game_start = time.time()
    hwnd = 0
    while time.time() - t_game_start < 15 and not hwnd:
        hwnd = T.find_window(pid)
        time.sleep(0.2)

    print(f"Game window handle: {hex(hwnd) if hwnd else 'NONE'}", flush=True)
    print("MONITORING GAMEPLAY IN REAL TIME...", flush=True)

    last_shot = 0
    shot_idx = 0

    try:
        while is_pid_alive(pid):
            el = time.time() - t_game_start
            if el - last_shot >= 5.0:
                last_shot = el
                shot_idx += 1
                shot_path = os.path.join(SHOTS_DIR, f"real_gameplay_{int(el):03d}s.png")
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
                    print(f"[{el:5.1f}s] Screenshot: {shot_path} -> mean={st.get('mean')} burn={st.get('burn_pct')}% dark={st.get('dark_pct')}%", flush=True)
            time.sleep(0.5)
    except Exception as e:
        print(f"Game session ended: {e}", flush=True)

    print("Game process exited. Finalizing monitor...", flush=True)
    if smi_proc.poll() is None:
        smi_proc.terminate()
        try:
            smi_proc.wait(timeout=2)
        except Exception:
            smi_proc.kill()

    # Find newest log
    logs = glob.glob(os.path.join(BUILD_DIR, "logs", "neverdead_*.log"))
    if logs:
        logs.sort(key=os.path.getmtime)
        print(f"Newest engine log: {logs[-1]}")
        with open(logs[-1], "r", encoding="utf-8", errors="replace") as f:
            for l in f:
                if any(k in l for k in ["[RTV-FLOAT16]", "[FRAME]", "[VIDEO]", "[PAINT-GATE]"]):
                    print(l.strip())

    print("=== MONITORING FINISHED ===", flush=True)

if __name__ == "__main__":
    main()
