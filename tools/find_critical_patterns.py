#!/usr/bin/env python3
import os

BASE_DIR = r"c:\Users\katze\.gemini\antigravity-ide\scratch\vibe-recomp\neverdead_project"
EXE_PATH = os.path.join(BASE_DIR, "NeverDead_base.exe")
XEX_UNCOMP = os.path.join(BASE_DIR, "..", "NeverDead_uncompressed.xex")
XEX_ASSETS = os.path.join(BASE_DIR, "assets", "default.xex")

with open(EXE_PATH, "rb") as f:
    exe_bytes = f.read()

with open(XEX_UNCOMP, "rb") as f:
    xex_bytes = f.read()

patterns = {
    "savegprlr_14 (f9c1ff68f9e1ff70)": bytes.fromhex("f9c1ff68f9e1ff70"),
    "sub_8271E508 (409800087d795b78)": bytes.fromhex("409800087d795b78"),
    "mflr at 71D508 (7d8802a64806e831)": bytes.fromhex("7d8802a64806e831"),
}

for name, pat in patterns.items():
    print(f"\n=== Searching pattern: {name} ===")
    
    # In NeverDead_base.exe
    pos = 0
    while True:
        pos = exe_bytes.find(pat, pos)
        if pos == -1:
            break
        va_flat = 0x82000000 + pos
        print(f"  NeverDead_base.exe: found at FileOff=0x{pos:08X} (Flat VA = 0x{va_flat:08X})")
        pos += 1
        
    # In NeverDead_uncompressed.xex
    pos = 0
    while True:
        pos = xex_bytes.find(pat, pos)
        if pos == -1:
            break
        va_flat = 0x82000000 + (pos - 0x3000)
        # Also PE section mapping: pos in PE = pos - 0x3000
        # If in .text (rawptr 0x1A8000, va 0x1B2200):
        pe_pos = pos - 0x3000
        va_pe = 0x821B2200 + (pe_pos - 0x001A8000) if pe_pos >= 0x001A8000 else 0
        print(f"  NeverDead_uncompressed.xex: found at FileOff=0x{pos:08X} (Flat VA = 0x{va_flat:08X}, PE mapped VA = 0x{va_pe:08X})")
        pos += 1
