#!/usr/bin/env python3
import struct
import os

BASE_DIR = r"c:\Users\katze\.gemini\antigravity-ide\scratch\vibe-recomp\neverdead_project"
EXE_PATH = os.path.join(BASE_DIR, "NeverDead_base.exe")
XEX_UNCOMP = os.path.join(BASE_DIR, "..", "NeverDead_uncompressed.xex")
XEX_ASSETS = os.path.join(BASE_DIR, "assets", "default.xex")

with open(EXE_PATH, "rb") as f:
    exe_data = f.read()

with open(XEX_UNCOMP, "rb") as f:
    xex_uncomp = f.read()

with open(XEX_ASSETS, "rb") as f:
    xex_assets = f.read()

print(f"EXE size: 0x{len(exe_data):X}")
print(f"XEX_UNCOMP size: 0x{len(xex_uncomp):X}")
print(f"XEX_ASSETS size: 0x{len(xex_assets):X}")

# Let's check XEX header of assets/default.xex
# Certificate and encryption
magic, mod_flags, pe_offset, res, cert_offset, opt_count = struct.unpack('>4sIIIII', xex_assets[:24])
print(f"XEX_ASSETS: magic={magic}, pe_offset=0x{pe_offset:X}, cert_offset=0x{cert_offset:X}, opt_count={opt_count}")

# Check security info in XEX_ASSETS
# cert_offset usually has security info
sec_info = xex_assets[cert_offset : cert_offset + 0x180]
image_size, = struct.unpack('>I', sec_info[0x100:0x104]) # or let's find load_address & image_size
print(f"sec_info hex: {sec_info[:64].hex()}")

# Let's inspect opt headers of assets/default.xex
pos = 24
for i in range(opt_count):
    k, v = struct.unpack('>II', xex_assets[pos:pos+8])
    pos += 8
    # 0x000003FF = File format / compression info
    if k == 0x000003FF:
        info = xex_assets[v:v+32]
        info_sz, enc_type, comp_type = struct.unpack('>IHH', info[:8])
        print(f"  Compression info @ 0x{v:X}: info_sz=0x{info_sz:X}, enc_type={enc_type}, comp_type={comp_type}")
        # comp_type: 1 = uncompressed, 2 = basic, 3 = normal
    elif k == 0x00010201:
        print(f"  Image base address: 0x{v:08X}")
    elif k == 0x00010100:
        print(f"  Entry point: 0x{v:08X}")
    elif k == 0x000200FF:
        print(f"  Page descriptors descriptor @ 0x{v:X}")
