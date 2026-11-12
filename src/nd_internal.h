// Neverdeth - Internal declarations shared across src/nd_*.cpp modules.
//
// Never force-included via -include; include explicitly from Neverdeth host
// modules only.

#pragma once

#include "nd_hooks.h"

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#endif

#include <atomic>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <string>

struct ID3D12Device;

#ifndef NEVERDETH_VERSION_STRING
#define NEVERDETH_VERSION_STRING "v0.1.1-alpha"
#endif

inline std::wstring NdVersionWString() {
  const char* ver = NEVERDETH_VERSION_STRING;
  std::wstring out;
  for (const char* p = ver; *p != '\0'; ++p) {
    out.push_back(static_cast<wchar_t>(static_cast<unsigned char>(*p)));
  }
  return out;
}

// Guest memory read/write helpers (big-endian Xbox 360 layout)
inline uint8_t NdReadGuestU8(uint32_t guest_va) {
  const uint8_t* p = nd_translate_va(guest_va);
  return p ? *p : 0;
}

inline bool NdWriteGuestU8(uint32_t guest_va, uint8_t value) {
  uint8_t* p = nd_translate_va(guest_va);
  if (p == nullptr) {
    return false;
  }
  *p = value;
  return true;
}

inline uint32_t NdBe32(const uint8_t* p) {
  return (uint32_t(p[0]) << 24) | (uint32_t(p[1]) << 16) | (uint32_t(p[2]) << 8) |
         uint32_t(p[3]);
}

inline void NdStoreBe32(uint8_t* p, uint32_t val) {
  p[0] = static_cast<uint8_t>(val >> 24);
  p[1] = static_cast<uint8_t>(val >> 16);
  p[2] = static_cast<uint8_t>(val >> 8);
  p[3] = static_cast<uint8_t>(val);
}

inline uint32_t NdReadGuestBe32(uint32_t guest_va) {
  const uint8_t* p = nd_translate_va(guest_va);
  return p ? NdBe32(p) : 0;
}

inline bool NdWriteGuestBe32(uint32_t guest_va, uint32_t value) {
  uint8_t* p = nd_translate_va(guest_va);
  if (p == nullptr) {
    return false;
  }
  NdStoreBe32(p, value);
  return true;
}

inline float NdBeBitsToFloat(uint32_t be_bits) {
  float f = 0.0f;
  std::memcpy(&f, &be_bits, sizeof(f));
  return f;
}

inline uint32_t NdFloatToBeBits(float f) {
  uint32_t bits = 0;
  std::memcpy(&bits, &f, sizeof(bits));
  return bits;
}

inline uint64_t NdFnv1a64(const void* data, size_t len) {
  const uint8_t* ptr = reinterpret_cast<const uint8_t*>(data);
  uint64_t h = 0xCBF29CE484222325ULL;
  for (size_t i = 0; i < len; ++i) {
    h ^= ptr[i];
    h *= 0x100000001B3ULL;
  }
  return h;
}

// Diagnostic filter for isolating D3D12/OSD subsystems via ND_PSO_DISABLE
inline bool NdQaDisabled(const char* feature) {
  static const std::string s_list = []() {
    const char* v = std::getenv("ND_PSO_DISABLE");
    return v != nullptr ? std::string(v) : std::string();
  }();
  return !s_list.empty() && s_list.find(feature) != std::string::npos;
}

// Shared gameplay, framerate, and presentation state
extern std::atomic<bool> g_in_3d_gameplay;
extern std::atomic<bool> g_unlock_framerate;
extern std::atomic<uint32_t> g_guest_device_va;
extern std::atomic<float> g_fps_target;
extern std::atomic<bool> g_opt_luma_resolve;
extern std::atomic<uint32_t> g_guest_draws_this_frame;

extern std::atomic<uint64_t> g_vd_init_calls;
extern std::atomic<uint64_t> g_vd_swap_calls;
extern std::atomic<uint64_t> g_last_presented_swap;
extern std::atomic<uint64_t> g_frames_presented;
extern std::atomic<double> g_present_fps;
extern std::atomic<uint64_t> g_present_logged_allowed;
extern std::atomic<uint64_t> g_present_logged_skipped;
extern std::atomic<void*> g_presenter_object;

// Shared shader compilation & OSD state (unified across PSO compiler and OSD)
extern std::atomic<uint32_t> g_compiled_shader_count;
extern std::atomic<uint32_t> g_active_pso_compiles;
extern std::atomic<bool> g_show_shader_compilation;
extern std::atomic<bool> g_is_precompiling_shaders;
extern std::atomic<uint32_t> g_precompile_total;
extern std::atomic<uint64_t> g_last_shader_compile_tick_ms;

// Shared frame pacing state
extern std::atomic<bool> g_guest_throttle_active;
#ifdef _WIN32
extern LARGE_INTEGER g_qpf;
extern LARGE_INTEGER g_guest_frame_start_qpc;

// Shared window and UI state
extern std::atomic<bool> g_menu_toggle_request;
extern std::atomic<HWND> g_config_hwnd;
extern int g_window_mode;
#endif

// Runtime patches & diagnostics (nd_runtime_patches.cpp)
bool NdIsDiagEnabled();
#ifdef _WIN32
uint8_t* NdAllocNear(void* target, size_t size);
#endif
int NdReadPaintMode();
bool NdRestorePaintMode();
void NdSilenceSpamLogs();
void NdSyncVBlankRate();
void NdSyncFpsClampMemory(float target);
void NdNotifyAsuraFpsIntervalChanged();
void NdInitFpsMode();
void NdCheckFpsToggleKey();

// Frame pacer (nd_frame_pacer.cpp)
void NdEnsureTimerResolution();
void NdInitQpcOnce();
void NdApplyFramePacer();
void NdPaceGuestAfterSwapComplete(float target_fps, uint64_t swap_count);

// DXGI presentation & game window (nd_dxgi_present.cpp)
#ifdef _WIN32
HWND NdGetGameWindow();
#endif

// D3D12 PSO cache & precompilation (nd_d3d12_pso.cpp)
void NdInstallD3D12DeviceHook();
void NdPrecompilePsoBundle(ID3D12Device* device);
void NdFlushPsoBundle();
void NdSavePipelineLibraryCache();

// Shader compilation OSD (nd_shader_osd.cpp)
#ifdef _WIN32
void NdEnsureShaderOsdWindow(HWND parent_hwnd);
void NdUpdateShaderOsd(HWND game_hwnd);
void NdHideShaderOsd();
#endif

// Dark theme helpers shared with setup wizard (nd_config_ui.cpp)
#ifdef _WIN32
void NdSetDarkTitleBar(HWND hwnd);
void NdDisableTheme(HWND ctrl);
#endif

// PM4 / XDK shadow-state reset (nd_diag_pm4.cpp)
void NdResetXdkShadowState();
