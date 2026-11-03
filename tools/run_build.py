#!/usr/bin/env python3
import os
import sys
import subprocess

msvc = r"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Tools\MSVC\14.44.35207"
sdk = r"C:\Program Files (x86)\Windows Kits\10"
sdkv = "10.0.26100.0"

env = os.environ.copy()
env["INCLUDE"] = (
    f"{msvc}\\include;"
    f"{sdk}\\Include\\{sdkv}\\ucrt;"
    f"{sdk}\\Include\\{sdkv}\\shared;"
    f"{sdk}\\Include\\{sdkv}\\um;"
    f"{sdk}\\Include\\{sdkv}\\winrt;"
    f"{sdk}\\Include\\{sdkv}\\cppwinrt"
)
env["LIB"] = (
    f"{msvc}\\lib\\x64;"
    f"{sdk}\\Lib\\{sdkv}\\ucrt\\x64;"
    f"{sdk}\\Lib\\{sdkv}\\um\\x64"
)
env["PATH"] = (
    f"C:\\Program Files\\LLVM\\bin;"
    f"C:\\Users\\katze\\AppData\\Local\\Programs\\Python\\Python312\\Scripts;"
    f"{env.get('PATH', '')}"
)

def run():
    cmd = ["cmake", "--build", "build_clang", "--config", "Release"]
    if len(sys.argv) > 1:
        cmd.extend(sys.argv[1:])
    print(f"Executing: {' '.join(cmd)}")
    sys.stdout.flush()
    res = subprocess.run(cmd, env=env)
    sys.exit(res.returncode)

if __name__ == "__main__":
    run()
