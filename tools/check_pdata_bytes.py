#!/usr/bin/env python3
import struct
import os

BASE_DIR = r"c:\Users\katze\.gemini\antigravity-ide\scratch\vibe-recomp\neverdead_project"
EXE_PATH = os.path.join(BASE_DIR, "NeverDead_base.exe")
XEX_UNCOMP = os.path.join(BASE_DIR, "..", "NeverDead_uncompressed.xex")

# Check .pdata in NeverDead_base.exe and NeverDead_uncompressed.xex
# RVA of .pdata is 0x16F800, size is 0x238D0
with open(EXE_PATH, "rb") as f:
    exe_data = f.read()

with open(XEX_UNCOMP, "rb") as f:
    xex_uncomp_data = f.read()

print(f"NeverDead_base.exe size: {len(exe_data):,} bytes (0x{len(exe_data):X})")
print(f"NeverDead_uncompressed.xex size: {len(xex_uncomp_data):,} bytes (0x{len(xex_uncomp_data):X})")

# Let's compare the bytes at 0x16F800 in exe vs 0x3000 + 0x16F800 in uncompressed xex
pdata_exe = exe_data[0x16F800 : 0x16F800 + 0x238D0]
pdata_xex = xex_uncomp_data[0x3000 + 0x16F800 : 0x3000 + 0x16F800 + 0x238D0]
print(f".pdata at RVA 0x16F800: exe length={len(pdata_exe)}, xex length={len(pdata_xex)}")
print(f"Do pdata bytes in exe and xex match exactly? {pdata_exe == pdata_xex}")
if pdata_exe != pdata_xex:
    diffs = sum(1 for a, b in zip(pdata_exe, pdata_xex) if a != b)
    print(f"  Differences: {diffs} bytes")
