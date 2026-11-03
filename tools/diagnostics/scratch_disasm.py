with open('../NeverDead_base.exe', 'rb') as f:
    data = f.read()

off = 0x3C0DC0
with open('disasm_3c0dc0.txt', 'w') as out:
    for i in range(30):
        va = 0x82000000 + off + i * 4
        chunk = data[off + i*4 : off + i*4 + 4]
        val = int.from_bytes(chunk, 'big')
        out.write(f"0x{va:08X}: 0x{val:08X}\n")

print("done 3c0dc0")
