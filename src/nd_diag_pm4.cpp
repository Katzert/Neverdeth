// Neverdeth - Video mid-ASM hooks, PM4 ring diagnostics, XDK shadow-state filter, and frame telemetry.

#include "nd_internal.h"

#ifdef _WIN32
#include <tlhelp32.h>
#endif

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <utility>
#include <vector>

#include <rex/logging.h>

std::atomic<uint64_t> g_vd_init_calls{0};

namespace {

bool NdRingEnabled() {
  static const bool s = []() {
    const char* v = std::getenv("ND_RING");
    return v != nullptr && v[0] == '1';
  }();
  return s;
}

bool NdXdkFilterEnabled() {
  static const bool s = []() {
    const char* v = std::getenv("ND_XDK_FILTER");
    return v != nullptr && v[0] == '1';
  }();
  return s;
}

struct NdXdkShadowState {
  uint32_t current_vs_addr{0};
  uint32_t current_vs_size{0};
  uint32_t current_ps_addr{0};
  uint32_t current_ps_size{0};

  uint32_t alu_consts[256][4]{};
  bool alu_consts_valid[256]{false};

  uint64_t total_shaders{0};
  uint64_t filtered_shaders{0};
  uint64_t total_consts{0};
  uint64_t filtered_consts{0};

  void Reset() {
    current_vs_addr = 0;
    current_vs_size = 0;
    current_ps_addr = 0;
    current_ps_size = 0;
    std::memset(alu_consts_valid, 0, sizeof(alu_consts_valid));
  }
};

NdXdkShadowState g_xdk_shadow{};

std::chrono::steady_clock::time_point g_last_swap;
bool g_have_last = false;
double g_sum_ms = 0.0;
double g_max_ms = 0.0;
uint64_t g_window_frames = 0;
uint64_t g_spike_100 = 0;
uint64_t g_spike_500 = 0;
uint64_t g_spike_1000 = 0;

double g_window_cpu_ms_sum = 0.0;
double g_window_frame_ms_sum = 0.0;
double g_window_min_frame_ms = 999999.0;
double g_window_max_frame_ms = 0.0;
uint32_t g_window_sample_count = 0;

uint32_t g_ring_ops[128]{};
uint32_t g_ring_reg_hits[4]{};
std::atomic<bool> g_resolve_trace_done{false};

#ifdef _WIN32
struct NdProcCounters {
  uint64_t read_bytes = 0;
  uint64_t write_bytes = 0;
  uint64_t read_ops = 0;
  uint64_t write_ops = 0;
  uint64_t cpu_100ns = 0;
};

NdProcCounters g_prev_counters{};
bool g_have_prev_counters = false;

NdProcCounters NdSampleCounters() {
  NdProcCounters c;
  IO_COUNTERS io{};
  if (GetProcessIoCounters(GetCurrentProcess(), &io) != 0) {
    c.read_bytes = io.ReadTransferCount;
    c.write_bytes = io.WriteTransferCount;
    c.read_ops = io.ReadOperationCount;
    c.write_ops = io.WriteOperationCount;
  }
  FILETIME create_time{};
  FILETIME exit_time{};
  FILETIME kernel_time{};
  FILETIME user_time{};
  if (GetProcessTimes(GetCurrentProcess(), &create_time, &exit_time, &kernel_time,
                      &user_time) != 0) {
    c.cpu_100ns = (uint64_t(kernel_time.dwHighDateTime) << 32 | kernel_time.dwLowDateTime) +
                  (uint64_t(user_time.dwHighDateTime) << 32 | user_time.dwLowDateTime);
  }
  return c;
}

struct NdThreadCpu {
  DWORD tid = 0;
  HANDLE handle = nullptr;
  char name[48] = {};
  uint64_t cpu_100ns = 0;
  uint64_t prev_cpu_100ns = 0;
};

std::vector<NdThreadCpu> g_threads;
std::chrono::steady_clock::time_point g_threads_last_refresh;

void NdRefreshThreads() {
  std::vector<NdThreadCpu> previous = std::move(g_threads);
  g_threads.clear();
  HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);
  if (snapshot == INVALID_HANDLE_VALUE) {
    g_threads = std::move(previous);
    return;
  }
  const DWORD pid = GetCurrentProcessId();
  THREADENTRY32 entry{};
  entry.dwSize = sizeof(entry);
  if (Thread32First(snapshot, &entry) != 0) {
    do {
      if (entry.th32OwnerProcessID != pid) {
        continue;
      }
      HANDLE handle = OpenThread(THREAD_QUERY_LIMITED_INFORMATION, FALSE, entry.th32ThreadID);
      if (handle == nullptr) {
        continue;
      }
      NdThreadCpu t;
      t.tid = entry.th32ThreadID;
      t.handle = handle;
      for (const NdThreadCpu& old : previous) {
        if (old.tid == t.tid) {
          t.cpu_100ns = old.cpu_100ns;
          t.prev_cpu_100ns = old.prev_cpu_100ns;
          std::memcpy(t.name, old.name, sizeof(t.name));
          break;
        }
      }
      if (t.name[0] == '\0') {
        PWSTR description = nullptr;
        if (SUCCEEDED(GetThreadDescription(handle, &description)) && description != nullptr) {
          WideCharToMultiByte(CP_UTF8, 0, description, -1, t.name, sizeof(t.name) - 1, nullptr,
                              nullptr);
          LocalFree(description);
        }
      }
      g_threads.push_back(t);
    } while (Thread32Next(snapshot, &entry) != 0);
  }
  CloseHandle(snapshot);
  for (NdThreadCpu& t : previous) {
    if (t.handle != nullptr) {
      CloseHandle(t.handle);
    }
  }
  for (NdThreadCpu& t : g_threads) {
    if (t.cpu_100ns == 0 && t.prev_cpu_100ns == 0) {
      FILETIME create_time{};
      FILETIME exit_time{};
      FILETIME kernel_time{};
      FILETIME user_time{};
      if (GetThreadTimes(t.handle, &create_time, &exit_time, &kernel_time, &user_time) != 0) {
        t.cpu_100ns = (uint64_t(kernel_time.dwHighDateTime) << 32 | kernel_time.dwLowDateTime) +
                      (uint64_t(user_time.dwHighDateTime) << 32 | user_time.dwLowDateTime);
        t.prev_cpu_100ns = t.cpu_100ns;
      }
    }
  }
  g_threads_last_refresh = std::chrono::steady_clock::now();
}

void NdSampleThreads() {
  for (NdThreadCpu& t : g_threads) {
    FILETIME create_time{};
    FILETIME exit_time{};
    FILETIME kernel_time{};
    FILETIME user_time{};
    if (GetThreadTimes(t.handle, &create_time, &exit_time, &kernel_time, &user_time) == 0) {
      continue;
    }
    t.prev_cpu_100ns = t.cpu_100ns;
    t.cpu_100ns = (uint64_t(kernel_time.dwHighDateTime) << 32 | kernel_time.dwLowDateTime) +
                  (uint64_t(user_time.dwHighDateTime) << 32 | user_time.dwLowDateTime);
  }
}
#endif  // _WIN32

void NdRingFlush(uint64_t swap_index) {
  char ops[220] = {};
  size_t used = 0;
  for (uint32_t op = 0; op < 128; ++op) {
    if (g_ring_ops[op] == 0) continue;
    const int w = snprintf(ops + used, sizeof(ops) - used, "%s0x%02X:%u",
                           used ? " " : "", op, g_ring_ops[op]);
    if (w <= 0 || used + size_t(w) >= sizeof(ops)) break;
    used += size_t(w);
  }
  REXLOG_INFO("neverdead: [RING] swap #{} | type-3 opcodes: {}", swap_index,
              ops[0] ? ops : "(none)");
  std::memset(g_ring_ops, 0, sizeof(g_ring_ops));
  std::memset(g_ring_reg_hits, 0, sizeof(g_ring_reg_hits));
}

void NdProbePm4Ring(uint32_t device, uint64_t swap_index) {
  const bool ring_on = NdRingEnabled();
  const bool filter_on = NdXdkFilterEnabled();
  if ((!ring_on && !filter_on) || device == 0) {
    return;
  }
  const uint8_t* dev = nd_translate_va(device);
  if (dev == nullptr) {
    return;
  }
  const uint32_t wr = NdBe32(dev + 48);
  static uint32_t s_last = 0;
  if (s_last == 0 || wr <= s_last || (wr - s_last) > (4u << 20)) {
    s_last = wr;
    return;
  }
  const uint8_t* p = nd_translate_va(s_last);
  if (p == nullptr) {
    s_last = wr;
    return;
  }
  const uint32_t dwords = (wr - s_last) >> 2;
  uint32_t i = 0;
  uint32_t draws = 0, resolves = 0, bin_mask = 0, bin_select = 0, predicated = 0;
  uint32_t swaps = 0, reg_writes = 0, surface_info = 0, modecontrol = 0;
  uint32_t color_info = 0, depth_info = 0, copy_dest_base = 0, copy_dest_info = 0;
  uint32_t draws_since_last_resolve = 0;

  std::vector<uint64_t> frame_shader_hashes;
  frame_shader_hashes.reserve(512);
  uint32_t im_load_vs = 0, im_load_ps = 0;

  const bool should_trace_resolves =
      (swap_index > 120 && !g_resolve_trace_done.load(std::memory_order_relaxed));

  while (i < dwords) {
    const uint32_t h = NdBe32(p + (i << 2));
    const uint32_t type = h >> 30;
    if (type == 3u) {
      const uint32_t opcode = (h >> 8) & 0x7F;
      const uint32_t count = ((h >> 16) & 0x3FFF) + 1;
      if (i + 1 + count > dwords) break;
      if ((h & 1u) != 0) ++predicated;
      switch (opcode) {
        case 0x22:  // PM4_DRAW_INDX
        case 0x34:  // PM4_DRAW_INDX_BIN
        case 0x35:  // PM4_DRAW_INDX_2_BIN
        case 0x36:  // PM4_DRAW_INDX_2
          ++draws;
          ++draws_since_last_resolve;
          break;
        case 0x27: {  // PM4_IM_LOAD
          if (count >= 2) {
            const uint32_t addr_type = NdBe32(p + ((i + 1) << 2));
            const uint32_t start_size = NdBe32(p + ((i + 2) << 2));
            const uint32_t addr = addr_type & ~0x3u;
            const uint32_t shader_type = addr_type & 0x3u;
            const uint32_t size_dwords = start_size & 0xFFFFu;
            if (shader_type == 0) ++im_load_vs;
            else ++im_load_ps;
            const uint8_t* code = addr ? nd_translate_va(addr) : nullptr;
            if (code != nullptr && size_dwords > 0) {
              frame_shader_hashes.push_back(NdFnv1a64(code, size_dwords * 4));
            }
            if (filter_on && addr != 0) {
              if (shader_type == 0) {
                if (g_xdk_shadow.current_vs_addr == addr &&
                    g_xdk_shadow.current_vs_size == size_dwords) {
                  uint8_t* mut_p = const_cast<uint8_t*>(p);
                  NdStoreBe32(mut_p + (i << 2), (h & ~0x00007F00u) | (0x10u << 8));
                  ++g_xdk_shadow.filtered_shaders;
                } else {
                  g_xdk_shadow.current_vs_addr = addr;
                  g_xdk_shadow.current_vs_size = size_dwords;
                  ++g_xdk_shadow.total_shaders;
                }
              } else if (shader_type == 1) {
                if (g_xdk_shadow.current_ps_addr == addr &&
                    g_xdk_shadow.current_ps_size == size_dwords) {
                  uint8_t* mut_p = const_cast<uint8_t*>(p);
                  NdStoreBe32(mut_p + (i << 2), (h & ~0x00007F00u) | (0x10u << 8));
                  ++g_xdk_shadow.filtered_shaders;
                } else {
                  g_xdk_shadow.current_ps_addr = addr;
                  g_xdk_shadow.current_ps_size = size_dwords;
                  ++g_xdk_shadow.total_shaders;
                }
              }
            }
          }
          break;
        }
        case 0x2F: {  // PM4_LOAD_ALU_CONSTANT
          if (filter_on && count >= 3) {
            const uint32_t address = NdBe32(p + ((i + 1) << 2)) & 0x3FFFFFFFu;
            const uint32_t offset_type = NdBe32(p + ((i + 2) << 2));
            const uint32_t size_dwords = NdBe32(p + ((i + 3) << 2)) & 0xFFFu;
            const uint32_t index = offset_type & 0x7FFu;
            const uint32_t alu_type = (offset_type >> 16) & 0xFFu;
            if (alu_type == 0 && (size_dwords % 4) == 0 && size_dwords > 0) {
              const uint32_t count_vec4 = size_dwords / 4;
              if (index + count_vec4 <= 256) {
                const uint8_t* src = nd_translate_va(address);
                if (src != nullptr) {
                  bool all_same = true;
                  for (uint32_t v = 0; v < count_vec4; ++v) {
                    const uint32_t c_idx = index + v;
                    if (!g_xdk_shadow.alu_consts_valid[c_idx]) {
                      all_same = false;
                      break;
                    }
                    const uint32_t w0 = NdBe32(src + ((v * 4 + 0) << 2));
                    const uint32_t w1 = NdBe32(src + ((v * 4 + 1) << 2));
                    const uint32_t w2 = NdBe32(src + ((v * 4 + 2) << 2));
                    const uint32_t w3 = NdBe32(src + ((v * 4 + 3) << 2));
                    if (g_xdk_shadow.alu_consts[c_idx][0] != w0 ||
                        g_xdk_shadow.alu_consts[c_idx][1] != w1 ||
                        g_xdk_shadow.alu_consts[c_idx][2] != w2 ||
                        g_xdk_shadow.alu_consts[c_idx][3] != w3) {
                      all_same = false;
                      break;
                    }
                  }
                  if (all_same) {
                    uint8_t* mut_p = const_cast<uint8_t*>(p);
                    NdStoreBe32(mut_p + (i << 2), (h & ~0x00007F00u) | (0x10u << 8));
                    g_xdk_shadow.filtered_consts += count_vec4;
                  } else {
                    for (uint32_t v = 0; v < count_vec4; ++v) {
                      const uint32_t c_idx = index + v;
                      g_xdk_shadow.alu_consts[c_idx][0] = NdBe32(src + ((v * 4 + 0) << 2));
                      g_xdk_shadow.alu_consts[c_idx][1] = NdBe32(src + ((v * 4 + 1) << 2));
                      g_xdk_shadow.alu_consts[c_idx][2] = NdBe32(src + ((v * 4 + 2) << 2));
                      g_xdk_shadow.alu_consts[c_idx][3] = NdBe32(src + ((v * 4 + 3) << 2));
                      g_xdk_shadow.alu_consts_valid[c_idx] = true;
                    }
                    g_xdk_shadow.total_consts += count_vec4;
                  }
                }
              }
            }
          }
          break;
        }
        case 0x50:
          ++bin_mask;
          break;
        case 0x51:
          ++bin_select;
          break;
        case 0x2A:
          ++swaps;
          break;
        default:
          break;
      }
      ++g_ring_ops[opcode];
      i += 1 + count;
    } else if (type == 0u) {
      const uint32_t count = ((h >> 16) & 0x3FFFu) + 1;
      const uint32_t base = h & 0x7FFFu;
      const bool one = ((h >> 15) & 1u) != 0;
      if (i + 1 + count > dwords) break;
      for (uint32_t m = 0; m < count; ++m) {
        const uint32_t reg = one ? base : base + m;
        const uint32_t val = NdBe32(p + ((i + 1 + m) << 2));
        ++reg_writes;
        if (reg == 0x2000) {
          surface_info = val;
          ++g_ring_reg_hits[0];
        } else if (reg == 0x2001) {
          color_info = val;
        } else if (reg == 0x2002) {
          depth_info = val;
        } else if (reg == 0x2019) {
          copy_dest_base = val;
        } else if (reg == 0x201B) {
          copy_dest_info = val;
        } else if (reg == 0x2208) {
          modecontrol = val;
          ++g_ring_reg_hits[1];
          if ((val & 7u) == 6u) {
            ++resolves;
            if (should_trace_resolves) {
              const uint32_t pitch = surface_info & 0x3FFFu;
              const uint32_t msaa = (surface_info >> 16) & 3u;
              const uint32_t edram_base = color_info & 0xFFFu;
              const uint32_t color_fmt = (color_info >> 16) & 0x0Fu;
              REXLOG_INFO(
                  "neverdead: [RESOLVE_TRACE] #{:02d} | draws_prev={} | "
                  "surface=0x{:08X} (pitch={} msaa={}) | color=0x{:08X} (base={} fmt={}) | "
                  "depth=0x{:08X} | dest_base=0x{:08X} dest_info=0x{:08X}",
                  resolves, draws_since_last_resolve, surface_info, pitch, msaa,
                  color_info, edram_base, color_fmt, depth_info, copy_dest_base, copy_dest_info);
            }
            draws_since_last_resolve = 0;
          }
        } else {
          ++g_ring_reg_hits[2];
        }
      }
      i += 1 + count;
    } else if (type == 1u) {
      if (i + 3 > dwords) break;
      i += 3;
    } else {
      ++i;
    }
  }
  s_last = wr;
  NdResetXdkShadowStateOnBoundary();

  if (draws >= 400 && resolves >= 10) {
    if (!g_in_3d_gameplay.exchange(true, std::memory_order_relaxed)) {
      REXLOG_INFO(
          "neverdead: [3D-GAMEPLAY] Enabled (draws={} resolves={} in_3d=1) -> Framerate unlocked",
          draws, resolves);
      NdNotifyAsuraFpsIntervalChanged();
    }
  }

  if (should_trace_resolves && resolves >= 10) {
    g_resolve_trace_done.store(true, std::memory_order_relaxed);
  }

  std::sort(frame_shader_hashes.begin(), frame_shader_hashes.end());
  const size_t unique_shaders =
      std::unique(frame_shader_hashes.begin(), frame_shader_hashes.end()) -
      frame_shader_hashes.begin();

  if ((swap_index % 60) == 1) {
    REXLOG_INFO(
        "neverdead: [RING] swap #{} | {} dwords | draws={} resolves={} "
        "bin_mask={} bin_select={} pred={} regs={} swaps={} | msaa={} pitch={} "
        "modecontrol=0x{:X} surface=0x{:X}",
        swap_index, dwords, draws, resolves, bin_mask, bin_select, predicated, reg_writes,
        swaps, (surface_info >> 16) & 3u, surface_info & 0x3FFFu, modecontrol, surface_info);
    if (!frame_shader_hashes.empty()) {
      REXLOG_INFO(
          "neverdead: [SHADER_STATS] swap #{} | total IM_LOAD={} (VS={} PS={}) | "
          "unique_shaders={} ({:.1f}x redundancy)",
          swap_index, frame_shader_hashes.size(), im_load_vs, im_load_ps, unique_shaders,
          double(frame_shader_hashes.size()) / std::max(size_t(1), unique_shaders));
    }
    NdRingFlush(swap_index);
  }
}

}  // namespace

void NdResetXdkShadowState() {
  g_xdk_shadow.Reset();
}

bool NdIsIn3dGameplay() {
  return g_in_3d_gameplay.load(std::memory_order_relaxed);
}

bool NdIsFpsUnlocked() {
  // Enforce the 30 FPS cap outside 3D gameplay (menus, loading screens, and Bink video)
  // to prevent audio/video decode races.
  if (!g_in_3d_gameplay.load(std::memory_order_relaxed)) {
    return false;
  }

  static const bool s_env_check = []() {
    const char* throttle = std::getenv("ND_GUEST_THROTTLE");
    const char* unlock = std::getenv("ND_UNLOCK_FPS");
    if (unlock != nullptr && unlock[0] == '1') {
      g_unlock_framerate.store(true, std::memory_order_relaxed);
    }
    if (throttle != nullptr && throttle[0] == '0') {
      g_unlock_framerate.store(true, std::memory_order_relaxed);
    }
    return true;
  }();
  (void)s_env_check;

  return g_unlock_framerate.load(std::memory_order_relaxed);
}

void NdVdSetDisplayModeHook(PPCRegister& r3) {
  const uint32_t mode = r3.u32;
  if (mode < 0x10000u) {
    return;
  }
  const uint32_t w = NdReadGuestBe32(mode + 0);
  const uint32_t h = NdReadGuestBe32(mode + 4);
  const uint32_t refresh_bits = NdReadGuestBe32(mode + 20);
  const float refresh = NdBeBitsToFloat(refresh_bits);
  static float last_refresh = -1.0f;
  if (refresh != last_refresh) {
    last_refresh = refresh;
    REXLOG_INFO("neverdead: [VIDEO] VdSetDisplayMode mode=0x{:08X} {}x{} refresh={:.1f}Hz",
                mode, w, h, refresh);
  }
}

void NdVdInitHook() {
  const uint64_t n = g_vd_init_calls.fetch_add(1, std::memory_order_relaxed) + 1;
  NdEarlyInit();
  NdPatchAsyncPipelineCache();
  if (n <= 4) {
    REXLOG_INFO("neverdead: [MIDASM] VdInitializeEngines call #{} (site 0x826BC3FC)", n);
  }
}

void NdVdSwapHook(PPCRegister& r31) {
  g_guest_device_va.store(r31.u32, std::memory_order_relaxed);
  const uint64_t n = g_vd_swap_calls.fetch_add(1, std::memory_order_relaxed) + 1;

  // Dynamic 3D gameplay detection with hysteresis on per-frame draw count.
  const uint32_t guest_draws = g_guest_draws_this_frame.exchange(0, std::memory_order_relaxed);
  static uint32_t s_low_draw_streak = 0;

  if (guest_draws >= 250) {
    s_low_draw_streak = 0;
    if (!g_in_3d_gameplay.exchange(true, std::memory_order_relaxed)) {
      REXLOG_INFO("neverdead: [3D-GAMEPLAY] Enabled (draws={} in_3d=1) -> Framerate unlocked",
                  guest_draws);
      NdNotifyAsuraFpsIntervalChanged();
    }
  } else if (guest_draws < 150) {
    if (++s_low_draw_streak >= 2) {
      if (g_in_3d_gameplay.exchange(false, std::memory_order_relaxed)) {
        REXLOG_INFO(
            "neverdead: [3D-GAMEPLAY] Disabled (draws={} streak={} in_3d=0) -> "
            "Restored 30 FPS cap for Menu/FMV",
            guest_draws, s_low_draw_streak);
        NdNotifyAsuraFpsIntervalChanged();
      }
    }
  } else {
    s_low_draw_streak = 0;
  }

  if (g_in_3d_gameplay.load(std::memory_order_relaxed)) {
    NdNotifyAsuraFpsIntervalChanged();
  }

  NdRestorePaintMode();
  const bool diag = NdIsDiagEnabled();
  if (diag && n <= 3) {
    REXLOG_INFO("neverdead: [SWAP] Enter #{} (guest thread at VdSwap)", n);
  }
  if (n <= 10) {
    NdEarlyInit();
  }
  if (n >= 2) {
    NdPatchAsyncPipelineCache();
  }

  const auto now = std::chrono::steady_clock::now();
#ifdef _WIN32
  static const bool s_enable_thread_profiling =
      NdIsDiagEnabled() && (std::getenv("ND_PROFILE_THREADS") != nullptr);
  NdProcCounters cur_counters{};
  if (s_enable_thread_profiling) {
    cur_counters = NdSampleCounters();
    if (g_threads.empty() ||
        std::chrono::duration_cast<std::chrono::seconds>(now - g_threads_last_refresh).count() >=
            2) {
      NdRefreshThreads();
    }
    NdSampleThreads();
  }
#endif

  if (g_have_last) {
    const double ms = std::chrono::duration<double, std::milli>(now - g_last_swap).count();
    g_sum_ms += ms;
    if (ms > g_max_ms) {
      g_max_ms = ms;
    }

    double frame_cpu_ms = 0.0;
#ifdef _WIN32
    LARGE_INTEGER swap_entry_qpc{};
    QueryPerformanceCounter(&swap_entry_qpc);
    if (g_guest_frame_start_qpc.QuadPart > 0 && g_qpf.QuadPart > 0) {
      frame_cpu_ms =
          static_cast<double>(swap_entry_qpc.QuadPart - g_guest_frame_start_qpc.QuadPart) *
          1000.0 / static_cast<double>(g_qpf.QuadPart);
    }
#endif
    g_window_frame_ms_sum += ms;
    g_window_cpu_ms_sum += frame_cpu_ms;
    if (ms < g_window_min_frame_ms) g_window_min_frame_ms = ms;
    if (ms > g_window_max_frame_ms) g_window_max_frame_ms = ms;
    ++g_window_sample_count;

    if (g_window_sample_count >= 60) {
      const double avg_ms = g_window_frame_ms_sum / g_window_sample_count;
      const double min_ms = g_window_min_frame_ms;
      const double max_ms = g_window_max_frame_ms;
      const double avg_cpu = g_window_cpu_ms_sum / g_window_sample_count;
      const double avg_gpu = std::max(0.0, avg_ms - avg_cpu);
      REXLOG_INFO(
          "neverdead: [FRAME_TIME] avg={:.1f} ms, min={:.1f} ms, max={:.1f} ms, "
          "cpu={:.1f} ms, gpu={:.1f} ms",
          avg_ms, min_ms, max_ms, avg_cpu, avg_gpu);

      const uint64_t sh_total = g_xdk_shadow.total_shaders + g_xdk_shadow.filtered_shaders;
      const double pct_sh = sh_total > 0 ? (100.0 * g_xdk_shadow.filtered_shaders / sh_total) : 0.0;
      const uint64_t c_total = g_xdk_shadow.total_consts + g_xdk_shadow.filtered_consts;
      const double pct_c = c_total > 0 ? (100.0 * g_xdk_shadow.filtered_consts / c_total) : 0.0;
      REXLOG_INFO(
          "neverdead: [XDK_SHADOW] filtered_shaders={} ({:.1f}%), filtered_consts={} ({:.1f}%)",
          g_xdk_shadow.filtered_shaders, pct_sh, g_xdk_shadow.filtered_consts, pct_c);

      g_window_frame_ms_sum = 0.0;
      g_window_cpu_ms_sum = 0.0;
      g_window_min_frame_ms = 999999.0;
      g_window_max_frame_ms = 0.0;
      g_window_sample_count = 0;
    }

    if (ms > 100.0) {
      ++g_spike_100;
      if (diag && n > 1) {
        fprintf(stderr, "[FRAME-SPIKE] VdSwap #%llu took %.1f ms (%.1f FPS inst)\n",
                static_cast<unsigned long long>(n), ms, 1000.0 / ms);
        fflush(stderr);
      }
    }
    if (ms > 500.0) {
      ++g_spike_500;
      if (diag) {
        REXLOG_INFO("neverdead: [FRAME] spike {:.1f} ms at frame {}", ms, n);
      }
    }
    if (ms > 1000.0) {
      ++g_spike_1000;
    }

#ifdef _WIN32
    if (ms > 200.0 && s_enable_thread_profiling) {
      const double cpu_ms =
          g_have_prev_counters
              ? double(cur_counters.cpu_100ns - g_prev_counters.cpu_100ns) / 10000.0
              : -1.0;
      const uint64_t read_delta =
          g_have_prev_counters ? cur_counters.read_bytes - g_prev_counters.read_bytes : 0;
      const uint64_t read_ops =
          g_have_prev_counters ? cur_counters.read_ops - g_prev_counters.read_ops : 0;
      char top[256] = {};
      size_t used = 0;
      {
        std::vector<std::pair<uint64_t, size_t>> deltas;
        deltas.reserve(g_threads.size());
        for (size_t i = 0; i < g_threads.size(); ++i) {
          deltas.emplace_back(g_threads[i].cpu_100ns - g_threads[i].prev_cpu_100ns, i);
        }
        const size_t wanted = deltas.size() < 4 ? deltas.size() : 4;
        std::partial_sort(deltas.begin(), deltas.begin() + wanted, deltas.end(),
                          [](const std::pair<uint64_t, size_t>& a,
                             const std::pair<uint64_t, size_t>& b) { return a.first > b.first; });
        for (size_t k = 0; k < wanted && used + 48 < sizeof(top); ++k) {
          const NdThreadCpu& t = g_threads[deltas[k].second];
          const double thread_ms = double(deltas[k].first) / 10000.0;
          if (thread_ms < 1.0) continue;
          const int written = snprintf(top + used, sizeof(top) - used, "%s[%lu]=%.0fms ",
                                       t.name[0] != '\0' ? t.name : "?",
                                       static_cast<unsigned long>(t.tid), thread_ms);
          if (written <= 0) break;
          used += size_t(written);
        }
      }
      REXLOG_INFO(
          "neverdead: [STALL] frame {} took {:.1f} ms | proc_cpu={:.1f} ms ({:.0f}%) "
          "| disk_read={:.2f} MB in {} ops | threads: {}",
          n, ms, cpu_ms, (ms > 0.0 && cpu_ms >= 0.0) ? 100.0 * cpu_ms / ms : 0.0,
          double(read_delta) / (1024.0 * 1024.0), read_ops, top);
    }
#endif
    ++g_window_frames;
  }

  g_last_swap = now;
  g_have_last = true;
#ifdef _WIN32
  g_prev_counters = cur_counters;
  g_have_prev_counters = true;
#endif

  if ((n % 300) == 0) {
    if (diag) {
      const double avg =
          g_window_frames ? (g_sum_ms / static_cast<double>(g_window_frames)) : 0.0;
      REXLOG_INFO(
          "neverdead: [FRAME] {} frames avg={:.2f} ms ({:.1f} FPS) max={:.1f} ms "
          "spikes>100={} >500={} >1000={} | dxgi_present_fps={:.1f} "
          "(allowed={} skipped={}) paint_mode={}",
          g_window_frames, avg, avg > 0.0 ? 1000.0 / avg : 0.0, g_max_ms, g_spike_100,
          g_spike_500, g_spike_1000, g_present_fps.load(std::memory_order_relaxed),
          g_present_logged_allowed.load(std::memory_order_relaxed),
          g_present_logged_skipped.load(std::memory_order_relaxed), NdReadPaintMode());
    }
    g_sum_ms = 0.0;
    g_max_ms = 0.0;
    g_window_frames = 0;
    g_spike_100 = 0;
    g_spike_500 = 0;
    g_spike_1000 = 0;
  }

  if ((n & 31) == 0) {
    NdCheckFpsToggleKey();
  }
  if (diag && n <= 3) {
    REXLOG_INFO("neverdead: [SWAP] Exit #{} (VdSwap returned)", n);
  }
  NdProbePm4Ring(r31.u32, n);
}

void NdVdSwapPostHook(PPCRegister& r31) {
  (void)r31;
  const uint64_t swap_count = g_vd_swap_calls.load(std::memory_order_relaxed);
  float target_fps = g_fps_target.load(std::memory_order_relaxed);

  if (!g_in_3d_gameplay.load(std::memory_order_relaxed) ||
      !g_unlock_framerate.load(std::memory_order_relaxed)) {
    target_fps = 30.0f;
  }

  NdPaceGuestAfterSwapComplete(target_fps, swap_count);
}
