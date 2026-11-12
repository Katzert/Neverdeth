// Neverdeth - DXGI SwapChain/Factory vtable hooks, game window subclassing, and present telemetry.

#include "nd_internal.h"

#ifdef _WIN32
#include <d3d12.h>
#include <dxgi.h>
#include <dxgi1_2.h>
#include <dxgi1_5.h>
#endif

#include <chrono>
#include <cstdio>
#include <mutex>

#include <rex/logging.h>

std::atomic<bool> g_unlock_framerate{true};
std::atomic<float> g_fps_target{0.0f};
std::atomic<bool> g_opt_luma_resolve{true};
std::atomic<uint32_t> g_guest_draws_this_frame{0};

std::atomic<uint64_t> g_vd_swap_calls{0};
std::atomic<uint64_t> g_last_presented_swap{0};
std::atomic<uint64_t> g_frames_presented{0};
std::atomic<double> g_present_fps{0.0};
std::atomic<uint64_t> g_present_logged_allowed{0};
std::atomic<uint64_t> g_present_logged_skipped{0};
std::atomic<void*> g_presenter_object{nullptr};

#ifdef _WIN32
std::atomic<bool> g_menu_toggle_request{false};

namespace {

using NdPresentFn = HRESULT(STDMETHODCALLTYPE*)(IDXGISwapChain*, UINT, UINT);
using NdCreateSwapChainForHwndFn = HRESULT(STDMETHODCALLTYPE*)(
    IDXGIFactory2*, IUnknown*, HWND, const DXGI_SWAP_CHAIN_DESC1*,
    const DXGI_SWAP_CHAIN_FULLSCREEN_DESC*, IDXGIOutput*, IDXGISwapChain1**);

struct NdSwapChainVtable {
  void** vtable = nullptr;
  NdPresentFn original_present = nullptr;
};

NdSwapChainVtable g_swapchain_vtables[8];
size_t g_swapchain_vtable_count = 0;
std::mutex g_swapchain_vtable_mutex;

NdCreateSwapChainForHwndFn g_real_create_swapchain_for_hwnd = nullptr;
std::mutex g_factory_hook_mutex;
bool g_factory_hook_installed = false;

std::atomic<bool> g_swapchain_allows_tearing{false};
std::atomic<int64_t> g_present_window_start_ticks{0};
std::atomic<uint64_t> g_present_window_allowed{0};
std::atomic<bool> g_present_stack_logged{false};

HWND g_game_hwnd = nullptr;
WNDPROC g_original_game_wndproc = nullptr;

NdPresentFn NdFindOriginalPresent(void* swap_chain) {
  void** vtable = *reinterpret_cast<void***>(swap_chain);
  std::lock_guard<std::mutex> lock(g_swapchain_vtable_mutex);
  for (size_t i = 0; i < g_swapchain_vtable_count; ++i) {
    if (g_swapchain_vtables[i].vtable == vtable) {
      return g_swapchain_vtables[i].original_present;
    }
  }
  return nullptr;
}

// Lockless window aggregation: one thread wins the CAS to flush telemetry every 2 seconds.
void NdPresentStatsTick(std::chrono::steady_clock::time_point now) {
  g_present_window_allowed.fetch_add(1, std::memory_order_relaxed);

  static std::atomic<bool> s_presenter_logged{false};
  if (!s_presenter_logged.load(std::memory_order_relaxed)) {
    void* presenter = g_presenter_object.load(std::memory_order_acquire);
    if (presenter != nullptr) {
      const uint8_t* p = static_cast<const uint8_t*>(presenter);
      REXLOG_INFO(
          "neverdead: [PAINT-MODE] presenter={:p} paint_mode_=+0xA8={} "
          "conn_state=+0xAC={} +0xB0={} implicit_vsync=+0xB1={} ui_drawers_size@+0x1C0={}",
          presenter, *reinterpret_cast<const int32_t*>(p + 0xA8),
          *reinterpret_cast<const int32_t*>(p + 0xAC), uint32_t(p[0xB0]), uint32_t(p[0xB1]),
          *reinterpret_cast<const uint64_t*>(p + 0x1C0));
      s_presenter_logged = true;
    }
  }

  const int64_t now_ticks = now.time_since_epoch().count();
  int64_t start_ticks = g_present_window_start_ticks.load(std::memory_order_relaxed);
  if (start_ticks == 0) {
    g_present_window_start_ticks.store(now_ticks, std::memory_order_relaxed);
    return;
  }

  const double elapsed =
      std::chrono::duration<double>(std::chrono::steady_clock::duration(now_ticks - start_ticks))
          .count();
  if (elapsed >= 2.0) {
    if (!g_present_window_start_ticks.compare_exchange_strong(start_ticks, now_ticks)) {
      return;
    }
    const uint64_t allowed_count = g_present_window_allowed.exchange(0, std::memory_order_relaxed);
    const double fps = double(allowed_count) / elapsed;
    g_present_fps.store(fps, std::memory_order_relaxed);
    g_present_logged_allowed.store(allowed_count, std::memory_order_relaxed);
    g_present_logged_skipped.store(0, std::memory_order_relaxed);
    if (NdIsDiagEnabled()) {
      REXLOG_INFO(
          "neverdead: [FRAME] dxgi_present_fps={:.1f} (allowed={}) "
          "paint_mode={} interval={:.2f}s",
          fps, allowed_count, NdReadPaintMode(), elapsed);
    }
  }
}

void NdLogPresentCallerOnce() {
  if (g_present_stack_logged.exchange(true, std::memory_order_relaxed)) {
    return;
  }
  void* frames[24] = {};
  const USHORT count = CaptureStackBackTrace(0, 24, frames, nullptr);
  REXLOG_INFO("neverdead: [PRESENT-TRACE] tid={} frames={}", GetCurrentThreadId(), count);
  for (USHORT i = 0; i < count; ++i) {
    HMODULE module = nullptr;
    wchar_t path[MAX_PATH] = {};
    const uintptr_t address = reinterpret_cast<uintptr_t>(frames[i]);
    if (GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                               GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                           reinterpret_cast<LPCWSTR>(address), &module) != 0 &&
        module != nullptr) {
      GetModuleFileNameW(module, path, MAX_PATH);
      const wchar_t* name = wcsrchr(path, L'\\');
      name = name ? name + 1 : path;
      char narrow[MAX_PATH] = {};
      WideCharToMultiByte(CP_UTF8, 0, name, -1, narrow, sizeof(narrow) - 1, nullptr, nullptr);
      REXLOG_INFO("neverdead: [PRESENT-TRACE]   #{} {} + 0x{:X}", i, narrow,
                  static_cast<unsigned long>(address - reinterpret_cast<uintptr_t>(module)));
    } else {
      REXLOG_INFO("neverdead: [PRESENT-TRACE]   #{} 0x{:X}", i,
                  static_cast<unsigned long>(address));
    }
  }
}

LRESULT CALLBACK NdGameSubclassWndProc(HWND hwnd, UINT msg, WPARAM wParam, LPARAM lParam) {
  if ((msg == WM_KEYDOWN || msg == WM_SYSKEYDOWN) && wParam == VK_F10) {
    HWND cfg = g_config_hwnd.load(std::memory_order_acquire);
    if (cfg == nullptr || !IsWindow(cfg)) {
      NdOpenConfigUI();
    } else {
      g_menu_toggle_request.store(true, std::memory_order_release);
    }
    return 0;
  }
  if (msg == WM_TIMER && wParam == 0x4E44) {
    NdUpdateShaderOsd(hwnd);
    return 0;
  }
  if (msg == WM_CLOSE || msg == WM_DESTROY) {
    REXLOG_INFO(
        "neverdead: [CLEAN-EXIT] WM_CLOSE/WM_DESTROY received on HWND {:p}; flushing PSO cache...",
        static_cast<void*>(hwnd));
    NdFlushPsoBundle();
    NdSavePipelineLibraryCache();
    fflush(nullptr);
    _flushall();
    TerminateProcess(GetCurrentProcess(), 0);
    return 0;
  }
  if (g_original_game_wndproc != nullptr) {
    return CallWindowProcW(g_original_game_wndproc, hwnd, msg, wParam, lParam);
  }
  return DefWindowProcW(hwnd, msg, wParam, lParam);
}

void NdHookSwapChainVtable(IDXGISwapChain* swap_chain);

extern "C" HRESULT STDMETHODCALLTYPE NdPresentHook(IDXGISwapChain* swap_chain,
                                                    UINT sync_interval, UINT flags) {
  if (swap_chain == nullptr) {
    return E_POINTER;
  }
  NdPresentFn real_present = NdFindOriginalPresent(swap_chain);
  if (real_present == nullptr) {
    REXLOG_ERROR("neverdead: [PRESENT-HOOK] Present called without registered original");
    return E_FAIL;
  }
  NdLogPresentCallerOnce();
  const auto now = std::chrono::steady_clock::now();

  NdPresentStatsTick(now);

  if (!NdIsFpsUnlocked() && !g_guest_throttle_active.load(std::memory_order_acquire)) {
    NdApplyFramePacer();
  }

  UINT present_sync = 0;
  UINT present_flags = flags;
  if (NdIsFpsUnlocked()) {
    present_sync = 0;
    if (g_swapchain_allows_tearing.load(std::memory_order_relaxed) && (flags == 0)) {
      present_flags |= DXGI_PRESENT_ALLOW_TEARING;
    }
  } else {
    present_sync = sync_interval;
  }

  HRESULT present_result = real_present(swap_chain, present_sync, present_flags);
  if (FAILED(present_result) && (present_flags & DXGI_PRESENT_ALLOW_TEARING)) {
    present_result = real_present(swap_chain, present_sync, flags);
  }
  if (SUCCEEDED(present_result)) {
    g_frames_presented.fetch_add(1, std::memory_order_release);
  }
  return present_result;
}

void NdHookSwapChainVtable(IDXGISwapChain* swap_chain) {
  void** vtable = *reinterpret_cast<void***>(swap_chain);
  std::lock_guard<std::mutex> lock(g_swapchain_vtable_mutex);
  for (size_t i = 0; i < g_swapchain_vtable_count; ++i) {
    if (g_swapchain_vtables[i].vtable == vtable) {
      return;
    }
  }
  if (g_swapchain_vtable_count >= 8) {
    return;
  }
  NdPresentFn original = reinterpret_cast<NdPresentFn>(vtable[8]);
  if (original == &NdPresentHook) {
    return;
  }
  DWORD old_protect = 0;
  if (VirtualProtect(&vtable[8], sizeof(void*), PAGE_READWRITE, &old_protect) == 0) {
    REXLOG_ERROR("neverdead: [PRESENT-HOOK] VirtualProtect on vtable[8] failed (err={})",
                 GetLastError());
    return;
  }
  g_swapchain_vtables[g_swapchain_vtable_count].vtable = vtable;
  g_swapchain_vtables[g_swapchain_vtable_count].original_present = original;
  ++g_swapchain_vtable_count;
  vtable[8] = reinterpret_cast<void*>(&NdPresentHook);
  DWORD ignored = 0;
  VirtualProtect(&vtable[8], sizeof(void*), old_protect, &ignored);
  FlushInstructionCache(GetCurrentProcess(), &vtable[8], sizeof(void*));
  REXLOG_INFO(
      "neverdead: [PRESENT-HOOK] hooked swapchain vtable[8] (Present): swap_chain={:p} "
      "original={:p}",
      static_cast<void*>(swap_chain), reinterpret_cast<void*>(original));
}

extern "C" HRESULT STDMETHODCALLTYPE NdCreateSwapChainForHwndDetour(
    IDXGIFactory2* factory, IUnknown* device, HWND hwnd, const DXGI_SWAP_CHAIN_DESC1* desc,
    const DXGI_SWAP_CHAIN_FULLSCREEN_DESC* fullscreen_desc, IDXGIOutput* restrict_to_output,
    IDXGISwapChain1** swap_chain_out) {
  if (g_real_create_swapchain_for_hwnd == nullptr) {
    return E_FAIL;
  }
  if (hwnd != nullptr && g_game_hwnd == nullptr) {
    g_game_hwnd = hwnd;
    g_original_game_wndproc = reinterpret_cast<WNDPROC>(
        SetWindowLongPtrW(hwnd, GWLP_WNDPROC, reinterpret_cast<LONG_PTR>(&NdGameSubclassWndProc)));
    SetTimer(hwnd, 0x4E44, 80, nullptr);
    REXLOG_INFO(
        "neverdead: [WINDOW] HWND={:p} subclassed for F10, OSD timer 0x4E44, and clean WM_CLOSE",
        static_cast<void*>(hwnd));
  }

  DXGI_SWAP_CHAIN_DESC1 mod_desc = *desc;
  BOOL allow_tearing = FALSE;
  IDXGIFactory5* factory5 = nullptr;
  if (SUCCEEDED(factory->QueryInterface(IID_PPV_ARGS(&factory5))) && factory5) {
    if (SUCCEEDED(factory5->CheckFeatureSupport(DXGI_FEATURE_PRESENT_ALLOW_TEARING,
                                                &allow_tearing, sizeof(allow_tearing))) &&
        allow_tearing) {
      mod_desc.Flags |= DXGI_SWAP_CHAIN_FLAG_ALLOW_TEARING;
      if (mod_desc.BufferCount < 3) {
        mod_desc.BufferCount = 3;
      }
      g_swapchain_allows_tearing.store(true, std::memory_order_relaxed);
      REXLOG_INFO("neverdead: [DXGI] DXGI_SWAP_CHAIN_FLAG_ALLOW_TEARING enabled (buffers={})",
                  mod_desc.BufferCount);
    }
    factory5->Release();
  }

  HRESULT hr = g_real_create_swapchain_for_hwnd(factory, device, hwnd, &mod_desc, fullscreen_desc,
                                                restrict_to_output, swap_chain_out);
  if (FAILED(hr) && allow_tearing) {
    hr = g_real_create_swapchain_for_hwnd(factory, device, hwnd, desc, fullscreen_desc,
                                          restrict_to_output, swap_chain_out);
    g_swapchain_allows_tearing.store(false, std::memory_order_relaxed);
  }
  if (SUCCEEDED(hr) && swap_chain_out != nullptr && *swap_chain_out != nullptr) {
    NdHookSwapChainVtable(*swap_chain_out);
  }

  if (device != nullptr) {
    ID3D12Device* d3d_dev = nullptr;
    if (FAILED(device->QueryInterface(IID_PPV_ARGS(&d3d_dev)))) {
      ID3D12DeviceChild* dev_child = nullptr;
      if (SUCCEEDED(device->QueryInterface(IID_PPV_ARGS(&dev_child))) && dev_child) {
        dev_child->GetDevice(IID_PPV_ARGS(&d3d_dev));
        dev_child->Release();
      }
    }
    if (d3d_dev != nullptr) {
      NdPrecompilePsoBundle(d3d_dev);
      d3d_dev->Release();
    }
  }

  return hr;
}

void NdInstallFactoryHook() {
  std::lock_guard<std::mutex> lock(g_factory_hook_mutex);
  if (g_factory_hook_installed) {
    return;
  }
  HMODULE dxgi = GetModuleHandleA("dxgi.dll");
  if (dxgi == nullptr) {
    dxgi = LoadLibraryA("dxgi.dll");
  }
  if (dxgi == nullptr) {
    REXLOG_ERROR("neverdead: [PRESENT-HOOK] dxgi.dll unavailable");
    return;
  }
  using CreateDXGIFactory2Fn = HRESULT(WINAPI*)(UINT, REFIID, void**);
  auto create_dxgi_factory2 =
      reinterpret_cast<CreateDXGIFactory2Fn>(GetProcAddress(dxgi, "CreateDXGIFactory2"));
  if (create_dxgi_factory2 == nullptr) {
    REXLOG_ERROR("neverdead: [PRESENT-HOOK] CreateDXGIFactory2 not exported");
    return;
  }
  IDXGIFactory2* factory = nullptr;
  HRESULT hr = create_dxgi_factory2(0, __uuidof(IDXGIFactory2),
                                    reinterpret_cast<void**>(&factory));
  if (FAILED(hr) || factory == nullptr) {
    REXLOG_ERROR("neverdead: [PRESENT-HOOK] CreateDXGIFactory2 failed (hr=0x{:08X})",
                 static_cast<unsigned>(hr));
    return;
  }
  void** vtable = *reinterpret_cast<void***>(factory);
  constexpr size_t kCreateSwapChainForHwndIndex = 15;
  void* slot = vtable[kCreateSwapChainForHwndIndex];
  if (slot == nullptr || slot == reinterpret_cast<void*>(&NdCreateSwapChainForHwndDetour)) {
    REXLOG_WARN(
        "neverdead: [PRESENT-HOOK] CreateSwapChainForHwnd slot invalid or already hooked ({:p})",
        slot);
    factory->Release();
    return;
  }
  DWORD old_protect = 0;
  if (VirtualProtect(&vtable[kCreateSwapChainForHwndIndex], sizeof(void*), PAGE_READWRITE,
                     &old_protect) == 0) {
    REXLOG_ERROR("neverdead: [PRESENT-HOOK] VirtualProtect on DXGI factory failed (err={})",
                 GetLastError());
    factory->Release();
    return;
  }
  g_real_create_swapchain_for_hwnd = reinterpret_cast<NdCreateSwapChainForHwndFn>(slot);
  vtable[kCreateSwapChainForHwndIndex] =
      reinterpret_cast<void*>(&NdCreateSwapChainForHwndDetour);
  DWORD ignored = 0;
  VirtualProtect(&vtable[kCreateSwapChainForHwndIndex], sizeof(void*), old_protect, &ignored);
  FlushInstructionCache(GetCurrentProcess(), &vtable[kCreateSwapChainForHwndIndex],
                        sizeof(void*));
  factory->Release();
  g_factory_hook_installed = true;
  REXLOG_INFO(
      "neverdead: [PRESENT-HOOK] IDXGIFactory2::CreateSwapChainForHwnd hooked (vtable[{}])",
      kCreateSwapChainForHwndIndex);
}

}  // namespace

HWND NdGetGameWindow() {
  if (g_game_hwnd != nullptr && IsWindow(g_game_hwnd)) {
    return g_game_hwnd;
  }
  g_game_hwnd = nullptr;
  const DWORD current_pid = GetCurrentProcessId();
  HWND console_hwnd = GetConsoleWindow();

  struct EnumData {
    DWORD pid;
    HWND console;
    HWND config;
    HWND found;
  } data{current_pid, console_hwnd, g_config_hwnd.load(std::memory_order_acquire), nullptr};

  EnumWindows(
      [](HWND hwnd, LPARAM lparam) -> BOOL {
        auto* d = reinterpret_cast<EnumData*>(lparam);
        if (!IsWindowVisible(hwnd)) {
          return TRUE;
        }
        if (hwnd == d->console || (d->config != nullptr && hwnd == d->config)) {
          return TRUE;
        }
        wchar_t cls[64] = {};
        if (GetClassNameW(hwnd, cls, 64) > 0 && lstrcmpiW(cls, L"NeverDeadConfigClass") == 0) {
          return TRUE;
        }
        DWORD pid = 0;
        GetWindowThreadProcessId(hwnd, &pid);
        if (pid == d->pid) {
          d->found = hwnd;
          return FALSE;
        }
        return TRUE;
      },
      reinterpret_cast<LPARAM>(&data));

  if (data.found != nullptr) {
    g_game_hwnd = data.found;
    if (g_original_game_wndproc == nullptr) {
      g_original_game_wndproc = reinterpret_cast<WNDPROC>(
          SetWindowLongPtrW(g_game_hwnd, GWLP_WNDPROC,
                            reinterpret_cast<LONG_PTR>(&NdGameSubclassWndProc)));
      SetTimer(g_game_hwnd, 0x4E44, 80, nullptr);
      REXLOG_INFO("neverdead: [WINDOW] HWND={:p} detected via EnumWindows and subclassed",
                  static_cast<void*>(g_game_hwnd));
    }
  }
  return g_game_hwnd;
}
#endif  // _WIN32

void NdInstallPresentGate() {
#ifdef _WIN32
  NdInstallFactoryHook();
#endif
}

void NdForceIntervalOneHook(PPCRegister& r11) {
  NdInitFpsMode();
  const float target = g_fps_target.load(std::memory_order_relaxed);
  const bool in_3d = g_in_3d_gameplay.load(std::memory_order_relaxed);
  const bool unlocked = g_unlock_framerate.load(std::memory_order_relaxed) && in_3d;

  if (!unlocked || target == 30.0f) {
    r11.u64 = 2;  // 30 FPS -> 2 VBlanks (33.33 ms)
  } else if (target <= 0.0f) {
    r11.u64 = 0;  // Unlimited -> immediate flip
  } else {
    r11.u64 = 1;  // 60 FPS -> 1 VBlank (16.67 ms)
  }
  NdSyncFpsClampMemory(0.0f);
}

void NdSetFpsLimitHook(PPCRegister& f0) {
  f0.f64 = 0.0;
  f0.f32 = 0.0f;
  NdSyncFpsClampMemory(0.0f);
}

extern "C" void NdOnDrawCall() {
  g_guest_draws_this_frame.fetch_add(1, std::memory_order_relaxed);
}

extern "C" bool NdShouldSkipLumaResolve() {
  if (!g_opt_luma_resolve.load(std::memory_order_relaxed)) {
    return false;
  }
  static std::atomic<uint32_t> g_luma_frame_counter{0};
  const uint32_t c = g_luma_frame_counter.fetch_add(1, std::memory_order_relaxed);
  return (c & 1) != 0;
}
