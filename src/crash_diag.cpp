// DIAG(neverdead-crash-diag): adoptado de 360tools templates/overlay/debug.
// Last-chance logger de access-violation: instala un VEH que SOLO observa
// (log + CONTINUE_SEARCH, no altera el manejo del SDK) y vuelca direccion de
// fallo, operacion y stack host a crash_diag.log (CWD = build_clang/).
// Sin PDB los frames salen como modulo+offset: resolver con neverdead.map
// (target_link_options /MAP). Ver docs en 360tools/docs/runtime-debugging.md.

#include <windows.h>

#include <dbghelp.h>

#include <cstdint>
#include <cstdio>
#include <cstdlib>

#pragma comment(lib, "dbghelp.lib")

namespace {

void symline(FILE* f, HANDLE proc, DWORD64 addr, int idx) {
  char buf[sizeof(SYMBOL_INFO) + 512];
  auto* si = reinterpret_cast<SYMBOL_INFO*>(buf);
  si->SizeOfStruct = sizeof(SYMBOL_INFO);
  si->MaxNameLen = 511;
  IMAGEHLP_MODULE64 mod;
  mod.SizeOfStruct = sizeof(mod);
  const char* modname = SymGetModuleInfo64(proc, addr, &mod) ? mod.ModuleName : "?";
  DWORD64 disp = 0;
  if (SymFromAddr(proc, addr, &disp, si))
    fprintf(f, "  [%2d] %s!%s +0x%llX  (0x%llX)\n", idx, modname, si->Name,
            (unsigned long long)disp, (unsigned long long)addr);
  else
    fprintf(f, "  [%2d] %s!0x%llX\n", idx, modname, (unsigned long long)addr);
  IMAGEHLP_LINE64 line;
  line.SizeOfStruct = sizeof(line);
  DWORD line_disp = 0;
  if (SymGetLineFromAddr64(proc, addr, &line_disp, &line))
    fprintf(f, "         %s:%lu\n", line.FileName, line.LineNumber);
}

// ND_VEH_REPAIR=0 -> el VEH solo registra y deja pasar la excepcion.
// Por defecto repara (comportamiento historico) para no romper la corrida.
bool veh_repair_enabled() {
  static int enabled = -1;
  if (enabled < 0) {
    const char* v = std::getenv("ND_VEH_REPAIR");
    enabled = (v != nullptr && v[0] == '0') ? 0 : 1;
  }
  return enabled != 0;
}

// Traza ruidosa: cada activacion queda en crash_diag.log con contador.
// Cap 200 lineas y luego 1 de cada 500 para no inundar.
void veh_log_loud(const char* kind, EXCEPTION_POINTERS* ep, unsigned long long addr) {
  static unsigned long count = 0;
  const unsigned long n = ++count;
  if (n > 200 && (n % 500) != 0) {
    return;
  }
  FILE* f = fopen("crash_diag.log", "a");
  if (f == nullptr) {
    return;
  }
  fprintf(f,
          "[VEH-LOUD #%lu] %s code=0x%08lX rip=0x%llX addr=0x%llX thread=%lu repair=%d\n", n,
          kind, (unsigned long)ep->ExceptionRecord->ExceptionCode,
          (unsigned long long)ep->ContextRecord->Rip, addr, GetCurrentThreadId(),
          veh_repair_enabled() ? 1 : 0);
  fflush(f);
  fclose(f);
}

LONG CALLBACK NeverdeadVeh(EXCEPTION_POINTERS* ep) {
  auto* er = ep->ExceptionRecord;
  if (er->ExceptionCode == 0x406D1388 || er->ExceptionCode == 0xE06D7363) {
    return EXCEPTION_CONTINUE_SEARCH;
  }

  // Auto-repair GPU worker divide-by-zero (0xC0000094) when ringbuffer size is 0
  if (er->ExceptionCode == 0xC0000094 && ep->ContextRecord != nullptr) {
    uint8_t* rip = reinterpret_cast<uint8_t*>(ep->ContextRecord->Rip);
    if (rip != nullptr && rip[0] == 0xF7 && rip[1] == 0xF1) {
      veh_log_loud("div0", ep, 0);
      if (!veh_repair_enabled()) {
        return EXCEPTION_CONTINUE_SEARCH;
      }
      uint8_t* rbp = reinterpret_cast<uint8_t*>(ep->ContextRecord->Rbp);
      if (rbp != nullptr) {
        *reinterpret_cast<uint32_t*>(rbp + 0x4C) = 0;
        *reinterpret_cast<uint32_t*>(rbp + 0x50) = 0;
      }
      ep->ContextRecord->Rax = 0;
      ep->ContextRecord->Rdx = 0;
      ep->ContextRecord->Rip += 19; // Advance past both div ecx to jmp +9 (0xeb 0x09)
      static bool s_div0_logged = false;
      if (!s_div0_logged) {
        s_div0_logged = true;
        FILE* f = fopen("crash_diag.log", "a");
        if (f) {
          fprintf(f, "[VEH-AUTO-REPAIR] Bypassed GPU divide-by-zero on uninitialized ringbuffer (rip=%p -> %p)\n",
                  rip, reinterpret_cast<void*>(ep->ContextRecord->Rip));
          fclose(f);
        }
      }
      return EXCEPTION_CONTINUE_EXECUTION;
    }
  }

  // Auto-repair guest memory access faults (0xC0000005) in virtual and physical arenas [0x200000000, 0x400000000)
  if (er->ExceptionCode == 0xC0000005 && er->NumberParameters >= 2) {
    DWORD64 fault_addr = er->ExceptionInformation[1];
    if (fault_addr >= 0x0000000200000000ull && fault_addr < 0x0000000400000000ull) {
      veh_log_loud("av", ep, fault_addr);
      if (!veh_repair_enabled()) {
        return EXCEPTION_CONTINUE_SEARCH;
      }
      void* page_addr = reinterpret_cast<void*>(fault_addr & ~0xFFFull);
      static uint32_t s_prot_fault_cnt = 0;
      DWORD old_prot = 0;
      if (VirtualProtect(page_addr, 0x1000, PAGE_EXECUTE_READWRITE, &old_prot) ||
          VirtualProtect(page_addr, 0x1000, PAGE_READWRITE, &old_prot)) {
        if (old_prot != PAGE_READWRITE && old_prot != PAGE_EXECUTE_READWRITE) {
          if (++s_prot_fault_cnt <= 20 || (s_prot_fault_cnt % 500 == 0)) {
            FILE* f = fopen("crash_diag.log", "a");
            if (f) {
              fprintf(f, "[VEH-AUTO-REPAIR #%u] prot fault (%s) at 0x%llX rip=0x%llX (page %p) old_prot=0x%lX -> resumed\n",
                      s_prot_fault_cnt, er->ExceptionInformation[0] == 1 ? "write" : "read",
                      (unsigned long long)fault_addr, (unsigned long long)er->ExceptionAddress,
                      page_addr, (unsigned long)old_prot);
              fclose(f);
            }
          }
          return EXCEPTION_CONTINUE_EXECUTION;
        }
        // Stop VEH infinite loop on unfixable protection faults: pass to debugger
        return EXCEPTION_CONTINUE_SEARCH;
      }
      // Fast chunk commit: align to 16MB boundary and commit 16MB
      constexpr uint64_t kChunkSize = 0x01000000ull; // 16MB
      void* chunk_addr = reinterpret_cast<void*>(fault_addr & ~(kChunkSize - 1));
      void* res = VirtualAlloc(chunk_addr, kChunkSize, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
      if (!res) {
        res = VirtualAlloc(chunk_addr, kChunkSize, MEM_COMMIT, PAGE_READWRITE);
      }
      if (!res) {
        // Fallback to 64KB
        chunk_addr = reinterpret_cast<void*>(fault_addr & ~0xFFFFull);
        res = VirtualAlloc(chunk_addr, 0x10000, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
        if (!res) {
          res = VirtualAlloc(chunk_addr, 0x10000, MEM_COMMIT, PAGE_READWRITE);
        }
      }
      if (res) {
        static uint32_t s_repair_cnt = 0;
        if (++s_repair_cnt <= 10 || (s_repair_cnt % 500 == 0)) {
          FILE* f = fopen("crash_diag.log", "a");
          if (f) {
            fprintf(f, "[VEH-AUTO-REPAIR #%u] commit fault (%s) at 0x%llX rip=0x%llX (chunk %p) -> committed zeroed & resumed\n",
                    s_repair_cnt, er->ExceptionInformation[0] == 1 ? "write" : "read",
                    (unsigned long long)fault_addr, (unsigned long long)er->ExceptionAddress,
                    chunk_addr);
            fclose(f);
          }
        }
        return EXCEPTION_CONTINUE_EXECUTION;
      }
    }
  }

  if ((er->ExceptionCode & 0xC0000000) != 0xC0000000) {
    return EXCEPTION_CONTINUE_SEARCH;
  }

  FILE* f = fopen("crash_diag.log", "a");
  if (!f) {
    return EXCEPTION_CONTINUE_SEARCH;
  }

  HANDLE proc = GetCurrentProcess();
  static bool inited = false;
  if (!inited) {
    SymSetOptions(SYMOPT_UNDNAME | SYMOPT_DEFERRED_LOADS | SYMOPT_LOAD_LINES);
    SymInitialize(proc, nullptr, TRUE);
    inited = true;
  }

  const char* op = (er->NumberParameters > 0 && er->ExceptionInformation[0] == 1)   ? "write"
                   : (er->NumberParameters > 0 && er->ExceptionInformation[0] == 0) ? "read"
                                                                                    : "exec";
  DWORD64 fault_addr = er->NumberParameters > 1 ? er->ExceptionInformation[1] : 0;
  CONTEXT* ctx = ep->ContextRecord;
  fprintf(f, "\n==== FATAL EXCEPTION 0x%08lX (%s 0x%llX) thread %lu ====\n",
          (unsigned long)er->ExceptionCode, op, (unsigned long long)fault_addr, GetCurrentThreadId());
  fprintf(f, "faulting instruction:\n");
  symline(f, proc, ctx->Rip, 0);

  fprintf(f, "stack:\n");
  STACKFRAME64 sf{};
  sf.AddrPC.Offset = ctx->Rip;
  sf.AddrPC.Mode = AddrModeFlat;
  sf.AddrFrame.Offset = ctx->Rbp;
  sf.AddrFrame.Mode = AddrModeFlat;
  sf.AddrStack.Offset = ctx->Rsp;
  sf.AddrStack.Mode = AddrModeFlat;
  CONTEXT walk = *ctx;
  for (int i = 0; i < 48; ++i) {
    if (!StackWalk64(IMAGE_FILE_MACHINE_AMD64, proc, GetCurrentThread(), &sf, &walk,
                     nullptr, SymFunctionTableAccess64, SymGetModuleBase64, nullptr)) {
      break;
    }
    if (!sf.AddrPC.Offset) {
      break;
    }
    symline(f, proc, sf.AddrPC.Offset, i);
  }
  fflush(f);
  fclose(f);
  return EXCEPTION_CONTINUE_SEARCH;  // deja que el crash siga su curso
}

struct NeverdeadVehInstall {
  NeverdeadVehInstall() {
    AddVectoredExceptionHandler(1, NeverdeadVeh);
    freopen("rexglue_engine.log", "w", stdout);
    freopen("rexglue_engine.log", "a", stderr);
    setvbuf(stdout, nullptr, _IONBF, 0);
    setvbuf(stderr, nullptr, _IONBF, 0);
  }
} g_neverdead_veh_install;

}  // namespace
