#!/usr/bin/env python3
import struct

with open("assets/default.xex", "rb") as f:
    f_assets = f.read()

with open("../NeverDead_uncompressed.xex", "rb") as f:
    f_uncomp = f.read()

# Size of raw file
print(f"assets size: {len(f_assets):,} bytes (0x{len(f_assets):X})")
print(f"uncomp size: {len(f_uncomp):,} bytes (0x{len(f_uncomp):X})")
print(f"Difference in file size: {len(f_assets) - len(f_uncomp)} bytes (0x{len(f_assets) - len(f_uncomp):X})")

# Look at block 1 in both:
# In assets: block 0 data is 0x198000. So block 1 starts at header_size (0x3000) + 0x198000 = 0x19B000.
# block 1 data_size in assets is 0x628000.
# In uncomp: block 1 data_size is 0x620000.
# Difference is 0x8000 (32,768 bytes)!
# Exactly 32,768 bytes = 0x8000!
