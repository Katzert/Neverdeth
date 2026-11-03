#!/usr/bin/env python3
import struct
import os
from collections import Counter
from Crypto.Cipher import AES

RETAIL_KEY = bytes([0x20, 0xB1, 0x85, 0xA5, 0x9D, 0x28, 0xFD, 0xC3,
                    0x40, 0x58, 0x3F, 0xBB, 0x08, 0x96, 0xBF, 0x91])

# 1. Map assets/default.xex
with open("assets/default.xex", "rb") as f:
    xex_assets = f.read()

magic, mod_flags, header_size, reserved, sec_offset, header_count = struct.unpack(">IIIIII", xex_assets[:24])
enc_aes_key = xex_assets[sec_offset + 0x150 : sec_offset + 0x160]
session_key = AES.new(RETAIL_KEY, AES.MODE_CBC, iv=b"\x00"*16).decrypt(enc_aes_key)

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

# 2. Load NeverDead_base.exe
with open("NeverDead_base.exe", "rb") as f:
    base_exe = f.read()

# 3. Parse .pdata from assets_mapped
# .pdata is at RVA 0x16F800, size 0x238D0
PDATA_RVA = 0x16F800
PDATA_SIZE = 0x238D0
pdata_raw = assets_mapped[PDATA_RVA : PDATA_RVA + PDATA_SIZE]
num_entries = len(pdata_raw) // 8

MFLR = (0x7D8802A6, 0x7D8803A6)
def is_prologue(data, off):
    if off < 0 or off + 4 > len(data):
        return False
    w, = struct.unpack(">I", data[off : off + 4])
    if w in MFLR:
        return True
    if (w >> 26) == 37 and ((w >> 16) & 0x1F) == 1:  # stwu r1,-N(r1)
        return True
    if (w >> 26) == 62 and ((w >> 16) & 0x1F) == 1:  # std rN,-off(r1)
        return True
    if (w >> 26) == 54 and ((w >> 16) & 0x1F) == 1:  # stfd fN,-off(r1)
        return True
    return False

TEXT_LO, TEXT_HI = 0x821B2200, 0x827C05E4

begins = []
for i in range(num_entries):
    start, packed = struct.unpack(">II", pdata_raw[i*8 : (i+1)*8])
    if TEXT_LO <= start < TEXT_HI and (start & 3) == 0:
        begins.append(start)

print(f"Total valid .text entries in .pdata: {len(begins)}")

# Test shift 0 in assets_mapped vs base_exe
prologues_assets_s0 = sum(1 for b in begins if is_prologue(assets_mapped, b - 0x82000000))
prologues_base_s0 = sum(1 for b in begins if is_prologue(base_exe, b - 0x82000000))

print(f"assets_mapped: valid prologues at shift 0: {prologues_assets_s0} / {len(begins)} ({prologues_assets_s0/len(begins)*100:.2f}%)")
print(f"NeverDead_base.exe: valid prologues at shift 0: {prologues_base_s0} / {len(begins)} ({prologues_base_s0/len(begins)*100:.2f}%)")

# Detailed check of shifts in assets_mapped
shifts = (0, 0x1000, 0x2000, -0x1000, -0x2000)
shift_counts_assets = Counter()
for b in begins:
    cands = [s for s in shifts if is_prologue(assets_mapped, b - 0x82000000 - s)]
    if len(cands) == 1:
        shift_counts_assets[cands[0]] += 1
    elif len(cands) > 1:
        shift_counts_assets["ambiguous"] += 1
    else:
        shift_counts_assets["none"] += 1

print(f"assets_mapped shift distribution: {dict(shift_counts_assets)}")

shift_counts_base = Counter()
for b in begins:
    cands = [s for s in shifts if is_prologue(base_exe, b - 0x82000000 - s)]
    if len(cands) == 1:
        shift_counts_base[cands[0]] += 1
    elif len(cands) > 1:
        shift_counts_base["ambiguous"] += 1
    else:
        shift_counts_base["none"] += 1

print(f"NeverDead_base.exe shift distribution: {dict(shift_counts_base)}")
