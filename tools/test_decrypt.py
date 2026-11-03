#!/usr/bin/env python3
import struct
import os
from Crypto.Cipher import AES

RETAIL_KEY = bytes([0x20, 0xB1, 0x85, 0xA5, 0x9D, 0x28, 0xFD, 0xC3,
                    0x40, 0x58, 0x3F, 0xBB, 0x08, 0x96, 0xBF, 0x91])
DEVKIT_KEY = bytes([0x00] * 16)

with open("assets/default.xex", "rb") as f:
    xex_data = f.read()

magic, mod_flags, header_size, reserved, sec_offset, header_count = struct.unpack(">IIIIII", xex_data[:24])
print(f"magic: 0x{magic:08X}, header_size: 0x{header_size:X}, sec_offset: 0x{sec_offset:X}")

enc_aes_key = xex_data[sec_offset + 0x150 : sec_offset + 0x160]
print(f"Encrypted AES key in sec_info: {enc_aes_key.hex()}")

for key_name, key in [("Retail", RETAIL_KEY), ("Devkit", DEVKIT_KEY)]:
    cipher = AES.new(key, AES.MODE_CBC, iv=b"\x00"*16)
    session_key = cipher.decrypt(enc_aes_key)
    print(f"Session key ({key_name}): {session_key.hex()}")
    
    # Try decrypting first 64 bytes of payload
    payload = xex_data[header_size : header_size + 64]
    p_cipher = AES.new(session_key, AES.MODE_CBC, iv=b"\x00"*16)
    dec = p_cipher.decrypt(payload)
    print(f"Decrypted first 16 bytes ({key_name}): {dec[:16]!r} (hex: {dec[:16].hex()})")
