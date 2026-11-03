with open('src/neverdead_app.h', 'rb') as f:
    raw = f.read()

target = b'''#ifdef _WIN32
    // CRITICO: Desbloquear permisos de escritura en todo el rango [0x82000000, 0x82C10000)
    // antes de fread() porque Windows inicia la arena de .rdata como PAGE_READONLY.
    DWORD old_prot = 0;
    VirtualProtect(guest_base, 0x00C10000u, PAGE_EXECUTE_READWRITE, &old_prot);
#endif'''

replacement = b'''#ifdef _WIN32
    // CRITICO: Desbloquear permisos de escritura en cada region de [0x82000000, 0x82C10000)
    // iterando via VirtualQuery (porque VirtualProtect falla si el rango cruza multiples regiones MBI).
    {
      uint8_t* curr = guest_base;
      uint8_t* end = guest_base + 0x00C10000u;
      size_t count = 0;
      while (curr < end) {
        MEMORY_BASIC_INFORMATION mbi{};
        if (!VirtualQuery(curr, &mbi, sizeof(mbi))) break;
        if (mbi.State == MEM_COMMIT && mbi.Protect != PAGE_EXECUTE_READWRITE && mbi.Protect != PAGE_READWRITE) {
          DWORD old_prot = 0;
          DWORD target_prot = (mbi.AllocationProtect & 0xF0) ? PAGE_EXECUTE_READWRITE : PAGE_READWRITE;
          if (VirtualProtect(mbi.BaseAddress, mbi.RegionSize, target_prot, &old_prot)) {
            ++count;
          } else if (VirtualProtect(mbi.BaseAddress, mbi.RegionSize, PAGE_READWRITE, &old_prot)) {
            ++count;
          }
        }
        curr = reinterpret_cast<uint8_t*>(mbi.BaseAddress) + mbi.RegionSize;
      }
      REXLOG_INFO("neverdead: MapBaseExeSections unlocked {} memory regions to writable", count);
    }
#endif'''

# Try with CRLF and LF
if target.replace(b'\n', b'\r\n') in raw:
    raw = raw.replace(target.replace(b'\n', b'\r\n'), replacement.replace(b'\n', b'\r\n'))
    print("Replaced CRLF")
elif target in raw:
    raw = raw.replace(target, replacement)
    print("Replaced LF")
else:
    print("Target not found!")

with open('src/neverdead_app.h', 'wb') as f:
    f.write(raw)
