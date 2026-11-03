#!/usr/bin/env python3
import struct
import os

BASE_DIR = r"c:\Users\katze\.gemini\antigravity-ide\scratch\vibe-recomp\neverdead_project"
EXE_PATH = os.path.join(BASE_DIR, "NeverDead_base.exe")
XEX_UNCOMP = os.path.join(BASE_DIR, "..", "NeverDead_uncompressed.xex")

with open(EXE_PATH, "rb") as f:
    exe_data = f.read()

with open(XEX_UNCOMP, "rb") as f:
    xex_uncomp = f.read()

# Let's inspect bytes in NeverDead_base.exe at:
# 1) Flat offset: VA - 0x82000000
# 2) Flat - 0x1000: VA - 0x82000000 - 0x1000
# 3) Flat - 0x2000: VA - 0x82000000 - 0x2000
# 4) PE raw: PointerToRawData + (VA - 0x821B2200)

def dump_candidates(va_list):
    for va in va_list:
        print(f"\n=======================================================")
        print(f"Target VA: 0x{va:08X}")
        print(f"=======================================================")
        
        # In NeverDead_base.exe
        flat_off = va - 0x82000000
        pe_off_exe = 0x001A8000 + (va - 0x821B2200) # .text rawptr in exe
        
        print("NeverDead_base.exe:")
        for name, off in [("flat (shift 0)", flat_off), 
                          ("flat - 0x1000", flat_off - 0x1000), 
                          ("flat - 0x2000", flat_off - 0x2000),
                          ("PE raw ptr (shift 0xA200)", pe_off_exe)]:
            if 0 <= off < len(exe_data) - 16:
                words = struct.unpack(">4I", exe_data[off : off + 16])
                hex_str = exe_data[off : off + 16].hex()
                print(f"  {name:25s} (off 0x{off:08X}): {hex_str} | words={[hex(w) for w in words]}")
            else:
                print(f"  {name:25s} (off 0x{off:08X}): OUT OF BOUNDS")

        # In NeverDead_uncompressed.xex
        # Note: In XEX, header is 0x3000 bytes.
        # rexglue loads xex by copying from 0x3000 to guest 0x82000000
        # If rexglue copies flatly from 0x3000:
        xex_flat_off = 0x3000 + flat_off
        pe_off_xex = 0x3000 + 0x001A8000 + (va - 0x821B2200)
        
        print("NeverDead_uncompressed.xex:")
        for name, off in [("xex flat (0x3000 + flat)", xex_flat_off),
                          ("xex flat - 0x1000", xex_flat_off - 0x1000),
                          ("xex flat - 0x2000", xex_flat_off - 0x2000),
                          ("xex PE raw (0x3000 + PE)", pe_off_xex)]:
            if 0 <= off < len(xex_uncomp) - 16:
                words = struct.unpack(">4I", xex_uncomp[off : off + 16])
                hex_str = xex_uncomp[off : off + 16].hex()
                print(f"  {name:25s} (off 0x{off:08X}): {hex_str} | words={[hex(w) for w in words]}")
            else:
                print(f"  {name:25s} (off 0x{off:08X}): OUT OF BOUNDS")

dump_candidates([0x8271E508, 0x8271D508, 0x8278CD10, 0x8278BD10])
