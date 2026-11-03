#!/usr/bin/env python3
"""m3b_fix_handoff_encoding.py - undo the Windows-1252 -> UTF-8 double encoding
that PowerShell's Get-Content/Set-Content round-trip introduced on handoff.md."""
import sys
from pathlib import Path

# Reverse table: byte -> the char PowerShell's ANSI decoder produced.
REV = {}
for b in range(0x00, 0x100):
    try:
        REV[b] = bytes([b]).decode("cp1252")
    except UnicodeDecodeError:
        REV[b] = chr(b)          # undefined cp1252 slot -> latin-1 control char
BACK = {c: b for b, c in REV.items()}

path = Path(sys.argv[1])
raw = path.read_bytes()
text = raw.decode("utf-8-sig")
out = bytearray()
missing = 0
for ch in text:
    b = BACK.get(ch)
    if b is None:
        missing += 1
        b = ord(ch) & 0xFF
    out.append(b)
fixed = bytes(out)
try:
    fixed.decode("utf-8")
except UnicodeDecodeError as exc:
    print(f"NOT recoverable as UTF-8: {exc}")
    sys.exit(2)
path.write_bytes(fixed)
print(f"recovered {path} : {len(raw)} -> {len(fixed)} bytes, unmapped chars = {missing}")
print("check:", fixed[:70].decode("utf-8", "replace"))
