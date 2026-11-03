#!/usr/bin/env python3
import struct
import os

BASE_DIR = r"c:\Users\katze\.gemini\antigravity-ide\scratch\vibe-recomp\neverdead_project"
EXE_PATH = os.path.join(BASE_DIR, "NeverDead_base.exe")
XEX_UNCOMP = os.path.join(BASE_DIR, "..", "NeverDead_uncompressed.xex")

PDATA_RVA = 0x16F800
PDATA_SIZE = 0x238D0
TEXT_LO = 0x821B2200
TEXT_HI = 0x827C05E4

def parse_pdata_from_bytes(raw_bytes, source_name):
    print(f"\n--- Parsing .pdata from {source_name} (len: {len(raw_bytes):,} bytes) ---")
    num_records = len(raw_bytes) // 8
    print(f"Total 8-byte records: {num_records}")
    
    entries = []
    text_entries = []
    misaligned = []
    size_zero = 0
    
    for i in range(num_records):
        start, packed = struct.unpack(">II", raw_bytes[i*8 : (i+1)*8])
        if start == 0 and packed == 0:
            continue
        
        prolog_len = packed & 0xFF
        func_len_instructions = (packed >> 8) & 0x3FFFFF
        func_size_bytes = func_len_instructions * 4
        is_32bit = (packed >> 30) & 1
        has_exception = (packed >> 31) & 1
        
        entry = {
            "index": i,
            "start": start,
            "packed": packed,
            "prolog_len": prolog_len,
            "func_len_insn": func_len_instructions,
            "size": func_size_bytes,
            "is_32bit": is_32bit,
            "has_exception": has_exception
        }
        entries.append(entry)
        
        if TEXT_LO <= start < TEXT_HI:
            if (start & 3) != 0:
                misaligned.append(entry)
            else:
                text_entries.append(entry)
                if func_size_bytes == 0:
                    size_zero += 1

    print(f"Non-null entries: {len(entries)}")
    print(f"Entries in .text range [0x{TEXT_LO:08X}, 0x{TEXT_HI:08X}): {len(text_entries)}")
    print(f"Misaligned entries in .text: {len(misaligned)}")
    for m in misaligned:
        print(f"  Misaligned: idx={m['index']}, start=0x{m['start']:08X}, size=0x{m['size']:X}")
    print(f"Entries with size == 0: {size_zero}")
    
    return entries, text_entries

with open(EXE_PATH, "rb") as f:
    exe_pdata = f.read()[PDATA_RVA : PDATA_RVA + PDATA_SIZE]

with open(XEX_UNCOMP, "rb") as f:
    f.seek(0x3000 + PDATA_RVA)
    xex_pdata = f.read(PDATA_SIZE)

entries_exe, text_exe = parse_pdata_from_bytes(exe_pdata, "NeverDead_base.exe @ RVA 0x16F800")
entries_xex, text_xex = parse_pdata_from_bytes(xex_pdata, "NeverDead_uncompressed.xex @ 0x3000 + 0x16F800")
