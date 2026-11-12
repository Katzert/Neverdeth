// Neverdeth - Host runtime memory patches, FPS/interval state, and startup input injection.

#include "nd_internal.h"

#include <cassert>
#include <cctype>
#include <cerrno>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <mutex>

#ifdef _WIN32
#include <intrin.h>
#endif

#include <rex/logging.h>

std::atomic<bool> g_in_3d_gameplay{false};
std::atomic<uint32_t> g_guest_device_va{0};

extern "C" bool NdCheckShouldPaintUI(void* presenter, bool force_paint);
extern "C" bool NdCheckShouldPaintAndPresent();

namespace {

constexpr size_t kPaintModeOffset = 0xA8;
std::atomic<bool> g_fps_mode_initialized{false};

#ifdef _WIN32
void NdInstallSetPaintModeHook() {
  static std::atomic<bool> s_done{false};
  if (s_done.exchange(true)) {
    return;
  }
  const char* env = std::getenv("ND_FORCE_GUEST_PRESENT");
  if (env != nullptr && env[0] == '0') {
    REXLOG_INFO("neverdead: [PAINT-MODE] disabled via ND_FORCE_GUEST_PRESENT=0");
    return;
  }
  HMODULE mod = GetModuleHandleA("rexruntime.dll");
  if (mod == nullptr) {
    REXLOG_ERROR("neverdead: [PAINT-MODE] rexruntime.dll is not loaded");
    s_done.store(false, std::memory_order_relaxed);
    return;
  }
  uint8_t* base = reinterpret_cast<uint8_t*>(mod);
  constexpr size_t kRva = 0xD6170;
  uint8_t* entry = base + kRva;
  // push rsi / push rdi / push rbx / sub rsp, 0x20 / cmp [rcx+0xA8], edx / jne
  static const uint8_t kExpected[15] = {0x56, 0x57, 0x53, 0x48, 0x83, 0xEC, 0x20, 0x39,
                                        0x91, 0xA8, 0x00, 0x00, 0x00, 0x75, 0x08};
  if ((entry[0] == 0xC7 && entry[1] == 0x41 && entry[2] == 0x18 && entry[3] == 0x00 &&
       entry[4] == 0x00) ||
      entry[0] == 0xE9) {
    REXLOG_INFO("neverdead: [PAINT-MODE] SetPaintModeFromUIThread already patched (rexruntime+0x{:X})",
                kRva);
    return;
  }
  if (std::memcmp(entry, kExpected, sizeof(kExpected)) != 0) {
    REXLOG_ERROR(
        "neverdead: [PAINT-MODE] unexpected prologue bytes at rexruntime+0x{:X}: "
        "{:02X} {:02X} {:02X} {:02X} {:02X}; skipping hook",
        kRva, entry[0], entry[1], entry[2], entry[3], entry[4]);
    return;
  }
  uint8_t* stub = NdAllocNear(entry, 0x1000);
  if (stub == nullptr) {
    REXLOG_ERROR("neverdead: [PAINT-MODE] failed to allocate trampoline near rexruntime");
    return;
  }

  size_t o = 0;
  auto put64 = [&](uint64_t v) {
    std::memcpy(stub + o, &v, sizeof(v));
    o += sizeof(v);
  };
  // Capture Presenter* (RCX) for diagnostics and pin non-zero modes to kGuestOutputThreadImmediately (2).
  // mov rax, &g_presenter_object ; mov [rax], rcx
  stub[o++] = 0x48;
  stub[o++] = 0xB8;
  put64(reinterpret_cast<uint64_t>(&g_presenter_object));
  stub[o++] = 0x48;
  stub[o++] = 0x89;
  stub[o++] = 0x08;
  // test edx, edx ; je +2 ; mov dl, 2 (preserve kNone = 0)
  stub[o++] = 0x85;
  stub[o++] = 0xD2;
  stub[o++] = 0x74;
  stub[o++] = 0x02;
  stub[o++] = 0xB2;
  stub[o++] = 0x02;
  // Original prologue
  stub[o++] = 0x56;
  stub[o++] = 0x57;
  stub[o++] = 0x53;
  stub[o++] = 0x48;
  stub[o++] = 0x83;
  stub[o++] = 0xEC;
  stub[o++] = 0x20;
  // mov rax, rexruntime+0xD6177 ; jmp rax
  stub[o++] = 0x48;
  stub[o++] = 0xB8;
  put64(reinterpret_cast<uint64_t>(base + 0xD6177));
  stub[o++] = 0xFF;
  stub[o++] = 0xE0;
  FlushInstructionCache(GetCurrentProcess(), stub, o);

  const intptr_t rel = reinterpret_cast<intptr_t>(stub) - reinterpret_cast<intptr_t>(entry + 5);
  const int32_t rel32 = static_cast<int32_t>(rel);
  DWORD old_protect = 0;
  if (VirtualProtect(entry, 5, PAGE_EXECUTE_READWRITE, &old_protect) == 0) {
    REXLOG_ERROR("neverdead: [PAINT-MODE] VirtualProtect failed (err={})", GetLastError());
    return;
  }
  entry[0] = 0xE9;
  std::memcpy(entry + 1, &rel32, sizeof(rel32));
  DWORD ignored = 0;
  VirtualProtect(entry, 5, old_protect, &ignored);
  FlushInstructionCache(GetCurrentProcess(), entry, 5);
  REXLOG_INFO("neverdead: [PAINT-MODE] SetPaintModeFromUIThread hooked at rexruntime+0x{:X}", kRva);
}

// Force paint_mode_ = 2 in the inlined GetDesiredPaintModeFromUIThread check
// inside Presenter::PaintFromUIThread so the guest thread presents every VdSwap.
void NdPatchInlinedPaintMode() {
  static std::atomic<bool> s_done{false};
  if (s_done.exchange(true)) {
    return;
  }
  const char* env = std::getenv("ND_FORCE_GUEST_PRESENT");
  if (env != nullptr && env[0] == '0') {
    REXLOG_INFO("neverdead: [PAINT-MODE] inline patch disabled via ND_FORCE_GUEST_PRESENT=0");
    return;
  }
  HMODULE mod = GetModuleHandleA("rexruntime.dll");
  if (mod == nullptr) {
    s_done.store(false, std::memory_order_relaxed);
    return;
  }
  uint8_t* base = reinterpret_cast<uint8_t*>(mod);
  constexpr size_t kMovRva = 0xD6C8D;  // 41 BF 01 00 00 00 -> 41 BF 02 00 00 00
  constexpr size_t kJneRva = 0xD6C9A;  // 75 18 (jne)       -> EB 18 (jmp)
  uint8_t* mov_site = base + kMovRva;
  uint8_t* jne_site = base + kJneRva;
  static const uint8_t kMovExpected[6] = {0x41, 0xBF, 0x01, 0x00, 0x00, 0x00};
  if (mov_site[2] == 0x02 || mov_site[0] == 0xA8 || mov_site[0] == 0xE9) {
    REXLOG_INFO("neverdead: [PAINT-MODE] inline paint mode already patched (rexruntime+0x{:X})",
                kMovRva);
    return;
  }
  if (std::memcmp(mov_site, kMovExpected, sizeof(kMovExpected)) != 0 || jne_site[0] != 0x75 ||
      jne_site[1] != 0x18) {
    REXLOG_ERROR(
        "neverdead: [PAINT-MODE] unexpected bytes at inline paint mode site "
        "(rexruntime+0x{:X}/0x{:X}); skipping",
        kMovRva, kJneRva);
    return;
  }
  DWORD old_protect = 0;
  if (VirtualProtect(mov_site, 6, PAGE_EXECUTE_READWRITE, &old_protect) == 0) {
    REXLOG_ERROR("neverdead: [PAINT-MODE] VirtualProtect failed (err={})", GetLastError());
    return;
  }
  mov_site[2] = 0x02;
  jne_site[0] = 0xEB;
  DWORD ignored = 0;
  VirtualProtect(mov_site, 6, old_protect, &ignored);
  FlushInstructionCache(GetCurrentProcess(), mov_site, 6);
  REXLOG_INFO(
      "neverdead: [PAINT-MODE] inline GetDesiredPaintMode pinned to 2 "
      "(rexruntime+0x{:X}, rexruntime+0x{:X})",
      kMovRva, kJneRva);
}

void NdInstallPaintFromUIThreadHook() {
  static std::atomic<bool> s_done{false};
  if (s_done.exchange(true)) {
    return;
  }
  HMODULE mod = GetModuleHandleA("rexruntime.dll");
  if (mod == nullptr) {
    s_done.store(false, std::memory_order_relaxed);
    return;
  }
  uint8_t* base = reinterpret_cast<uint8_t*>(mod);
  static const bool s_gate_off = []() {
    const char* e = std::getenv("ND_NO_PAINT_GATE");
    return e != nullptr && e[0] == '1';
  }();
  if (s_gate_off) {
    REXLOG_INFO("neverdead: [PAINT-GATE] UI paint gate disabled via ND_NO_PAINT_GATE=1");
    return;
  }
  constexpr size_t kRva = 0xD6AC0;
  uint8_t* entry = base + kRva;
  // push r15 / push r14 / push rsi
  static const uint8_t kExpected[5] = {0x41, 0x57, 0x41, 0x56, 0x56};
  if (entry[0] == 0xE9 || (entry[0] == 0xC7 && entry[1] == 0x46 && entry[2] == 0x30)) {
    REXLOG_INFO("neverdead: [PAINT-GATE] PaintFromUIThread gate already active (rexruntime+0x{:X})",
                kRva);
    return;
  }
  if (std::memcmp(entry, kExpected, sizeof(kExpected)) != 0) {
    REXLOG_ERROR("neverdead: [PAINT-GATE] unexpected bytes at PaintFromUIThread+0x{:X}; skipping",
                 kRva);
    return;
  }
  uint8_t* stub = NdAllocNear(entry, 0x1000);
  if (stub == nullptr) {
    REXLOG_ERROR("neverdead: [PAINT-GATE] failed to allocate PaintFromUIThread trampoline");
    return;
  }
  size_t o = 0;
  auto put64 = [&](uint64_t v) {
    std::memcpy(stub + o, &v, sizeof(v));
    o += sizeof(v);
  };
  stub[o++] = 0x48; stub[o++] = 0x83; stub[o++] = 0xEC; stub[o++] = 0x28;  // sub rsp, 0x28
  stub[o++] = 0x48; stub[o++] = 0x89; stub[o++] = 0x4C; stub[o++] = 0x24; stub[o++] = 0x20;  // mov [rsp+0x20], rcx
  stub[o++] = 0x48; stub[o++] = 0x89; stub[o++] = 0x54; stub[o++] = 0x24; stub[o++] = 0x18;  // mov [rsp+0x18], rdx
  stub[o++] = 0x48; stub[o++] = 0xB8;
  put64(reinterpret_cast<uint64_t>(&NdCheckShouldPaintUI));
  stub[o++] = 0xFF; stub[o++] = 0xD0;  // call rax
  stub[o++] = 0x48; stub[o++] = 0x8B; stub[o++] = 0x54; stub[o++] = 0x24; stub[o++] = 0x18;  // mov rdx, [rsp+0x18]
  stub[o++] = 0x48; stub[o++] = 0x8B; stub[o++] = 0x4C; stub[o++] = 0x24; stub[o++] = 0x20;  // mov rcx, [rsp+0x20]
  stub[o++] = 0x48; stub[o++] = 0x83; stub[o++] = 0xC4; stub[o++] = 0x28;  // add rsp, 0x28
  stub[o++] = 0x84; stub[o++] = 0xC0;  // test al, al
  stub[o++] = 0x75; stub[o++] = 0x01;  // jnz +1
  stub[o++] = 0xC3;                    // ret
  stub[o++] = 0x41; stub[o++] = 0x57;  // push r15
  stub[o++] = 0x41; stub[o++] = 0x56;  // push r14
  stub[o++] = 0x56;                    // push rsi
  stub[o++] = 0x48; stub[o++] = 0xB8;
  put64(reinterpret_cast<uint64_t>(entry + 5));
  stub[o++] = 0xFF; stub[o++] = 0xE0;  // jmp rax
  FlushInstructionCache(GetCurrentProcess(), stub, o);

  const intptr_t rel = reinterpret_cast<intptr_t>(stub) - reinterpret_cast<intptr_t>(entry + 5);
  const int32_t rel32 = static_cast<int32_t>(rel);
  DWORD old_protect = 0;
  if (VirtualProtect(entry, 5, PAGE_EXECUTE_READWRITE, &old_protect) == 0) {
    return;
  }
  entry[0] = 0xE9;
  std::memcpy(entry + 1, &rel32, sizeof(rel32));
  DWORD ignored = 0;
  VirtualProtect(entry, 5, old_protect, &ignored);
  FlushInstructionCache(GetCurrentProcess(), entry, 5);
  REXLOG_INFO("neverdead: [PAINT-GATE] PaintFromUIThread gate installed (rexruntime+0x{:X})", kRva);
}

void NdInstallPaintAndPresentGate() {
  static std::atomic<bool> s_done{false};
  if (s_done.exchange(true)) {
    return;
  }
  HMODULE mod = GetModuleHandleA("rexruntime.dll");
  if (mod == nullptr) {
    s_done.store(false, std::memory_order_relaxed);
    return;
  }
  uint8_t* base = reinterpret_cast<uint8_t*>(mod);
  static const bool s_gate_off = []() {
    const char* e = std::getenv("ND_NO_PAINT_GATE");
    return e != nullptr && e[0] == '1';
  }();
  if (s_gate_off) {
    REXLOG_INFO("neverdead: [PAINT-GATE] PaintAndPresent gate disabled via ND_NO_PAINT_GATE=1");
    return;
  }
  constexpr size_t kRva = 0xFFC10;
  uint8_t* entry = base + kRva;
  // Stolen 12 bytes: push rbp / push r15..r12 / push rsi / push rdi / push rbx
  static const uint8_t kExpected[12] = {
      0x55, 0x41, 0x57, 0x41, 0x56, 0x41, 0x55, 0x41, 0x54, 0x56, 0x57, 0x53};
  if (entry[0] == 0xE9 || (entry[0] == 0x48 && entry[1] == 0x89 && entry[2] == 0xD0)) {
    REXLOG_INFO("neverdead: [PAINT-GATE] PaintAndPresent gate already active (rexruntime+0x{:X})",
                kRva);
    return;
  }
  if (std::memcmp(entry, kExpected, sizeof(kExpected)) != 0) {
    REXLOG_ERROR("neverdead: [PAINT-GATE] unexpected bytes at PaintAndPresent+0x{:X}; skipping",
                 kRva);
    return;
  }
  uint8_t* stub = NdAllocNear(entry, 0x1000);
  if (stub == nullptr) {
    REXLOG_ERROR("neverdead: [PAINT-GATE] failed to allocate PaintAndPresent trampoline");
    return;
  }
  size_t o = 0;
  auto put64 = [&](uint64_t v) {
    std::memcpy(stub + o, &v, sizeof(v));
    o += sizeof(v);
  };
  stub[o++] = 0x48; stub[o++] = 0x83; stub[o++] = 0xEC; stub[o++] = 0x28;
  stub[o++] = 0x48; stub[o++] = 0x89; stub[o++] = 0x4C; stub[o++] = 0x24; stub[o++] = 0x20;
  stub[o++] = 0x48; stub[o++] = 0x89; stub[o++] = 0x54; stub[o++] = 0x24; stub[o++] = 0x18;
  stub[o++] = 0x48; stub[o++] = 0xB8;
  put64(reinterpret_cast<uint64_t>(&NdCheckShouldPaintAndPresent));
  stub[o++] = 0xFF; stub[o++] = 0xD0;
  stub[o++] = 0x48; stub[o++] = 0x8B; stub[o++] = 0x54; stub[o++] = 0x24; stub[o++] = 0x18;
  stub[o++] = 0x48; stub[o++] = 0x8B; stub[o++] = 0x4C; stub[o++] = 0x24; stub[o++] = 0x20;
  stub[o++] = 0x48; stub[o++] = 0x83; stub[o++] = 0xC4; stub[o++] = 0x28;
  stub[o++] = 0x84; stub[o++] = 0xC0;  // test al, al
  stub[o++] = 0x75; stub[o++] = 0x03;  // jnz +3
  stub[o++] = 0x31; stub[o++] = 0xC0;  // xor eax, eax (PaintResult::kNotPresented = 0)
  stub[o++] = 0xC3;                    // ret
  for (size_t i = 0; i < sizeof(kExpected); ++i) {
    stub[o++] = kExpected[i];
  }
  stub[o++] = 0x48; stub[o++] = 0xB8;
  put64(reinterpret_cast<uint64_t>(entry + 12));
  stub[o++] = 0xFF; stub[o++] = 0xE0;
  FlushInstructionCache(GetCurrentProcess(), stub, o);

  const intptr_t rel = reinterpret_cast<intptr_t>(stub) - reinterpret_cast<intptr_t>(entry + 5);
  const int32_t rel32 = static_cast<int32_t>(rel);
  DWORD old_protect = 0;
  if (VirtualProtect(entry, 12, PAGE_EXECUTE_READWRITE, &old_protect) == 0) {
    return;
  }
  entry[0] = 0xE9;
  std::memcpy(entry + 1, &rel32, sizeof(rel32));
  for (size_t i = 5; i < 12; ++i) {
    entry[i] = 0x90;
  }
  DWORD ignored = 0;
  VirtualProtect(entry, 12, old_protect, &ignored);
  FlushInstructionCache(GetCurrentProcess(), entry, 12);
  REXLOG_INFO("neverdead: [PAINT-GATE] PaintAndPresent gate installed (rexruntime+0x{:X})", kRva);
}

// Restore paint_mode_ = 2 on return from PaintFromUIThread so redundant UI paints stay gated.
void NdInstallPaintReturnHook() {
  static std::atomic<bool> s_done{false};
  if (s_done.exchange(true)) {
    return;
  }
  HMODULE mod = GetModuleHandleA("rexruntime.dll");
  if (mod == nullptr) {
    s_done.store(false, std::memory_order_relaxed);
    return;
  }
  uint8_t* base = reinterpret_cast<uint8_t*>(mod);
  constexpr size_t kRva = 0xDE951;
  uint8_t* site = base + kRva;
  static const uint8_t kExpected[16] = {0x48, 0x89, 0xF1, 0xC6, 0x81, 0xC0, 0x00, 0x00,
                                        0x00, 0x00, 0x48, 0x83, 0xC4, 0x20, 0x5E, 0xC3};
  if (site[0] == 0xE9 || (site[0] == 0xBD && site[1] == 0xA8)) {
    REXLOG_INFO("neverdead: [PAINT-MODE] Paint epilogue already patched (rexruntime+0x{:X})",
                kRva);
    return;
  }
  if (std::memcmp(site, kExpected, sizeof(kExpected)) != 0) {
    REXLOG_ERROR("neverdead: [PAINT-MODE] unexpected epilogue bytes (rexruntime+0x{:X}); skipping",
                 kRva);
    return;
  }
  uint8_t* stub = NdAllocNear(site, 0x1000);
  if (stub == nullptr) {
    REXLOG_ERROR("neverdead: [PAINT-MODE] failed to allocate Paint epilogue trampoline");
    return;
  }
  // mov rax, [rsi+0xB0] ; mov dword [rax+0xA8], 2 ; mov rcx, rsi
  // mov byte [rcx+0xC0], 0 ; add rsp, 0x20 ; pop rsi ; ret
  static const uint8_t kStub[] = {
      0x48, 0x8B, 0x86, 0xB0, 0x00, 0x00, 0x00,
      0xC7, 0x80, 0xA8, 0x00, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00,
      0x48, 0x89, 0xF1,
      0xC6, 0x81, 0xC0, 0x00, 0x00, 0x00, 0x00,
      0x48, 0x83, 0xC4, 0x20,
      0x5E,
      0xC3};
  std::memcpy(stub, kStub, sizeof(kStub));
  FlushInstructionCache(GetCurrentProcess(), stub, sizeof(kStub));

  const intptr_t rel = reinterpret_cast<intptr_t>(stub) - reinterpret_cast<intptr_t>(site + 5);
  const int32_t rel32 = static_cast<int32_t>(rel);
  DWORD old_protect = 0;
  if (VirtualProtect(site, sizeof(kExpected), PAGE_EXECUTE_READWRITE, &old_protect) == 0) {
    return;
  }
  site[0] = 0xE9;
  std::memcpy(site + 1, &rel32, sizeof(rel32));
  std::memset(site + 5, 0x90, sizeof(kExpected) - 5);
  DWORD ignored = 0;
  VirtualProtect(site, sizeof(kExpected), old_protect, &ignored);
  FlushInstructionCache(GetCurrentProcess(), site, sizeof(kExpected));
  REXLOG_INFO("neverdead: [PAINT-MODE] Paint epilogue hooked (rexruntime+0x{:X})", kRva);
}
#endif  // _WIN32

}  // namespace

extern "C" bool NdCheckShouldPaintUI(void* presenter, bool force_paint) {
  g_presenter_object.store(presenter, std::memory_order_release);
  NdRestorePaintMode();
  const uint64_t swaps = g_vd_swap_calls.load(std::memory_order_acquire);
  if (swaps > 0) {
    return false;
  }
  return force_paint;
}

extern "C" bool NdCheckShouldPaintAndPresent() {
  const uint64_t swaps = g_vd_swap_calls.load(std::memory_order_acquire);
  if (swaps == 0) {
    return true;
  }
  uint64_t last = g_last_presented_swap.load(std::memory_order_acquire);
  while (swaps > last) {
    if (g_last_presented_swap.compare_exchange_weak(
            last, swaps, std::memory_order_acq_rel, std::memory_order_acquire)) {
      return true;
    }
  }
  return false;
}

bool NdIsDiagEnabled() {
  static const bool s_diag = []() {
    const char* e = std::getenv("ND_DIAG");
    return e != nullptr && e[0] == '1' && e[1] == '\0';
  }();
  return s_diag;
}

#ifdef _WIN32
uint8_t* NdAllocNear(void* target, size_t size) {
  for (intptr_t delta = 0x10000; delta <= 0x70000000; delta += 0x10000) {
    auto* p = static_cast<uint8_t*>(VirtualAlloc(reinterpret_cast<uint8_t*>(target) + delta,
                                                 size, MEM_COMMIT | MEM_RESERVE,
                                                 PAGE_EXECUTE_READWRITE));
    if (p != nullptr) {
      return p;
    }
    p = static_cast<uint8_t*>(VirtualAlloc(reinterpret_cast<uint8_t*>(target) - delta, size,
                                           MEM_COMMIT | MEM_RESERVE,
                                           PAGE_EXECUTE_READWRITE));
    if (p != nullptr) {
      return p;
    }
  }
  return nullptr;
}
#endif

int NdReadPaintMode() {
  void* presenter = g_presenter_object.load(std::memory_order_acquire);
  if (presenter == nullptr) {
    return -1;
  }
  return *reinterpret_cast<volatile int32_t*>(static_cast<uint8_t*>(presenter) +
                                              kPaintModeOffset);
}

bool NdRestorePaintMode() {
  void* presenter = g_presenter_object.load(std::memory_order_acquire);
  if (presenter == nullptr) {
    return false;
  }
  auto* mode = reinterpret_cast<volatile int32_t*>(static_cast<uint8_t*>(presenter) +
                                                   kPaintModeOffset);
  const int32_t current = *mode;
  if (current == 2) {
    return false;
  }
  *mode = 2;
  static std::atomic<uint32_t> s_repaired{0};
  const uint32_t total = s_repaired.fetch_add(1, std::memory_order_relaxed) + 1;
  if (NdIsDiagEnabled() && (total == 1 || (total % 500) == 0)) {
    REXLOG_INFO("neverdead: [PAINT-MODE] restored paint_mode_ to 2 (was {}) total={}", current,
                total);
  }
  return true;
}

void NdSilenceXctdLog() {
#ifdef _WIN32
  static std::atomic<bool> s_done{false};
  if (s_done.load(std::memory_order_relaxed)) {
    return;
  }
  HMODULE mod = GetModuleHandleA("rexruntime.dll");
  if (mod == nullptr) {
    return;
  }
  uint8_t* base = reinterpret_cast<uint8_t*>(mod);
  constexpr size_t kRva = 0x3E9143;
  uint8_t* target = base + kRva;
  static const uint8_t kExpected[6] = {0x0F, 0x8F, 0x04, 0x02, 0x00, 0x00};
  static const uint8_t kPatched[6] = {0xE9, 0x05, 0x02, 0x00, 0x00, 0x90};

  DWORD old_protect = 0;
  if (VirtualProtect(target, sizeof(kPatched), PAGE_EXECUTE_READWRITE, &old_protect) != 0) {
    if (std::memcmp(target, kExpected, sizeof(kExpected)) == 0) {
      std::memcpy(target, kPatched, sizeof(kPatched));
      FlushInstructionCache(GetCurrentProcess(), target, sizeof(kPatched));
      s_done.store(true, std::memory_order_relaxed);
      REXLOG_INFO(
          "neverdead: [XCTD-SILENCE] XFileXctdCompressionInformation error spam silenced "
          "(rexruntime+0x{:X})",
          kRva);
    } else if (std::memcmp(target, kPatched, sizeof(kPatched)) == 0) {
      s_done.store(true, std::memory_order_relaxed);
    } else {
      REXLOG_WARN("neverdead: [XCTD-SILENCE] unexpected bytes at rexruntime+0x{:X}; skipping",
                  kRva);
    }
    VirtualProtect(target, sizeof(kPatched), old_protect, &old_protect);
  }
#endif
}

void NdPatchAsyncPipelineCache() {
#ifdef _WIN32
  static std::atomic<bool> s_done{false};
  if (s_done.load(std::memory_order_acquire)) {
    return;
  }
  static std::mutex s_patch_mtx;
  std::lock_guard<std::mutex> lk(s_patch_mtx);
  if (s_done.load(std::memory_order_relaxed)) {
    return;
  }

  HMODULE mod = GetModuleHandleA("rexgpu-xenos.dll");
  if (mod == nullptr) {
    return;
  }
  uint8_t* base = reinterpret_cast<uint8_t*>(mod);

  auto parse_timeout_ms = []() -> uint64_t {
    const char* raw = std::getenv("ND_PSO_TIMEOUT_MS");
    if (raw == nullptr) return 0;
    while (*raw != '\0' && std::isspace(static_cast<unsigned char>(*raw))) ++raw;
    if (*raw == '\0' || *raw == '-') {
      REXLOG_WARN("neverdead: [ASYNC-PSO] Invalid ND_PSO_TIMEOUT_MS='{}'; using 0 ms", raw);
      return 0;
    }
    errno = 0;
    char* end = nullptr;
    const unsigned long long parsed = std::strtoull(raw, &end, 10);
    while (end != nullptr && *end != '\0' && std::isspace(static_cast<unsigned char>(*end))) ++end;
    if (errno == ERANGE || end == raw || (end != nullptr && *end != '\0') ||
        parsed >= 0xFFFFFFFFULL) {
      REXLOG_WARN(
          "neverdead: [ASYNC-PSO] Out-of-range/invalid ND_PSO_TIMEOUT_MS='{}'; using 0 ms", raw);
      return 0;
    }
    return static_cast<uint64_t>(parsed);
  };

  auto parse_bool_env = [](const char* name, bool default_val) -> bool {
    const char* raw = std::getenv(name);
    if (raw == nullptr || *raw == '\0') return default_val;
    if (_stricmp(raw, "0") == 0 || _stricmp(raw, "false") == 0 || _stricmp(raw, "no") == 0 ||
        _stricmp(raw, "off") == 0) {
      return false;
    }
    if (_stricmp(raw, "1") == 0 || _stricmp(raw, "true") == 0 || _stricmp(raw, "yes") == 0 ||
        _stricmp(raw, "on") == 0) {
      return true;
    }
    return default_val;
  };

  bool all_ok = true;

  // Patch PipelineCache::EndSubmission's blocking wait timeout (0xE8AFA) and bypass its
  // synchronous CreateQueuedPipelinesOnProcessorThread() drain (0xE8A88 -> 0xE8840) so background
  // D3D12 workers compile queued PSOs without stalling VdSwap. Leave InitializeShaderStorage's
  // wait intact so disk-backed shader/PSO loads on warm runs still complete before swap.
  const uint64_t timeout_ms = parse_timeout_ms();
  uint8_t kPatched[10] = {0x49, 0xB8, 0, 0, 0, 0, 0, 0, 0, 0};
  std::memcpy(kPatched + 2, &timeout_ms, sizeof(timeout_ms));

  constexpr size_t kWaitSiteRva = 0xE8AFA;
  constexpr uint8_t kWaitExpectedImm[8] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x7F};
  constexpr uint8_t kWaitExpectedTail[10] = {0x31, 0xD2, 0x48, 0x83, 0xC4, 0x30, 0x5F, 0x5E, 0x5D, 0xE9};
  uint8_t* wait_site = base + kWaitSiteRva;
  const bool wait_tail_ok = std::memcmp(wait_site + 10, kWaitExpectedTail, sizeof(kWaitExpectedTail)) == 0;
  if (wait_site[0] == 0x49 && wait_site[1] == 0xB8 && wait_tail_ok) {
    if (std::memcmp(wait_site, kPatched, sizeof(kPatched)) == 0) {
      // Already patched.
    } else if (std::memcmp(wait_site + 2, kWaitExpectedImm, sizeof(kWaitExpectedImm)) == 0) {
      DWORD old_prot = 0;
      if (VirtualProtect(wait_site, sizeof(kPatched), PAGE_EXECUTE_READWRITE, &old_prot) != 0) {
        std::memcpy(wait_site, kPatched, sizeof(kPatched));
        DWORD ign = 0;
        if (VirtualProtect(wait_site, sizeof(kPatched), old_prot, &ign) == 0) {
          REXLOG_ERROR(
              "neverdead: [ASYNC-PSO] VirtualProtect restore failed at rexgpu-xenos+0x{:X} (err={})",
              kWaitSiteRva, GetLastError());
          all_ok = false;
        }
        FlushInstructionCache(GetCurrentProcess(), wait_site, sizeof(kPatched));
        REXLOG_INFO(
            "neverdead: [ASYNC-PSO] PipelineCache wait timeout set to {} ms "
            "(rexgpu-xenos+0x{:X})",
            timeout_ms, kWaitSiteRva);
      } else {
        REXLOG_ERROR(
            "neverdead: [ASYNC-PSO] VirtualProtect failed at rexgpu-xenos+0x{:X} (err={})",
            kWaitSiteRva, GetLastError());
        all_ok = false;
      }
    } else {
      static bool s_warned_imm = false;
      if (!s_warned_imm) {
        s_warned_imm = true;
        REXLOG_WARN("neverdead: [ASYNC-PSO] Unexpected immediate at rexgpu-xenos+0x{:X}; skipped",
                    kWaitSiteRva);
      }
    }
  } else {
    static bool s_warned_sig = false;
    if (!s_warned_sig) {
      s_warned_sig = true;
      REXLOG_WARN("neverdead: [ASYNC-PSO] Wait signature mismatch at rexgpu-xenos+0x{:X}; skipped",
                  kWaitSiteRva);
    }
  }

  if (parse_bool_env("ND_ASYNC_NO_SWAP_DRAIN", true)) {
    constexpr size_t kMovPrefixRva = 0xE8A85;
    constexpr size_t kEndSubmissionDrainCallRva = 0xE8A88;
    constexpr size_t kCreateQueuedPipelinesRva = 0xE8840;
    constexpr uint8_t kMovPrefix[3] = {0x48, 0x89, 0xF1};
    constexpr uint8_t kLeaSuffix[7] = {0x48, 0x8D, 0xBE, 0xC8, 0x03, 0x00, 0x00};
    constexpr uint8_t kNops[5] = {0x90, 0x90, 0x90, 0x90, 0x90};

    uint8_t* mov_site = base + kMovPrefixRva;
    uint8_t* call_site = base + kEndSubmissionDrainCallRva;
    const bool context_ok =
        std::memcmp(mov_site, kMovPrefix, sizeof(kMovPrefix)) == 0 &&
        std::memcmp(call_site + 5, kLeaSuffix, sizeof(kLeaSuffix)) == 0;
    if (context_ok && std::memcmp(call_site, kNops, sizeof(kNops)) != 0) {
      bool target_ok = false;
      if (call_site[0] == 0xE8) {
        int32_t rel = 0;
        std::memcpy(&rel, call_site + 1, sizeof(rel));
        target_ok = (call_site + 5 + rel == base + kCreateQueuedPipelinesRva);
      }
      if (target_ok) {
        DWORD old_prot = 0;
        if (VirtualProtect(call_site, sizeof(uint64_t), PAGE_EXECUTE_READWRITE, &old_prot) != 0) {
          bool patched = false;
          assert((reinterpret_cast<uintptr_t>(call_site) % 8) == 0);
          if ((reinterpret_cast<uintptr_t>(call_site) % 8) == 0) {
            uint64_t expected = 0;
            std::memcpy(&expected, call_site, sizeof(expected));
            uint64_t desired = expected;
            uint8_t* desired_bytes = reinterpret_cast<uint8_t*>(&desired);
            for (size_t i = 0; i < sizeof(kNops); ++i) {
              desired_bytes[i] = kNops[i];
            }
            const __int64 prev = _InterlockedCompareExchange64(
                reinterpret_cast<volatile __int64*>(call_site),
                static_cast<__int64>(desired),
                static_cast<__int64>(expected));
            if (prev == static_cast<__int64>(expected)) {
              patched = true;
            } else {
              REXLOG_ERROR(
                  "neverdead: [ASYNC-PSO] Atomic CAS failed at rexgpu-xenos+0x{:X} "
                  "(expected=0x{:016X}, actual=0x{:016X})",
                  kEndSubmissionDrainCallRva, expected, static_cast<uint64_t>(prev));
              all_ok = false;
            }
          } else {
            std::memcpy(call_site, kNops, sizeof(kNops));
            patched = true;
          }

          DWORD ign = 0;
          if (VirtualProtect(call_site, sizeof(uint64_t), old_prot, &ign) == 0) {
            REXLOG_ERROR(
                "neverdead: [ASYNC-PSO] VirtualProtect restore failed at rexgpu-xenos+0x{:X} "
                "(err={})",
                kEndSubmissionDrainCallRva, GetLastError());
            all_ok = false;
          }
          if (patched) {
            FlushInstructionCache(GetCurrentProcess(), call_site, sizeof(uint64_t));
            REXLOG_INFO(
                "neverdead: [ASYNC-PSO] Bypassed synchronous EndSubmission queue drain "
                "(rexgpu-xenos+0x{:X})",
                kEndSubmissionDrainCallRva);
          }
        } else {
          REXLOG_ERROR(
              "neverdead: [ASYNC-PSO] VirtualProtect failed at rexgpu-xenos+0x{:X} (err={})",
              kEndSubmissionDrainCallRva, GetLastError());
          all_ok = false;
        }
      } else {
        static bool s_warned_target = false;
        if (!s_warned_target) {
          s_warned_target = true;
          REXLOG_WARN(
              "neverdead: [ASYNC-PSO] Drain call target mismatch at rexgpu-xenos+0x{:X}; skipped",
              kEndSubmissionDrainCallRva);
        }
      }
    } else if (!context_ok) {
      static bool s_warned_context = false;
      if (!s_warned_context) {
        s_warned_context = true;
        REXLOG_WARN(
            "neverdead: [ASYNC-PSO] Drain signature mismatch at rexgpu-xenos+0x{:X}; skipped",
            kEndSubmissionDrainCallRva);
      }
    }
  }

  if (all_ok) {
    s_done.store(true, std::memory_order_release);
  }
#endif
}

void NdSilenceSpamLogs() {
#ifdef _WIN32
  static std::atomic<bool> s_done{false};
  if (s_done.load(std::memory_order_relaxed)) {
    return;
  }

  // Silence high-volume PM4_DRAW_INDX and vertex-fetch warning paths in rexgpu-xenos.dll.
  HMODULE gpu_mod = GetModuleHandleA("rexgpu-xenos.dll");
  if (gpu_mod) {
    uint8_t* base = reinterpret_cast<uint8_t*>(gpu_mod);

    uint8_t* p1 = base + 0x15881;
    DWORD old1 = 0;
    if (VirtualProtect(p1, 6, PAGE_EXECUTE_READWRITE, &old1)) {
      p1[0] = 0xE9; p1[1] = 0xBE; p1[2] = 0x00; p1[3] = 0x00; p1[4] = 0x00; p1[5] = 0x90;
      DWORD ign = 0;
      VirtualProtect(p1, 6, old1, &ign);
      FlushInstructionCache(GetCurrentProcess(), p1, 6);
    }

    uint8_t* p2 = base + 0xA68F1;
    DWORD old2 = 0;
    if (VirtualProtect(p2, 6, PAGE_EXECUTE_READWRITE, &old2)) {
      p2[0] = 0xE9; p2[1] = 0x8A; p2[2] = 0x04; p2[3] = 0x00; p2[4] = 0x00; p2[5] = 0x90;
      DWORD ign = 0;
      VirtualProtect(p2, 6, old2, &ign);
      FlushInstructionCache(GetCurrentProcess(), p2, 6);
    }

    uint8_t* p3 = base + 0xA6976;
    DWORD old3 = 0;
    if (VirtualProtect(p3, 6, PAGE_EXECUTE_READWRITE, &old3)) {
      p3[0] = 0xE9; p3[1] = 0x05; p3[2] = 0x04; p3[3] = 0x00; p3[4] = 0x00; p3[5] = 0x90;
      DWORD ign = 0;
      VirtualProtect(p3, 6, old3, &ign);
      FlushInstructionCache(GetCurrentProcess(), p3, 6);
    }
    REXLOG_INFO("neverdead: [LOG-FILTER] silenced PM4_DRAW_INDX and vertex-fetch log spam");
  }

  // Silence UIPaint trace spam in rexruntime.dll.
  HMODULE rt_mod = GetModuleHandleA("rexruntime.dll");
  if (rt_mod) {
    uint8_t* base = reinterpret_cast<uint8_t*>(rt_mod);
    uint8_t* p4 = base + 0xE4150;
    DWORD old4 = 0;
    if (VirtualProtect(p4, 2, PAGE_EXECUTE_READWRITE, &old4)) {
      p4[0] = 0xEB; p4[1] = 0x7F;
      DWORD ign = 0;
      VirtualProtect(p4, 2, old4, &ign);
      FlushInstructionCache(GetCurrentProcess(), p4, 2);
      REXLOG_INFO("neverdead: [LOG-FILTER] silenced UIPaint log spam");
    }
  }

  if (gpu_mod && rt_mod) {
    s_done.store(true, std::memory_order_relaxed);
  }
#endif
}

void NdSyncVBlankRate() {
  // Intentional strict no-op: the GPU plugin's dedicated VBlank worker thread
  // drives guest VBlank interrupts; modifying its wait state races with SwapChain
  // initialization.
}

void NdEarlyInit() {
  static const bool s_qa_logged = []() {
    const char* v = std::getenv("ND_PSO_DISABLE");
    REXLOG_INFO("neverdead: [PSO-DIAG] ND_PSO_DISABLE='{}'", v ? v : "(none)");
    return true;
  }();
  (void)s_qa_logged;

  NdLoadSettingsFromIni();
  NdEnsureTimerResolution();

#ifdef _WIN32
  NdPatchInlinedPaintMode();
  NdInstallSetPaintModeHook();
  NdInstallPaintFromUIThreadHook();
  NdInstallPaintAndPresentGate();
  NdInstallPaintReturnHook();
#endif
  NdInstallPresentGate();
  NdSilenceXctdLog();
  NdSilenceSpamLogs();
  NdPatchAsyncPipelineCache();
  NdInstallD3D12DeviceHook();
}

void NdSyncFpsClampMemory(float target) {
  (void)target;
  // Keep the guest clamp variable at 0.0f (unlocked) so the engine does not stall
  // waiting on VBlanks; host frame pacing enforces the target limit instead.
  if (nd_translate_va(0x82B31FBC) != nullptr) {
    NdWriteGuestBe32(0x82B31FBC, NdFloatToBeBits(0.0f));
  }
}

void NdNotifyAsuraFpsIntervalChanged() {
  uint32_t dev = g_guest_device_va.load(std::memory_order_relaxed);
  if (dev == 0) {
    return;
  }

  const float target = g_fps_target.load(std::memory_order_relaxed);
  const bool in_3d = g_in_3d_gameplay.load(std::memory_order_relaxed);
  const bool unlocked = g_unlock_framerate.load(std::memory_order_relaxed) && in_3d;

  uint32_t interval = 2;  // 30 FPS
  if (unlocked) {
    if (target <= 0.0f) {
      interval = 0;  // Unlimited
    } else if (target > 35.0f) {
      interval = 1;  // 60 FPS
    }
  }

  // Update the device interval and clear the cached interval state so the
  // guest render thread re-emits the PM4 presentation interval packet.
  NdWriteGuestBe32(dev + 14000, interval);
  NdWriteGuestBe32(dev + 24584, 0);
  NdSyncVBlankRate();

  static uint32_t s_last_logged_interval = 999;
  static bool s_last_logged_3d = false;
  static float s_last_logged_target = -999.0f;
  static uint64_t s_sync_call_count = 0;

  const bool changed = (interval != s_last_logged_interval || in_3d != s_last_logged_3d ||
                        target != s_last_logged_target);
  if (changed || (++s_sync_call_count % 300 == 1)) {
    s_last_logged_interval = interval;
    s_last_logged_3d = in_3d;
    s_last_logged_target = target;
    REXLOG_INFO(
        "neverdead: [FPS-SYNC] Device=0x{:08X} -> interval={} (target={:.1f}, in_3d={}, unlock={})",
        dev, interval, target, in_3d ? 1 : 0, unlocked);
  }
}

void NdInitFpsMode() {
  if (g_fps_mode_initialized.exchange(true)) {
    return;
  }
  NdLoadSettingsFromIni();
  float target = g_fps_target.load(std::memory_order_relaxed);
  const bool unlock = g_unlock_framerate.load(std::memory_order_relaxed);
  if (unlock && target <= 0.0f) {
    target = 0.0f;
  }
  const char* env_unlock = std::getenv("ND_UNLOCK_FPS");
  if (env_unlock != nullptr && env_unlock[0] == '1') {
    g_unlock_framerate.store(true, std::memory_order_relaxed);
    target = 0.0f;
  }
  const char* env = std::getenv("ND_FPS_LIMIT");
  if (env != nullptr) {
    const float v = static_cast<float>(std::atof(env));
    if (v >= 0.0f && v <= 1000.0f) {
      target = v;
      if (v > 30.0f || v == 0.0f) {
        g_unlock_framerate.store(true, std::memory_order_relaxed);
      }
    }
  }
  g_fps_target.store(target, std::memory_order_relaxed);
  NdSyncFpsClampMemory(target);
  NdNotifyAsuraFpsIntervalChanged();
  REXLOG_INFO("neverdead: [FPS] Initialized at {:.1f} FPS (0.0 = unlimited, unlock={})",
              target, g_unlock_framerate.load());
}

void NdCheckFpsToggleKey() {
  NdInitFpsMode();
#ifdef _WIN32
  static std::atomic<bool> s_f8_was_down{false};
  const SHORT f8_state = GetAsyncKeyState(VK_F8);
  const bool f8_down = (f8_state & 0x8000) != 0;
  if (f8_down && !s_f8_was_down.exchange(true)) {
    const bool next_unlock = !g_unlock_framerate.load(std::memory_order_relaxed);
    g_unlock_framerate.store(next_unlock, std::memory_order_relaxed);
    const float next = next_unlock ? 0.0f : 30.0f;
    g_fps_target.store(next, std::memory_order_relaxed);
    NdSyncFpsClampMemory(next);
    NdNotifyAsuraFpsIntervalChanged();
    NdSaveSettingsToIni();
    REXLOG_INFO("neverdead: [FPS-TOGGLE] F8 pressed: mode switched to {}",
                next_unlock ? "Unlocked (0.0f)" : "30 FPS (30.0f)");
  } else if (!f8_down && s_f8_was_down.load(std::memory_order_relaxed)) {
    s_f8_was_down.store(false, std::memory_order_relaxed);
  }
#endif
}

void NdAutoAdvanceInput(PPCContext& ctx, uint8_t* base, uint32_t guest_state) {
  if (ctx.r3.u32 != 0 && ctx.r3.u32 != 254 && ctx.r3.u32 != 1167) {
    return;
  }
  if (g_in_3d_gameplay.load(std::memory_order_relaxed)) {
    return;
  }

  static const bool s_enabled = []() {
    const char* env = std::getenv("ND_AUTO_NAV");
    return env != nullptr && std::atoi(env) == 1;
  }();

  static const auto s_t0 = std::chrono::steady_clock::now();
  const auto now = std::chrono::steady_clock::now();
  const auto elapsed_ms =
      std::chrono::duration_cast<std::chrono::milliseconds>(now - s_t0).count();

  uint16_t inject_btn = 0;
  const char* btn_name = "NONE";

  // Skip the opening logo video automatically during startup (< 2.5s).
  static std::atomic<bool> s_startup_intro_skipped{false};
  if (!s_startup_intro_skipped.load(std::memory_order_relaxed) && elapsed_ms < 2500) {
    inject_btn = 0x0010;  // XINPUT_GAMEPAD_START
    btn_name = "START (INTRO SKIP)";
    if (elapsed_ms > 400) {
      s_startup_intro_skipped.store(true, std::memory_order_relaxed);
    }
  } else if (!s_enabled) {
    return;
  } else {
    const uint64_t cycle_ms = 1200;
    const uint64_t phase = static_cast<uint64_t>(elapsed_ms) % cycle_ms;
    const bool button_down = (phase < 400);

    if (button_down) {
      if (elapsed_ms < 16000) {
        inject_btn = 0x0010;  // XINPUT_GAMEPAD_START
        btn_name = "START";
      } else {
        inject_btn = 0x1000;  // XINPUT_GAMEPAD_A
        btn_name = "A";
      }
    }
  }

  static bool s_was_down = false;
  if (inject_btn != 0 && !s_was_down) {
    s_was_down = true;
    REXLOG_INFO("neverdead: [AUTO-NAV] t={:.1f}s -> Pressing {}", elapsed_ms / 1000.0, btn_name);
  } else if (inject_btn == 0 && s_was_down) {
    s_was_down = false;
  }

  if (inject_btn != 0 && guest_state != 0 && base != nullptr) {
    uint8_t* p8 = base + (guest_state + 8);
    uint8_t* p4 = base + (guest_state + 4);

    uint16_t cur8 = (static_cast<uint16_t>(p8[0]) << 8) | p8[1];
    cur8 |= inject_btn;
    p8[0] = static_cast<uint8_t>(cur8 >> 8);
    p8[1] = static_cast<uint8_t>(cur8 & 0xFF);

    uint16_t cur4 = (static_cast<uint16_t>(p4[0]) << 8) | p4[1];
    cur4 |= inject_btn;
    p4[0] = static_cast<uint8_t>(cur4 >> 8);
    p4[1] = static_cast<uint8_t>(cur4 & 0xFF);

    ctx.r3.s64 = 0;  // ERROR_SUCCESS
  }
}
