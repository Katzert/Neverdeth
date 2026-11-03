with open('src/crash_diag.cpp', 'rb') as f:
    raw = f.read()

target = b'''      if (VirtualProtect(page_addr, 0x1000, PAGE_EXECUTE_READWRITE, &old_prot) ||
          VirtualProtect(page_addr, 0x1000, PAGE_READWRITE, &old_prot)) {
        if (++s_prot_fault_cnt <= 20) {
          FILE* f = fopen("crash_diag.log", "a");
          if (f) {
            fprintf(f, "[VEH-AUTO-REPAIR #%u] prot fault (%s) at 0x%llX rip=0x%llX (page %p) old_prot=0x%lX -> resumed\\n",
                    s_prot_fault_cnt, er->ExceptionInformation[0] == 1 ? "write" : "read",
                    (unsigned long long)fault_addr, (unsigned long long)er->ExceptionAddress,
                    page_addr, (unsigned long)old_prot);
            fclose(f);
          }
        }
        return EXCEPTION_CONTINUE_EXECUTION;
      }'''

replacement = b'''      if (VirtualProtect(page_addr, 0x1000, PAGE_EXECUTE_READWRITE, &old_prot) ||
          VirtualProtect(page_addr, 0x1000, PAGE_READWRITE, &old_prot)) {
        if (++s_prot_fault_cnt <= 20) {
          FILE* f = fopen("crash_diag.log", "a");
          if (f) {
            fprintf(f, "[VEH-AUTO-REPAIR #%u] prot fault (%s) at 0x%llX rip=0x%llX (page %p) old_prot=0x%lX -> resumed\\n",
                    s_prot_fault_cnt, er->ExceptionInformation[0] == 1 ? "write" : "read",
                    (unsigned long long)fault_addr, (unsigned long long)er->ExceptionAddress,
                    page_addr, (unsigned long)old_prot);
            fclose(f);
          }
          return EXCEPTION_CONTINUE_EXECUTION;
        }
        // Stop VEH infinite loop on unfixable protection faults: pass to debugger
        return EXCEPTION_CONTINUE_SEARCH;
      }'''

if target.replace(b'\n', b'\r\n') in raw:
    raw = raw.replace(target.replace(b'\n', b'\r\n'), replacement.replace(b'\n', b'\r\n'))
    print("Replaced CRLF in crash_diag.cpp")
elif target in raw:
    raw = raw.replace(target, replacement)
    print("Replaced LF in crash_diag.cpp")
else:
    print("Target not found in crash_diag.cpp!")

with open('src/crash_diag.cpp', 'wb') as f:
    f.write(raw)
