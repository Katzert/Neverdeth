import os
import subprocess
import time

def main():
    assets_dir = os.path.abspath("assets")
    cmd = [r"build_clang\neverdead.exe", "--game_data_root", assets_dir]
    proc = subprocess.Popen(cmd, cwd="build_clang")
    print(f"Launched neverdead.exe (PID {proc.pid})")

    for check_sec in [10, 20, 30]:
        time.sleep(10)
        ret = proc.poll()
        if ret is not None:
            print(f"Process terminated at ~{check_sec}s with code {ret}")
            return
        print(f"At {check_sec}s: Process is ALIVE and RUNNING!")

    print("Stopping process after 30s...")
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
    print("Test finished successfully.")

if __name__ == "__main__":
    main()
