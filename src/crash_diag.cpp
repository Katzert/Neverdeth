// Vectored exception handler (VEH) crash logger and recovery fallback.
//
// Logs fatal exceptions with DbgHelp stack traces to crash_diag.log (without a
// PDB, resolve module+offset addresses against Neverdeth.map). When
// ND_VEH_REPAIR != 0 (default), also recovers from uninitialized GPU ringbuffer
// divide-by-zero faults and uncommitted/protected guest arena access faults.

#include <windows.h>

#include <dbghelp.h>

#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <mutex>

#pragma comment(lib, "dbghelp.lib")

namespace {

std::mutex g_log_mutex;

void symline(FILE* f, HANDLE proc, DWORD64 addr, int idx) {
  char buf[sizeof(SYMBOL_INFO) + 512];
  auto* si = reinterpret_cast<SYMBOL_INFO*>(buf);
  si->SizeOfStruct = sizeof(SYMBOL_INFO);
  si->MaxNameLen = 511;
  IMAGEHLP_MODULE64 mod;
  mod.SizeOfStruct = sizeof(mod);
  const char* modname = SymGetModuleInfo64(proc, addr, &mod) ? mod.ModuleName : "?";
  DWORD64 disp = 0;
  if (SymFromAddr(proc, addr, &disp, si)) {
    fprintf(f, "  [%2d] %s!%s +0x%llX  (0x%llX)\n", idx, modname, si->Name,
            (unsigned long long)disp, (unsigned long long)addr);
  } else {
    fprintf(f, "  [%2d] %s!0x%llX\n", idx, modname, (unsigned long long)addr);
  }
  IMAGEHLP_LINE64 line;
  line.SizeOfStruct = sizeof(line);
  DWORD line_disp = 0;
  if (SymGetLineFromAddr64(proc, addr, &line_disp, &line)) {
    fprintf(f, "         %s:%lu\n", line.FileName, line.LineNumber);
  }
}

// ND_VEH_REPAIR=0 disables auto-repair so exceptions are only logged and passed through.
bool veh_repair_enabled() {
  static int enabled = -1;
  if (enabled < 0) {
    const char* v = std::getenv("ND_VEH_REPAIR");
    enabled = (v != nullptr && v[0] == '0') ? 0 : 1;
  }
  return enabled != 0;
}

// Logs the first 200 VEH activations and 1 in 500 thereafter to prevent log flooding.
void veh_log_loud(const char* kind, EXCEPTION_POINTERS* ep, unsigned long long addr) {
  static std::atomic<unsigned long> count{0};
  const unsigned long n = ++count;
  if (n > 200 && (n % 500) != 0) {
    return;
  }
  std::lock_guard<std::mutex> lock(g_log_mutex);
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

  // Auto-repair GPU worker divide-by-zero (0xC0000094) when ringbuffer size is 0.
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
      ep->ContextRecord->Rip += 19;  // Advance past both div ecx to jmp +9 (0xEB 0x09)
      static std::atomic<bool> s_div0_logged{false};
      if (!s_div0_logged.exchange(true)) {
        std::lock_guard<std::mutex> lock(g_log_mutex);
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

  // Auto-repair guest memory access faults (0xC0000005) in virtual/physical arenas [0x200000000, 0x400000000).
  if (er->ExceptionCode == 0xC0000005 && er->NumberParameters >= 2) {
    DWORD64 fault_addr = er->ExceptionInformation[1];
    if (fault_addr >= 0x0000000200000000ull && fault_addr < 0x0000000400000000ull) {
      veh_log_loud("av", ep, fault_addr);
      if (!veh_repair_enabled()) {
        return EXCEPTION_CONTINUE_SEARCH;
      }
      void* page_addr = reinterpret_cast<void*>(fault_addr & ~0xFFFull);
      static std::atomic<uint32_t> s_prot_fault_cnt{0};
      DWORD old_prot = 0;
      if (VirtualProtect(page_addr, 0x1000, PAGE_EXECUTE_READWRITE, &old_prot) ||
          VirtualProtect(page_addr, 0x1000, PAGE_READWRITE, &old_prot)) {
        if (old_prot != PAGE_READWRITE && old_prot != PAGE_EXECUTE_READWRITE) {
          const uint32_t cnt = ++s_prot_fault_cnt;
          if (cnt <= 20 || (cnt % 500 == 0)) {
            std::lock_guard<std::mutex> lock(g_log_mutex);
            FILE* f = fopen("crash_diag.log", "a");
            if (f) {
              fprintf(f, "[VEH-AUTO-REPAIR #%u] prot fault (%s) at 0x%llX rip=0x%llX (page %p) old_prot=0x%lX -> resumed\n",
                      cnt, er->ExceptionInformation[0] == 1 ? "write" : "read",
                      (unsigned long long)fault_addr, (unsigned long long)er->ExceptionAddress,
                      page_addr, (unsigned long)old_prot);
              fclose(f);
            }
          }
          return EXCEPTION_CONTINUE_EXECUTION;
        }
        // Pass unfixable protection faults to the debugger to avoid an infinite VEH loop.
        return EXCEPTION_CONTINUE_SEARCH;
      }

      // Fast chunk commit: align to 16MB boundary and commit 16MB, with 64KB fallback.
      constexpr uint64_t kChunkSize = 0x01000000ull;
      void* chunk_addr = reinterpret_cast<void*>(fault_addr & ~(kChunkSize - 1));
      void* res = VirtualAlloc(chunk_addr, kChunkSize, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
      if (!res) {
        res = VirtualAlloc(chunk_addr, kChunkSize, MEM_COMMIT, PAGE_READWRITE);
      }
      if (!res) {
        chunk_addr = reinterpret_cast<void*>(fault_addr & ~0xFFFFull);
        res = VirtualAlloc(chunk_addr, 0x10000, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
        if (!res) {
          res = VirtualAlloc(chunk_addr, 0x10000, MEM_COMMIT, PAGE_READWRITE);
        }
      }
      if (res) {
        static std::atomic<uint32_t> s_repair_cnt{0};
        const uint32_t cnt = ++s_repair_cnt;
        if (cnt <= 10 || (cnt % 500 == 0)) {
          std::lock_guard<std::mutex> lock(g_log_mutex);
          FILE* f = fopen("crash_diag.log", "a");
          if (f) {
            fprintf(f, "[VEH-AUTO-REPAIR #%u] commit fault (%s) at 0x%llX rip=0x%llX (chunk %p) -> committed zeroed & resumed\n",
                    cnt, er->ExceptionInformation[0] == 1 ? "write" : "read",
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

  std::lock_guard<std::mutex> lock(g_log_mutex);
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
  return EXCEPTION_CONTINUE_SEARCH;
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
