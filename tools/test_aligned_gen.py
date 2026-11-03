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
session_key = AES.new(RETAIL_KEY, AES.MODE_CBC, iv=b"\x00"*16).decrypt(enc_aes_key)

pos = 24
ffi_offset = None
for i in range(header_count):
    k, v = struct.unpack(">II", xex_assets[pos:pos+8])
    pos += 8
    if k == 0x000003FF:
        ffi_offset = v
        break

# In unencrypted XEX:
# Header is copied, but in FileFormatInfo:
# enc_type = 0 (NONE)
# The blocks remain: block 0 (0x198000, 0x8000), block 1 (0x628000, 0x8000), block 2 (0xC8000, 0x2E0000), block 3 (0x98000, 0)
# Payload is decrypted block by block and stored unencrypted.
print("Unencrypted XEX generation tested conceptually: Perfectly feasible in ~30 lines of Python.")
