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

pos = 24
ffi_offset = None
for i in range(header_count):
    k, v = struct.unpack(">II", xex_assets[pos:pos+8])
    pos += 8
    if k == 0x000003FF:
        ffi_offset = v
        break

info_size, enc_type, comp_type = struct.unpack(">IHH", xex_assets[ffi_offset:ffi_offset+8])
block_count = (info_size - 8) // 8
blocks = []
b_pos = ffi_offset + 8
for i in range(block_count):
    d_sz, z_sz = struct.unpack(">II", xex_assets[b_pos:b_pos+8])
    b_pos += 8
    blocks.append((d_sz, z_sz))

total_uncompressed = sum(d + z for d, z in blocks)
assets_mapped = bytearray(total_uncompressed)
d_ptr = 0
p_ptr = header_size
iv = b"\x00" * 16
for i, (d_sz, z_sz) in enumerate(blocks):
    if d_sz > 0:
        chunk = xex_assets[p_ptr : p_ptr + d_sz]
        cipher = AES.new(session_key, AES.MODE_CBC, iv=iv)
        pt = cipher.decrypt(chunk)
        iv = chunk[-16:]
        assets_mapped[d_ptr : d_ptr + d_sz] = pt
        p_ptr += d_sz
    d_ptr += d_sz + z_sz

# Now unpack NeverDead_uncompressed.xex
with open("../NeverDead_uncompressed.xex", "rb") as f:
    xex_uncomp = f.read()

magic, mod_flags, header_size, reserved, sec_offset, header_count = struct.unpack(">IIIIII", xex_uncomp[:24])
pos = 24
ffi_offset = None
for i in range(header_count):
    k, v = struct.unpack(">II", xex_uncomp[pos:pos+8])
    pos += 8
    if k == 0x000003FF:
        ffi_offset = v
        break

info_size, enc_type, comp_type = struct.unpack(">IHH", xex_uncomp[ffi_offset:ffi_offset+8])
block_count = (info_size - 8) // 8
blocks_u = []
b_pos = ffi_offset + 8
for i in range(block_count):
    d_sz, z_sz = struct.unpack(">II", xex_uncomp[b_pos:b_pos+8])
    b_pos += 8
    blocks_u.append((d_sz, z_sz))

total_uncompressed_u = sum(d + z for d, z in blocks_u)
uncomp_mapped = bytearray(total_uncompressed_u)
d_ptr = 0
p_ptr = header_size
for i, (d_sz, z_sz) in enumerate(blocks_u):
    if d_sz > 0:
        uncomp_mapped[d_ptr : d_ptr + d_sz] = xex_uncomp[p_ptr : p_ptr + d_sz]
        p_ptr += d_sz
    d_ptr += d_sz + z_sz

with open("NeverDead_base.exe", "rb") as f:
    base_exe = f.read()

print(f"assets_mapped:  len=0x{len(assets_mapped):X}")
print(f"uncomp_mapped:  len=0x{len(uncomp_mapped):X}")
print(f"NeverDead_base: len=0x{len(base_exe):X}")

# Let's search critical patterns in all three!
patterns = {
    "savegprlr_14 (f9c1ff68f9e1ff70)": bytes.fromhex("f9c1ff68f9e1ff70"),
    "sub_8271E508 in recomp (409800087d795b78)": bytes.fromhex("409800087d795b78"),
    "mflr at 71D508 (7d8802a64806e831)": bytes.fromhex("7d8802a64806e831"),
}

for name, pat in patterns.items():
    print(f"\n=== Pattern: {name} ===")
    for bname, buf in [("assets_mapped", assets_mapped), 
                       ("uncomp_mapped", uncomp_mapped), 
                       ("NeverDead_base.exe", base_exe)]:
        pos = 0
        hits = []
        while True:
            pos = buf.find(pat, pos)
            if pos == -1:
                break
            va = 0x82000000 + pos
            hits.append((pos, va))
            pos += 1
        print(f"  {bname:20s}: {len(hits)} hits")
        for p, va in hits:
            print(f"    offset=0x{p:08X} -> Flat VA=0x{va:08X}")
