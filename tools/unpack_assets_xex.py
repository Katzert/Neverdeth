#!/usr/bin/env python3
import struct
import os
from Crypto.Cipher import AES

RETAIL_KEY = bytes([0x20, 0xB1, 0x85, 0xA5, 0x9D, 0x28, 0xFD, 0xC3,
                    0x40, 0x58, 0x3F, 0xBB, 0x08, 0x96, 0xBF, 0x91])

with open("assets/default.xex", "rb") as f:
    xex_assets = f.read()

magic, mod_flags, header_size, reserved, sec_offset, header_count = struct.unpack(">IIIIII", xex_assets[:24])
enc_aes_key = xex_assets[sec_offset + 0x150 : sec_offset + 0x160]

cipher = AES.new(RETAIL_KEY, AES.MODE_CBC, iv=b"\x00"*16)
session_key = cipher.decrypt(enc_aes_key)
print(f"session_key: {session_key.hex()}")

# Find XEX_HEADER_FILE_FORMAT_INFO (0x000003FF)
pos = 24
ffi_offset = None
for i in range(header_count):
    k, v = struct.unpack(">II", xex_assets[pos:pos+8])
    pos += 8
    if k == 0x000003FF:
        ffi_offset = v
        break

print(f"FileFormatInfo offset: 0x{ffi_offset:X}")
info_size, enc_type, comp_type = struct.unpack(">IHH", xex_assets[ffi_offset:ffi_offset+8])
print(f"info_size: 0x{info_size:X}, enc_type: {enc_type}, comp_type: {comp_type}")

block_count = (info_size - 8) // 8
blocks = []
b_pos = ffi_offset + 8
for i in range(block_count):
    d_sz, z_sz = struct.unpack(">II", xex_assets[b_pos:b_pos+8])
    b_pos += 8
    blocks.append((d_sz, z_sz))

print(f"Number of basic compression blocks: {len(blocks)}")
for i, (d, z) in enumerate(blocks):
    print(f"  block {i:2d}: data_size=0x{d:08X}, zero_size=0x{z:08X} (total 0x{d+z:08X})")

# Unpack!
total_uncompressed = sum(d + z for d, z in blocks)
print(f"Total uncompressed size: 0x{total_uncompressed:X} ({total_uncompressed:,} bytes)")

# Decrypt blocks with AES-128-CBC chained
# In CBC across blocks, the IV of block N is the last 16 bytes of ciphertext of block N-1
out = bytearray(total_uncompressed)
d_ptr = 0
p_ptr = header_size

iv = b"\x00" * 16
for i, (d_sz, z_sz) in enumerate(blocks):
    if d_sz > 0:
        chunk = xex_assets[p_ptr : p_ptr + d_sz]
        cipher = AES.new(session_key, AES.MODE_CBC, iv=iv)
        pt = cipher.decrypt(chunk)
        iv = chunk[-16:] # last block of ciphertext becomes next IV
        out[d_ptr : d_ptr + d_sz] = pt
        p_ptr += d_sz
    d_ptr += d_sz + z_sz

print(f"Unpacked output length: 0x{len(out):X}")
print(f"First 16 bytes of unpacked: {out[:16].hex()} (MZ: {out[:2] == b'MZ'})")

# Compare out against NeverDead_base.exe
with open("NeverDead_base.exe", "rb") as f:
    exe_data = f.read()

print(f"NeverDead_base.exe length: 0x{len(exe_data):X}")
if len(out) == len(exe_data):
    if out == exe_data:
        print(">>> OUT IS 100% IDENTICAL TO NeverDead_base.exe! <<<")
    else:
        diffs = sum(1 for a, b in zip(out, exe_data) if a != b)
        print(f">>> OUT DIFFERS FROM NeverDead_base.exe BY {diffs:,} BYTES! <<<")
        for i, (a, b) in enumerate(zip(out, exe_data)):
            if a != b:
                print(f"    First diff at 0x{i:08X}: unpacked=0x{a:02X}, exe=0x{b:02X}")
                break
else:
    print(f"Lengths differ: out=0x{len(out):X}, exe=0x{len(exe_data):X}")
