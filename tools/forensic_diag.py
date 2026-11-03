#!/usr/bin/env python3
import struct
import os
import sys

BASE_DIR = r"c:\Users\katze\.gemini\antigravity-ide\scratch\vibe-recomp\neverdead_project"
EXE_PATH = os.path.join(BASE_DIR, "NeverDead_base.exe")
XEX_UNCOMP = os.path.join(BASE_DIR, "..", "NeverDead_uncompressed.xex")
XEX_ASSETS = os.path.join(BASE_DIR, "assets", "default.xex")

def parse_pe_sections(filename, base_file_offset=0):
    print(f"\n=======================================================")
    print(f"PE Analysis: {filename} (Offset: 0x{base_file_offset:X})")
    print(f"=======================================================")
    with open(filename, "rb") as f:
        f.seek(base_file_offset)
        dos = f.read(64)
        if len(dos) < 64 or dos[:2] != b"MZ":
            print("Not a valid MZ header!")
            return None
        e_lfanew = struct.unpack("<I", dos[60:64])[0]
        f.seek(base_file_offset + e_lfanew)
        sig = f.read(4)
        if sig != b"PE\x00\x00":
            print(f"Invalid PE sig: {sig}")
            return None
        coff = f.read(20)
        machine, num_sec, timedate, sym_ptr, num_sym, opt_sz, charact = struct.unpack("<HHIIIHH", coff)
        opt = f.read(opt_sz)
        magic, = struct.unpack("<H", opt[:2])
        image_base, = struct.unpack("<I", opt[28:32])
        sec_align, = struct.unpack("<I", opt[32:36])
        file_align, = struct.unpack("<I", opt[36:40])
        print(f"Machine: 0x{machine:04X}, Sections: {num_sec}, OptHeaderSize: {opt_sz}")
        print(f"ImageBase: 0x{image_base:08X}, SectionAlign: 0x{sec_align:X}, FileAlign: 0x{file_align:X}")
        
        # Exception Table (.pdata) is entry 3
        # Data directories start at offset 96 in 32-bit PE
        rva_pdata, sz_pdata = struct.unpack("<II", opt[96 + 3*8 : 96 + 3*8 + 8])
        print(f"Exception Table (.pdata) DataDir: RVA=0x{rva_pdata:08X}, Size=0x{sz_pdata:X}")

        sections = []
        for i in range(num_sec):
            sec = f.read(40)
            name = sec[:8].rstrip(b"\x00").decode("ascii", errors="replace")
            vsize, vaddr, rsize, raddr, _, _, _, _, characts = struct.unpack("<IIIIIIHHI", sec[8:])
            file_raddr = base_file_offset + raddr
            sections.append({
                "name": name,
                "vaddr": vaddr,
                "vsize": vsize,
                "raddr": raddr,
                "file_raddr": file_raddr,
                "rsize": rsize,
                "characts": characts
            })
            print(f"  Sec {i:2d}: {name:8s} VA=0x{vaddr:08X} (sz 0x{vsize:06X}) | RawPtr=0x{raddr:08X} -> FileOff=0x{file_raddr:08X} (sz 0x{rsize:06X})")
        return {
            "image_base": image_base,
            "pdata_rva": rva_pdata,
            "pdata_size": sz_pdata,
            "sections": sections
        }

pe_exe = parse_pe_sections(EXE_PATH, 0)
pe_xex_uncomp = parse_pe_sections(XEX_UNCOMP, 0x3000)
