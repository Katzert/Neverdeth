// Neverdeth - High-resolution frame pacer, render-timeout hook, and boundary reset.

#include "nd_internal.h"

#include <mutex>

std::atomic<bool> g_guest_throttle_active{false};

#ifdef _WIN32
LARGE_INTEGER g_qpf{};
LARGE_INTEGER g_guest_frame_start_qpc{};

namespace {

std::atomic<uint64_t> g_completed_swap_seq{0};
LARGE_INTEGER g_last_guest_swap_qpc{};
int64_t g_last_pacer_qpc = 0;

void NdWakeAddress(void* addr) {
  static auto s_fn = []() -> void(WINAPI*)(void*) {
    HMODULE h = GetModuleHandleA("api-ms-win-core-synch-l1-2-0.dll");
    if (!h) h = GetModuleHandleA("kernel32.dll");
    if (!h) h = LoadLibraryA("api-ms-win-core-synch-l1-2-0.dll");
    if (!h) return nullptr;
    return reinterpret_cast<void(WINAPI*)(void*)>(GetProcAddress(h, "WakeByAddressSingle"));
  }();
  if (s_fn) {
    s_fn(addr);
  }
}

}  // namespace
#endif  // _WIN32

void NdEnsureTimerResolution() {
#ifdef _WIN32
  static std::once_flag flag;
  std::call_once(flag, []() {
    HMODULE winmm = LoadLibraryA("winmm.dll");
    if (winmm == nullptr) return;
    using TimeBeginPeriodFn = UINT(WINAPI*)(UINT);
    auto p_timeBeginPeriod =
        reinterpret_cast<TimeBeginPeriodFn>(GetProcAddress(winmm, "timeBeginPeriod"));
    if (p_timeBeginPeriod != nullptr) {
      p_timeBeginPeriod(1);
    }
  });
#endif
}

void NdInitQpcOnce() {
#ifdef _WIN32
  static std::once_flag flag;
  std::call_once(flag, []() {
    QueryPerformanceFrequency(&g_qpf);
    QueryPerformanceCounter(&g_last_guest_swap_qpc);
    NdEnsureTimerResolution();
  });
#endif
}

void NdApplyFramePacer() {
#ifdef _WIN32
  if (g_guest_throttle_active.load(std::memory_order_acquire)) {
    return;
  }
  const float target_fps = g_fps_target.load(std::memory_order_relaxed);
  if (target_fps <= 0.0f) {
    g_last_pacer_qpc = 0;
    return;
  }

  NdInitQpcOnce();

  const int64_t target_ticks =
      static_cast<int64_t>(static_cast<double>(g_qpf.QuadPart) / target_fps);
  if (g_last_pacer_qpc == 0) {
    LARGE_INTEGER now_qpc{};
    QueryPerformanceCounter(&now_qpc);
    g_last_pacer_qpc = now_qpc.QuadPart;
    return;
  }

  const int64_t target_time = g_last_pacer_qpc + target_ticks;

  LARGE_INTEGER cur_qpc{};
  QueryPerformanceCounter(&cur_qpc);

  const int64_t remaining_ticks = target_time - cur_qpc.QuadPart;
  if (remaining_ticks > 0) {
    const double remaining_ms =
        (static_cast<double>(remaining_ticks) * 1000.0) / static_cast<double>(g_qpf.QuadPart);
    // Hybrid wait: yield CPU with Sleep() down to ~1.5 ms, then spin-wait for precision.
    if (remaining_ms > 2.0) {
      const DWORD sleep_ms = static_cast<DWORD>(remaining_ms - 1.5);
      if (sleep_ms > 0) {
        Sleep(sleep_ms);
      }
    }
    do {
      YieldProcessor();
      QueryPerformanceCounter(&cur_qpc);
    } while (cur_qpc.QuadPart < target_time);
  }

  if (cur_qpc.QuadPart - target_time > target_ticks) {
    g_last_pacer_qpc = cur_qpc.QuadPart;
  } else {
    g_last_pacer_qpc = target_time;
  }
#endif
}

void NdPaceGuestAfterSwapComplete(float target_fps, uint64_t swap_count) {
#ifdef _WIN32
  NdInitQpcOnce();

  g_completed_swap_seq.fetch_add(1, std::memory_order_release);
  NdWakeAddress(reinterpret_cast<void*>(&g_completed_swap_seq));

  if (swap_count <= 120 || target_fps <= 0.0f) {
    g_guest_throttle_active.store(false, std::memory_order_release);
    QueryPerformanceCounter(&g_last_guest_swap_qpc);
    QueryPerformanceCounter(&g_guest_frame_start_qpc);
    return;
  }

  g_guest_throttle_active.store(true, std::memory_order_release);

  const double target_sec = 1.0 / static_cast<double>(target_fps);
  LARGE_INTEGER now{};
  QueryPerformanceCounter(&now);
  double elapsed_sec =
      static_cast<double>(now.QuadPart - g_last_guest_swap_qpc.QuadPart) /
      static_cast<double>(g_qpf.QuadPart);

  if (elapsed_sec < target_sec) {
    const double remaining_ms = (target_sec - elapsed_sec) * 1000.0;
    if (remaining_ms > 1.8) {
      Sleep(static_cast<DWORD>(remaining_ms - 1.5));
    }
    do {
      YieldProcessor();
      QueryPerformanceCounter(&now);
      elapsed_sec = static_cast<double>(now.QuadPart - g_last_guest_swap_qpc.QuadPart) /
                    static_cast<double>(g_qpf.QuadPart);
    } while (elapsed_sec < target_sec);
  }
  g_last_guest_swap_qpc = now;
  QueryPerformanceCounter(&g_guest_frame_start_qpc);
#else
  (void)target_fps;
  (void)swap_count;
#endif
}

void NdAdjustRenderTimeoutHook(PPCRegister& r11) {
  const float target = g_fps_target.load(std::memory_order_relaxed);
  const bool in_3d = g_in_3d_gameplay.load(std::memory_order_relaxed);
  const bool unlocked = g_unlock_framerate.load(std::memory_order_relaxed) && in_3d;

  if (!unlocked || target == 30.0f) {
    r11.s64 = -333333;  // 33.33 ms (30 FPS)
  } else if (target <= 0.0f) {
    r11.s64 = -10000;   // 1.0 ms (Unlimited)
  } else {
    r11.s64 = -166667;  // 16.67 ms (60 FPS)
  }
}

extern "C" void NdResetXdkShadowStateOnBoundary() {
  NdResetXdkShadowState();
}
