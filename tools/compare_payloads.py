#!/usr/bin/env python3
import struct
import os
from Crypto.Cipher import AES

RETAIL_KEY = bytes([0x20, 0xB1, 0x85, 0xA5, 0x9D, 0x28, 0xFD, 0xC3,
                    0x40, 0x58, 0x3F, 0xBB, 0x08, 0x96, 0xBF, 0x91])

with open("assets/default.xex", "rb") as f:
    xex_assets = f.read()

with open("../NeverDead_uncompressed.xex", "rb") as f:
    xex_uncomp = f.read()

magic, mod_flags, header_size, reserved, sec_offset, header_count = struct.unpack(">IIIIII", xex_assets[:24])
enc_aes_key = xex_assets[sec_offset + 0x150 : sec_offset + 0x160]

cipher = AES.new(RETAIL_KEY, AES.MODE_CBC, iv=b"\x00"*16)
session_key = cipher.decrypt(enc_aes_key)

payload_assets = xex_assets[header_size:]
p_cipher = AES.new(session_key, AES.MODE_CBC, iv=b"\x00"*16)
dec_assets = p_cipher.decrypt(payload_assets)

payload_uncomp = xex_uncomp[header_size:]

print(f"Decrypted assets/default.xex payload size: {len(dec_assets):,} bytes")
print(f"NeverDead_uncompressed.xex payload size:    {len(payload_uncomp):,} bytes")

min_len = min(len(dec_assets), len(payload_uncomp))
print(f"Comparing first {min_len:,} bytes:")
if dec_assets[:min_len] == payload_uncomp[:min_len]:
    print("  -> First bytes match 100% EXACTLY!")
else:
    diffs = sum(1 for a, b in zip(dec_assets[:min_len], payload_uncomp[:min_len]) if a != b)
    print(f"  -> Differs by {diffs} bytes!")
    # Let's find first diff
    for idx, (a, b) in enumerate(zip(dec_assets[:min_len], payload_uncomp[:min_len])):
        if a != b:
            print(f"     First diff at offset 0x{idx:X}: dec=0x{a:02X}, uncomp=0x{b:02X}")
            break
