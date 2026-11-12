// Overrides for inline XEX import stubs (0x827BD8A4..0x827BE5D4).
//
// The XEX2 import library table points to an unused region, while actual calls
// target these 16-byte inline thunks whose recompiled bodies are raw stub bytes.
// Each stub is overridden via REX_HOOK_RAW (strong symbol replacing the weak
// recompiled alias) and delegated to the runtime wrapper __imp__<Name>(ctx, base)
// or a safe host implementation.
//
// sub_827BD8F4 (0x155 KeTlsSetValue) is implemented in nd_ke_tls_fix.h.

#pragma once

#include <atomic>
#include <cstdint>
#include <cstdlib>
#include <cstring>

#include <xmmintrin.h>  // _mm_getcsr / _mm_setcsr for NdiPinFlushToZero

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#endif

#include <rex/hook.h>
#include <rex/logging.h>

namespace neverdead::sync {

// Lock table for guest RTL_CRITICAL_SECTION addresses.
// Fixed 4096-slot open-addressing table using atomic keys and native Win32
// CRITICAL_SECTION objects (which are recursive per thread and fast in user mode).
class LockManager {
 public:
  static LockManager& Instance() {
    static LockManager inst;
    return inst;
  }

  CRITICAL_SECTION* GetOrCreate(uint32_t cs) {
    constexpr uint32_t kMask = kSlots - 1;
    const uint32_t h = (cs * 2654435761u) & kMask;
    for (uint32_t probe = 0; probe < kSlots; ++probe) {
      Slot& slot = slots_[(h + probe) & kMask];
      const uint32_t key = slot.key.load(std::memory_order_acquire);
      if (key == cs) {
        WaitReady(slot);
        return &slot.cs;
      }
      if (key == 0) {
        uint32_t expected = 0;
        if (slot.key.compare_exchange_strong(expected, cs, std::memory_order_acq_rel)) {
          InitializeCriticalSectionAndSpinCount(&slot.cs, 4000);
          slot.ready.store(true, std::memory_order_release);
          return &slot.cs;
        }
        if (expected == cs) {  // Another thread claimed this slot concurrently.
          WaitReady(slot);
          return &slot.cs;
        }
      }
    }
    // Table full: serialize on the global fallback lock to preserve correctness.
    return &fallback_;
  }

 private:
  static constexpr uint32_t kSlots = 4096;
  struct Slot {
    std::atomic<uint32_t> key{0};
    std::atomic<bool> ready{false};
    CRITICAL_SECTION cs{};
  };

  static void WaitReady(Slot& slot) {
    while (!slot.ready.load(std::memory_order_acquire)) {
      YieldProcessor();
    }
  }

  LockManager() { InitializeCriticalSectionAndSpinCount(&fallback_, 4000); }

  Slot slots_[kSlots]{};
  CRITICAL_SECTION fallback_{};
};

}  // namespace neverdead::sync

namespace neverdead::fix {

// Diagnostic logging for high-frequency hooks is disabled unless ND_DIAG=1.
inline bool NdiDiagEnabled() {
  static const bool s_diag = []() {
    const char* e = std::getenv("ND_DIAG");
    return e != nullptr && e[0] == '1' && e[1] == '\0';
  }();
  return s_diag;
}

}  // namespace neverdead::fix

#define ND_IMPORT_REAL(sym, va, ord, fname)                              \
  REX_EXTERN(__imp__##fname);                                            \
  REX_HOOK_RAW(sym) {                                                    \
    static bool nd_called = false;                                       \
    if (!nd_called) {                                                    \
      nd_called = true;                                                  \
      REXLOG_INFO(                                                       \
          "neverdead: IMPORT-REAL va=0x{:08X} ord=0x{:03X} {} lr=0x{:08X}", \
          va, ord, #fname, (uint32_t)ctx.lr);                            \
    }                                                                    \
    __imp__##fname(ctx, base);                                           \
  }

inline bool NdiIsInvalidOrDummyCS(uint32_t cs) {
  return (cs < 0x10000u || (cs & 0xFFFF0000u) == 0x3F800000u || cs == 0x3D634BD7u);
}

// Keep FTZ|DAZ (0x8040) pinned in MXCSR on guest threads, since runtime thread
// setup and trap restore paths can reset flush mode. Amortized every 256 locks.
inline void NdiPinFlushToZero() {
  static thread_local uint32_t s_counter = 0;
  ++s_counter;
  if (s_counter != 1u && (s_counter & 0xFFu) != 0u) {
    return;
  }
  _mm_setcsr(_mm_getcsr() | 0x8040u);  // FTZ (0x8000) | DAZ (0x0040)
}

REX_HOOK_RAW(sub_827BD8A4) {  // 0x12E RtlInitializeCriticalSection
  const uint32_t cs = ctx.r3.u32;
  if (NdiIsInvalidOrDummyCS(cs)) {
    return;
  }
  neverdead::sync::LockManager::Instance().GetOrCreate(cs);
}

REX_HOOK_RAW(sub_827BD8B4) {  // 0x125 RtlEnterCriticalSection
  NdiPinFlushToZero();
  const uint32_t cs = ctx.r3.u32;
  if (NdiIsInvalidOrDummyCS(cs)) {
    return;
  }
  EnterCriticalSection(neverdead::sync::LockManager::Instance().GetOrCreate(cs));
}

REX_HOOK_RAW(sub_827BD8C4) {  // 0x130 RtlLeaveCriticalSection
  const uint32_t cs = ctx.r3.u32;
  if (NdiIsInvalidOrDummyCS(cs)) {
    return;
  }
  // Drop LeaveCriticalSection if the current thread does not own the lock,
  // avoiding undefined behavior when guest error paths unlock unacquired CSs.
  CRITICAL_SECTION* cs_ptr = neverdead::sync::LockManager::Instance().GetOrCreate(cs);
  if (reinterpret_cast<uintptr_t>(cs_ptr->OwningThread) !=
      static_cast<uintptr_t>(GetCurrentThreadId())) {
    if (neverdead::fix::NdiDiagEnabled()) {
      static std::atomic<uint32_t> s_dropped{0};
      const uint32_t total = s_dropped.fetch_add(1, std::memory_order_relaxed) + 1;
      if (total == 1 || (total % 1000) == 0) {
        REXLOG_INFO(
            "neverdead: [CS-GUARD] Dropped LeaveCriticalSection (non-owner) "
            "cs=0x{:08X} tid={} owner=0x{:X} total={}",
            cs, GetCurrentThreadId(),
            reinterpret_cast<uintptr_t>(cs_ptr->OwningThread), total);
      }
    }
    return;
  }
  LeaveCriticalSection(cs_ptr);
}

ND_IMPORT_REAL(sub_827BD8D4, 0x827BD8D4u, 0x1A5, __C_specific_handler)
ND_IMPORT_REAL(sub_827BD8E4, 0x827BD8E4u, 0x154, KeTlsGetValue)

REX_HOOK_RAW(sub_827BD904) {  // 0x12F RtlInitializeCriticalSectionAndSpinCount
  const uint32_t cs = ctx.r3.u32;
  if (NdiIsInvalidOrDummyCS(cs)) {
    ctx.r3.u64 = 1;
    return;
  }
  neverdead::sync::LockManager::Instance().GetOrCreate(cs);
  ctx.r3.u64 = 1;
}

ND_IMPORT_REAL(sub_827BD914, 0x827BD914u, 0x152, KeTlsAlloc)

REX_HOOK_RAW(sub_827BD924) {  // 0x141 RtlTryEnterCriticalSection
  const uint32_t cs = ctx.r3.u32;
  if (NdiIsInvalidOrDummyCS(cs)) {
    ctx.r3.u64 = 1;
    return;
  }
  const BOOL ok = TryEnterCriticalSection(neverdead::sync::LockManager::Instance().GetOrCreate(cs));
  ctx.r3.u64 = ok ? 1 : 0;
}

ND_IMPORT_REAL(sub_827BD944, 0x827BD944u, 0x12C, RtlInitAnsiString)
ND_IMPORT_REAL(sub_827BD974, 0x827BD974u, 0x194, XexCheckExecutablePrivilege)
ND_IMPORT_REAL(sub_827BD9A4, 0x827BD9A4u, 0x136, RtlRaiseException)
ND_IMPORT_REAL(sub_827BD9B4, 0x827BD9B4u, 0x105, ObDereferenceObject)
ND_IMPORT_REAL(sub_827BD9D4, 0x827BD9D4u, 0x110, ObReferenceObjectByHandle)
ND_IMPORT_REAL(sub_827BDA24, 0x827BDA24u, 0x140, RtlTimeToTimeFields)
ND_IMPORT_REAL(sub_827BDAB4, 0x827BDAB4u, 0x11D, RtlCompareStringN)
ND_IMPORT_REAL(sub_827BDAE4, 0x827BDAE4u, 0x12B, RtlImageXexHeaderField)
ND_IMPORT_REAL(sub_827BDB14, 0x827BDB14u, 0x19B, XexLoadImageHeaders)
ND_IMPORT_REAL(sub_827BDB24, 0x827BDB24u, 0x135, RtlNtStatusToDosError)
ND_IMPORT_REAL(sub_827BDB34, 0x827BDB34u, 0x13A, _snprintf)
ND_IMPORT_REAL(sub_827BDBF4, 0x827BDBF4u, 0x101, NtYieldExecution)
ND_IMPORT_REAL(sub_827BDC44, 0x827BDC44u, 0x1A1, XexUnloadImage)
ND_IMPORT_REAL(sub_827BDC54, 0x827BDC54u, 0x197, XexGetProcedureAddress)
ND_IMPORT_REAL(sub_827BDC64, 0x827BDC64u, 0x199, XexLoadImage)
ND_IMPORT_REAL(sub_827BDC74, 0x827BDC74u, 0x143, RtlUnicodeToMultiByteN)
ND_IMPORT_REAL(sub_827BDC94, 0x827BDC94u, 0x13F, RtlTimeFieldsToTime)
ND_IMPORT_REAL(sub_827BDCC4, 0x827BDCC4u, 0x192, XeCryptSha)
ND_IMPORT_REAL(sub_827BDCD4, 0x827BDCD4u, 0x10F, ObReferenceObject)
ND_IMPORT_REAL(sub_827BDD74, 0x827BDD74u, 0x100, NtWriteFileGather)
ND_IMPORT_REAL(sub_827BDDA4, 0x827BDDA4u, 0x149, RtlUpcaseUnicodeChar)
ND_IMPORT_REAL(sub_827BDDB4, 0x827BDDB4u, 0x109, ObIsTitleObject)
ND_IMPORT_REAL(sub_827BDE74, 0x827BDE74u, 0x103, ObCreateSymbolicLink)
ND_IMPORT_REAL(sub_827BDE84, 0x827BDE84u, 0x104, ObDeleteSymbolicLink)
ND_IMPORT_REAL(sub_827BDEB4, 0x827BDEB4u, 0x195, XexGetModuleHandle)
ND_IMPORT_REAL(sub_827BDF04, 0x827BDF04u, 0x126, RtlFillMemoryUlong)
ND_IMPORT_REAL(sub_827BDF24, 0x827BDF24u, 0x11B, RtlCompareMemoryUlong)
ND_IMPORT_REAL(sub_827BDF44, 0x827BDF44u, 0x1D9, VdSetSystemCommandBufferGpuIdentifierAddress)
ND_IMPORT_REAL(sub_827BDF54, 0x827BDF54u, 0x1DF, KiApcNormalRoutineNop)

// xboxkrnl#0x0BE (MmGetPhysicalAddress): fall back to Xbox 360 direct physical
// mask (& 0x1FFFFFFF) if the runtime returns 0 for an untracked guest address.
REX_EXTERN(__imp__MmGetPhysicalAddress);
REX_HOOK_RAW(sub_827BDF64) {
  static bool s_diag_printed = false;
  const uint32_t virt_in = ctx.r3.u32;
  __imp__MmGetPhysicalAddress(ctx, base);
  if (ctx.r3.u32 == 0 && virt_in != 0) {
    ctx.r3.u64 = virt_in & 0x1FFFFFFFu;
    REXLOG_INFO("neverdead: MmGetPhysicalAddress virt=0x{:08X} -> fallback phys=0x{:08X} lr=0x{:08X}",
                virt_in, ctx.r3.u32, (uint32_t)ctx.lr);
  } else if (!s_diag_printed) {
    s_diag_printed = true;
    REXLOG_INFO("neverdead: MmGetPhysicalAddress virt=0x{:08X} -> phys=0x{:08X} lr=0x{:08X}",
                virt_in, ctx.r3.u32, (uint32_t)ctx.lr);
  }
}

ND_IMPORT_REAL(sub_827BDF94, 0x827BDF94u, 0x1B6, VdEnableRingBufferRPtrWriteBack)

REX_EXTERN(__imp__VdInitializeRingBuffer);
REX_HOOK_RAW(sub_827BDFA4) {
  const uint32_t phys_base = ctx.r3.u32;
  const uint32_t log2_size = ctx.r4.u32;
  const uint32_t size_bytes = (log2_size > 0 && log2_size < 32) ? (1u << log2_size) : 0;
  REXLOG_INFO("neverdead: VdInitializeRingBuffer phys_base=0x{:08X} log2_size={} (size={} bytes) lr=0x{:08X}",
              phys_base, log2_size, size_bytes, (uint32_t)ctx.lr);
  __imp__VdInitializeRingBuffer(ctx, base);
}

ND_IMPORT_REAL(sub_827BDFC4, 0x827BDFC4u, 0x1C7, VdPersistDisplay)
ND_IMPORT_REAL(sub_827BDFE4, 0x827BDFE4u, 0x1BD, VdGetSystemCommandBuffer)
ND_IMPORT_REAL(sub_827BDFF4, 0x827BDFF4u, 0x13B, sprintf)
ND_IMPORT_REAL(sub_827BE004, 0x827BE004u, 0x1B4, VdEnableDisableClockGating)
ND_IMPORT_REAL(sub_827BE014, 0x827BE014u, 0x1B9, VdGetCurrentDisplayGamma)
ND_IMPORT_REAL(sub_827BE024, 0x827BE024u, 0x1D3, VdSetDisplayMode)
ND_IMPORT_REAL(sub_827BE034, 0x827BE034u, 0x1CA, VdQueryVideoMode)
ND_IMPORT_REAL(sub_827BE044, 0x827BE044u, 0x1BA, VdGetCurrentDisplayInformation)
ND_IMPORT_REAL(sub_827BE054, 0x827BE054u, 0x1C9, VdQueryVideoFlags)

REX_EXTERN(__imp__VdSetGraphicsInterruptCallback);
REX_HOOK_RAW(sub_827BE064) {
  REXLOG_INFO(
      "neverdead: IMPORT-REAL va=0x827BE064 ord=0x1D5 VdSetGraphicsInterruptCallback lr=0x{:08X} pfnCallback=0x{:08X} userData=0x{:08X}",
      (uint32_t)ctx.lr, ctx.r3.u32, ctx.r4.u32);
  __imp__VdSetGraphicsInterruptCallback(ctx, base);
}

ND_IMPORT_REAL(sub_827BE074, 0x827BE074u, 0x1D4, VdSetDisplayModeOverride)

REX_EXTERN(__imp__VdInitializeEngines);
REX_HOOK_RAW(sub_827BE084) {
  REXLOG_INFO("neverdead: IMPORT-REAL va=0x827BE084 ord=0x1C2 VdInitializeEngines CALLED! lr=0x{:08X}", (uint32_t)ctx.lr);
  __imp__VdInitializeEngines(ctx, base);
}

ND_IMPORT_REAL(sub_827BE094, 0x827BE094u, 0x1C6, VdIsHSIOTrainingSucceeded)
ND_IMPORT_REAL(sub_827BE0A4, 0x827BE0A4u, 0x1DC, VdShutdownEngines)
ND_IMPORT_REAL(sub_827BE0B4, 0x827BE0B4u, 0x1B1, VdCallGraphicsNotificationRoutines)
ND_IMPORT_REAL(sub_827BE0C4, 0x827BE0C4u, 0x14D, _vsnprintf)
ND_IMPORT_REAL(sub_827BE104, 0x827BE104u, 0x1C5, VdInitializeScalerCommandBuffer)
ND_IMPORT_REAL(sub_827BE464, 0x827BE464u, 0x1F8, XAudioGetVoiceCategoryVolume)
ND_IMPORT_REAL(sub_827BE4B4, 0x827BE4B4u, 0x1F5, XAudioSubmitRenderDriverFrame)
ND_IMPORT_REAL(sub_827BE4C4, 0x827BE4C4u, 0x1F4, XAudioUnregisterRenderDriverClient)
ND_IMPORT_REAL(sub_827BE4D4, 0x827BE4D4u, 0x1F3, XAudioRegisterRenderDriverClient)
ND_IMPORT_REAL(sub_827BE4E4, 0x827BE4E4u, 0x1FF, XAudioGetSpeakerConfig)
ND_IMPORT_REAL(sub_827BE554, 0x827BE554u, 0x153, KeTlsFree)
ND_IMPORT_REAL(sub_827BE564, 0x827BE564u, 0x147, RtlUnwind)
ND_IMPORT_REAL(sub_827BE5A4, 0x827BE5A4u, 0x127, RtlFreeAnsiString)
ND_IMPORT_REAL(sub_827BE5B4, 0x827BE5B4u, 0x142, RtlUnicodeStringToAnsiString)
ND_IMPORT_REAL(sub_827BE5C4, 0x827BE5C4u, 0x12D, RtlInitUnicodeString)

// Safe host RtlMultiByteToUnicodeN conversion.
REX_HOOK_RAW(sub_827BE5D4) {
  uint32_t dst = ctx.r3.u32;
  uint32_t max_bytes = ctx.r4.u32;
  uint32_t p_bytes = ctx.r5.u32;
  uint32_t src = ctx.r6.u32;
  uint32_t src_bytes = ctx.r7.u32;

  if (neverdead::fix::NdiDiagEnabled()) {
    REXLOG_INFO("neverdead: RtlMultiByteToUnicodeN dst=0x{:08X} max_b={} p_b=0x{:08X} src=0x{:08X} src_b={} lr=0x{:08X}",
                dst, max_bytes, p_bytes, src, src_bytes, (uint32_t)ctx.lr);
  }
  if (dst == 0 || max_bytes == 0 || src == 0) {
    if (p_bytes != 0) REX_STORE_U32(p_bytes, 0);
    ctx.r3.u64 = 0;
    return;
  }

  uint32_t chars_to_copy = max_bytes / 2;
  if (src_bytes > 0 && src_bytes < chars_to_copy) {
    chars_to_copy = src_bytes;
  }
  uint32_t written = 0;
  for (uint32_t i = 0; i < chars_to_copy; ++i) {
    uint8_t ch = REX_LOAD_U8(src + i);
    REX_STORE_U16(dst + i * 2, static_cast<uint16_t>(ch));
    written += 2;
    if (ch == 0 && src_bytes == 0) break;
  }
  if (p_bytes != 0) {
    REX_STORE_U32(p_bytes, written);
  }
  ctx.r3.u64 = 0;
}

// Ordinal 0x052 is RtlSetLastWin32Error / SetLastError (avoiding ExGetXConfigSetting clobbering r3).
REX_HOOK_RAW(sub_827BDF34) {
  ::SetLastError(ctx.r3.u32);
}

// _getptd_noexit (sub_8278BCA0): allocate a single 4KB guest PTD block per thread
// instead of repeatedly growing the guest bump allocator by 64KB per call.
REX_HOOK_RAW(sub_8278BCA0) {
  static thread_local uint32_t s_thread_ptd = 0;
  if (s_thread_ptd == 0) {
    static std::atomic<uint32_t> s_ptd_alloc{0x30100000u};
    uint32_t ptd = s_ptd_alloc.fetch_add(0x1000u);
    void* host_ptr = reinterpret_cast<void*>(reinterpret_cast<uintptr_t>(base) + ptd);
    VirtualAlloc(host_ptr, 0x1000, MEM_COMMIT, PAGE_READWRITE);
    std::memset(host_ptr, 0, 0x1000);
    s_thread_ptd = ptd;
    REXLOG_INFO("neverdead: _getptd (sub_8278BCA0) initialized PTD for thread at guest=0x{:08X}", ptd);
  }
  ctx.r3.u64 = s_thread_ptd;
  ctx.r31.u64 = s_thread_ptd;
}

ND_IMPORT_REAL(sub_827BD934, 0x827BD934u, 0x0C4, MmQueryAddressProtect)
ND_IMPORT_REAL(sub_827BD954, 0x827BD954u, 0x0D5, NtCreateSemaphore)
ND_IMPORT_REAL(sub_827BD964, 0x827BD964u, 0x0F3, NtReleaseSemaphore)
ND_IMPORT_REAL(sub_827BD984, 0x827BD984u, 0x0BA, MmAllocatePhysicalMemoryEx)
ND_IMPORT_REAL(sub_827BD994, 0x827BD994u, 0x0BD, MmFreePhysicalMemory)
ND_IMPORT_REAL(sub_827BD9C4, 0x827BD9C4u, 0x099, KeSetBasePriorityThread)
ND_IMPORT_REAL(sub_827BD9E4, 0x827BD9E4u, 0x081, KeQueryBasePriorityThread)
ND_IMPORT_REAL(sub_827BD9F4, 0x827BD9F4u, 0x0E3, NtQueueApcThread)
ND_IMPORT_REAL(sub_827BDA04, 0x827BDA04u, 0x097, KeSetAffinityThread)
ND_IMPORT_REAL(sub_827BDA14, 0x827BDA14u, 0x019, ExTerminateThread)
ND_IMPORT_REAL(sub_827BDA34, 0x827BDA34u, 0x084, KeQuerySystemTime)
ND_IMPORT_REAL(sub_827BDA44, 0x827BDA44u, 0x0DF, NtOpenFile)
ND_IMPORT_REAL(sub_827BDA54, 0x827BDA54u, 0x0D2, NtCreateFile)
ND_IMPORT_REAL(sub_827BDA64, 0x827BDA64u, 0x066, KeGetCurrentProcessType)
ND_IMPORT_REAL(sub_827BDA74, 0x827BDA74u, 0x0FD, NtWaitForSingleObjectEx)
ND_IMPORT_REAL(sub_827BDA84, 0x827BDA84u, 0x0FF, NtWriteFile)
ND_IMPORT_REAL(sub_827BDA94, 0x827BDA94u, 0x0CF, NtClose)
ND_IMPORT_REAL(sub_827BDAA4, 0x827BDAA4u, 0x0F7, NtSetInformationFile)
ND_IMPORT_REAL(sub_827BDAC4, 0x827BDAC4u, 0x0EF, NtQueryVolumeInformationFile)
// Recompiled call sites invoke __imp__NtQueryInformationFile directly, so
// XFileXctdCompressionInformation (class 27) noise cannot be filtered in this stub.
ND_IMPORT_REAL(sub_827BDAD4, 0x827BDAD4u, 0x0E8, NtQueryInformationFile)
ND_IMPORT_REAL(sub_827BDAF4, 0x827BDAF4u, 0x0D9, NtDeviceIoControlFile)
ND_IMPORT_REAL(sub_827BDB04, 0x827BDB04u, 0x0D1, NtCreateEvent)
ND_IMPORT_REAL(sub_827BDB44, 0x827BDB44u, 0x0E7, NtQueryFullAttributesFile)
ND_IMPORT_REAL(sub_827BDB54, 0x827BDB54u, 0x0F5, NtResumeThread)
ND_IMPORT_REAL(sub_827BDB64, 0x827BDB64u, 0x083, KeQueryPerformanceFrequency)
ND_IMPORT_REAL(sub_827BDB74, 0x827BDB74u, 0x0F6, NtSetEvent)
ND_IMPORT_REAL(sub_827BDB84, 0x827BDB84u, 0x0CE, NtClearEvent)
ND_IMPORT_REAL(sub_827BDB94, 0x827BDB94u, 0x0EE, NtQueryVirtualMemory)
ND_IMPORT_REAL(sub_827BDBA4, 0x827BDBA4u, 0x0C6, MmQueryStatistics)
ND_IMPORT_REAL(sub_827BDBB4, 0x827BDBB4u, 0x010, ExGetXConfigSetting)
ND_IMPORT_REAL(sub_827BDBC4, 0x827BDBC4u, 0x003, DbgPrint)
ND_IMPORT_REAL(sub_827BDBD4, 0x827BDBD4u, 0x0D4, NtCreateMutant)
ND_IMPORT_REAL(sub_827BDBE4, 0x827BDBE4u, 0x0F2, NtReleaseMutant)
ND_IMPORT_REAL(sub_827BDC24, 0x827BDC24u, 0x0F0, NtReadFile)
ND_IMPORT_REAL(sub_827BDC34, 0x827BDC34u, 0x0DA, NtDuplicateObject)
ND_IMPORT_REAL(sub_827BDC84, 0x827BDC84u, 0x05A, KeDelayExecutionThread)
ND_IMPORT_REAL(sub_827BDCA4, 0x827BDCA4u, 0x00D, ExCreateThread)
ND_IMPORT_REAL(sub_827BDCE4, 0x827BDCE4u, 0x037, IoCreateDevice)
ND_IMPORT_REAL(sub_827BDCF4, 0x827BDCF4u, 0x07D, KeLeaveCriticalRegion)
ND_IMPORT_REAL(sub_827BDD04, 0x827BDD04u, 0x039, IoDeleteDevice)
ND_IMPORT_REAL(sub_827BDD14, 0x827BDD14u, 0x05F, KeEnterCriticalRegion)
ND_IMPORT_REAL(sub_827BDD24, 0x827BDD24u, 0x0B0, KeWaitForSingleObject)
ND_IMPORT_REAL(sub_827BDD34, 0x827BDD34u, 0x09D, KeSetEvent)
ND_IMPORT_REAL(sub_827BDD44, 0x827BDD44u, 0x00B, ExAllocatePoolTypeWithTag)
ND_IMPORT_REAL(sub_827BDD54, 0x827BDD54u, 0x00F, ExFreePool)
ND_IMPORT_REAL(sub_827BDD64, 0x827BDD64u, 0x035, IoCompleteRequest)
ND_IMPORT_REAL(sub_827BDD84, 0x827BDD84u, 0x0B4, KfReleaseSpinLock)
ND_IMPORT_REAL(sub_827BDD94, 0x827BDD94u, 0x0B1, KfAcquireSpinLock)
ND_IMPORT_REAL(sub_827BDDC4, 0x827BDDC4u, 0x034, IoCheckShareAccess)
ND_IMPORT_REAL(sub_827BDDD4, 0x827BDDD4u, 0x047, NetDll_XNetQosServiceLookup)
ND_IMPORT_REAL(sub_827BDDE4, 0x827BDDE4u, 0x045, NetDll_XNetQosListen)
ND_IMPORT_REAL(sub_827BDE14, 0x827BDE14u, 0x257, XeKeysConsoleSignatureVerification)
ND_IMPORT_REAL(sub_827BDE24, 0x827BDE24u, 0x256, XeKeysConsolePrivateKeySign)
ND_IMPORT_REAL(sub_827BDE34, 0x827BDE34u, 0x0DC, NtFreeVirtualMemory)
ND_IMPORT_REAL(sub_827BDE44, 0x827BDE44u, 0x0CC, NtAllocateVirtualMemory)
ND_IMPORT_REAL(sub_827BDE54, 0x827BDE54u, 0x0DB, NtFlushBuffersFile)
ND_IMPORT_REAL(sub_827BDE64, 0x827BDE64u, 0x03B, IoDismountVolume)
ND_IMPORT_REAL(sub_827BDE94, 0x827BDE94u, 0x08F, KeResetEvent)
ND_IMPORT_REAL(sub_827BDEA4, 0x827BDEA4u, 0x015, ExRegisterTitleTerminateNotification)
ND_IMPORT_REAL(sub_827BDEC4, 0x827BDEC4u, 0x0C0, MmLockAndMapSegmentArray)
ND_IMPORT_REAL(sub_827BDED4, 0x827BDED4u, 0x0C9, MmUnlockAndUnmapSegmentArray)
ND_IMPORT_REAL(sub_827BDEE4, 0x827BDEE4u, 0x028, HalReturnToFirmware)
ND_IMPORT_REAL(sub_827BDEF4, 0x827BDEF4u, 0x03C, IoDismountVolumeByFileHandle)
ND_IMPORT_REAL(sub_827BDF14, 0x827BDF14u, 0x053, KeBugCheckEx)
ND_IMPORT_REAL(sub_827BDF74, 0x827BDF74u, 0x089, KeReleaseSpinLockFromRaisedIrql)
ND_IMPORT_REAL(sub_827BDF84, 0x827BDF84u, 0x07B, KeInsertQueueDpc)
ND_IMPORT_REAL(sub_827BDFB4, 0x827BDFB4u, 0x04D, KeAcquireSpinLockAtRaisedIrql)

REX_EXTERN(__imp__VdSwap);
REX_HOOK_RAW(sub_827BDFD4) {
  __imp__VdSwap(ctx, base);
}

ND_IMPORT_REAL(sub_827BE0D4, 0x827BE0D4u, 0x06F, KeInitializeDpc)
ND_IMPORT_REAL(sub_827BE0E4, 0x827BE0E4u, 0x06B, KeLockL2)
ND_IMPORT_REAL(sub_827BE0F4, 0x827BE0F4u, 0x06C, KeUnlockL2)
ND_IMPORT_REAL(sub_827BE114, 0x827BE114u, 0x269, VdRetrainEDRAM)
ND_IMPORT_REAL(sub_827BE124, 0x827BE124u, 0x26A, VdRetrainEDRAMWorker)
ND_IMPORT_REAL(sub_827BE134, 0x827BE134u, 0x09A, KeSetCurrentProcessType)
ND_IMPORT_REAL(sub_827BE474, 0x827BE474u, 0x0C2, MmMapIoSpace)
ND_IMPORT_REAL(sub_827BE484, 0x827BE484u, 0x224, XMACreateContext)
ND_IMPORT_REAL(sub_827BE494, 0x827BE494u, 0x226, XMAReleaseContext)
ND_IMPORT_REAL(sub_827BE4A4, 0x827BE4A4u, 0x0AF, KeWaitForMultipleObjects)
ND_IMPORT_REAL(sub_827BE4F4, 0x827BE4F4u, 0x350, XAudioGetDuckerLevel)
ND_IMPORT_REAL(sub_827BE504, 0x827BE504u, 0x34D, XAudioEnableDucker)
ND_IMPORT_REAL(sub_827BE514, 0x827BE514u, 0x355, XAudioGetDuckerReleaseTime)
ND_IMPORT_REAL(sub_827BE524, 0x827BE524u, 0x353, XAudioGetDuckerAttackTime)
ND_IMPORT_REAL(sub_827BE534, 0x827BE534u, 0x357, XAudioGetDuckerHoldTime)
ND_IMPORT_REAL(sub_827BE544, 0x827BE544u, 0x351, XAudioGetDuckerThreshold)
ND_IMPORT_REAL(sub_827BE574, 0x827BE574u, 0x0CD, NtCancelTimer)
ND_IMPORT_REAL(sub_827BE584, 0x827BE584u, 0x0FA, NtSetTimerEx)
ND_IMPORT_REAL(sub_827BE594, 0x827BE594u, 0x0D7, NtCreateTimer)

#undef ND_IMPORT_REAL
