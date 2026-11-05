// NeverDead — instrumentacion de los imports de video por mid-ASM hooks.
//
// Por que aqui y no en nd_import_diag.h:
//   El codigo generado llama directamente a `__imp__VdSwap(ctx, base)`; el
//   simbolo `__imp__*` es FUERTE en rexruntime.lib, asi que redefinirlo da
//   LNK2005. Los hooks por direccion (0x827BExxx) tampoco sirven: el arbol
//   registra los stubs en 0x827C0xxx (desfase -0x2000) y el generado no llama
//   a esos simbolos.
//
// Mecanismo nativo: [[midasm_hook]] inyecta una llamada en la instruccion
// exacta. Aqui se enganchan los dos unicos `bl` del invitado:
//   0x826BC3FC -> bl VdInitializeEngines (stub 0x827C0084)
//   0x826B89A0 -> bl VdSwap              (stub 0x827BFFD4)
// Verificado con capstone sobre el XEX descifrado (imagen offset == RVA).
#include "nd_hooks.h"
#include "nd_native_extractor.h"
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <tlhelp32.h>
#include <shlobj.h>
#include <commdlg.h>
// La puerta de Present se instala sobre la vtable de IDXGISwapChain, no sobre
// un call-site adivinado de rexruntime.dll (ver NdInstallPresentGate).
#include <dxgi.h>
#include <dxgi1_2.h>
#include <d3d12.h>
#endif
#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <mutex>
#include <utility>
#include <string>
#include <thread>
#include <vector>
#include <rex/logging.h>
// Declaracion anticipada global: NdPresentHook (dentro del namespace anonimo de
// mas abajo) aplica el pacer, pero NdApplyFramePacer se define despues.
static void NdApplyFramePacer();
static void NdNotifyAsuraFpsIntervalChanged();

static std::atomic<bool> g_hospital_reached{false};
static std::atomic<bool> g_in_3d_gameplay{false};
static std::atomic<bool> g_unlock_framerate{false};
static std::atomic<bool>& g_unlock_fps = g_unlock_framerate;
static std::atomic<uint32_t> g_guest_device_va{0};

bool NdIsIn3dGameplay() {
  return g_in_3d_gameplay.load(std::memory_order_relaxed);
}

static inline uint64_t NdFnv1a64(const void* data, size_t len) {
  const uint8_t* ptr = reinterpret_cast<const uint8_t*>(data);
  uint64_t h = 0xCBF29CE484222325ULL;
  for (size_t i = 0; i < len; ++i) {
    h ^= ptr[i];
    h *= 0x100000001B3ULL;
  }
  return h;
}

bool NdIsFpsUnlocked() {
  // Durante menus, pantallas de carga y videos Bink, el limite de 30 FPS es obligatorio
  // para evitar carreras criticas, desbordes en decodificacion y crashes en RtlLeaveCriticalSection.
  if (!g_in_3d_gameplay.load(std::memory_order_relaxed)) {
    return false;
  }

  static const bool s_env_check = []() {
    const char* throttle = std::getenv("ND_GUEST_THROTTLE");
    const char* unlock = std::getenv("ND_UNLOCK_FPS");
    if (unlock != nullptr && unlock[0] == '1') g_unlock_framerate.store(true, std::memory_order_relaxed);
    if (throttle != nullptr && throttle[0] == '0') g_unlock_framerate.store(true, std::memory_order_relaxed);
    return true;
  }();
  (void)s_env_check;

  return g_unlock_framerate.load(std::memory_order_relaxed);
}

namespace {
std::atomic<uint64_t> g_vd_init_calls{0};
std::atomic<uint64_t> g_vd_swap_calls{0};
std::atomic<uint64_t> g_last_presented_swap{0};
// Puntero al rex::ui::Presenter, capturado en SetPaintModeFromUIThread (RCX).
// Solo se usa para leer paint_mode_ (offset 0xA8, verificado desensamblando
// rexruntime.dll: `cmp dword [rcx+0xA8], 2` en 0xD69C5 y
// `mov dword [rsi+0xA8], edi` en 0xD61A8).
std::atomic<void*> g_presenter_object{nullptr};
constexpr size_t kPaintModeOffset = 0xA8;
// Telemetria DXGI Present. El overlay de NVIDIA mide las llamadas reales a
// IDXGISwapChain::Present, asi que esto es la unica metrica que vale para el
// GATE 1 (los [FRAME] de VdSwap miden el bucle del invitado, no el host).
std::atomic<uint64_t> g_present_allowed{0};
std::atomic<uint64_t> g_present_skipped{0};
std::atomic<uint64_t> g_present_total{0};
std::atomic<uint64_t> g_present_logged_allowed{0};
std::atomic<uint64_t> g_present_logged_skipped{0};
std::atomic<double> g_present_fps{0.0};
// Back-pressure invitado <-> presentador (Tarea 4, v11).
// El invitado solo puede ir UN frame por delante de lo presentado: eso es lo
// que convierte los 35.7 FPS del bucle guest en 30.0 reales sin tocar el reloj
// interno del motor (0x82B31FBC sigue siempre a 0.0f).
std::atomic<uint64_t> g_frames_submitted{0};
std::atomic<uint64_t> g_frames_presented{0};
std::atomic<uint64_t> g_throttle_waits{0};
std::atomic<uint64_t> g_throttle_timeouts{0};
std::atomic<uint32_t> g_present_thread_id{0};
std::atomic<int> g_throttle_timeout_streak{0};
std::atomic<bool> g_throttle_disabled{false};
// El invitado ya marca el ritmo (pacing post-swap): el pacer del
// presentador debe ceder, o dos pacers a 30 Hz baten entre si.
// Variables de sincronizacion 1:1 Invitado -> Presentador (Turno v12)
static std::atomic<bool> g_guest_throttle_active{false};
static std::atomic<uint64_t> g_completed_swap_seq{0};
static LARGE_INTEGER g_qpf{};
static LARGE_INTEGER g_last_guest_swap_qpc{};

static void NdEnsureTimerResolution() {
  static std::once_flag flag;
  std::call_once(flag, []() {
    HMODULE winmm = LoadLibraryA("winmm.dll");
    if (winmm == nullptr) return;
    using TimeBeginPeriodFn = UINT(WINAPI*)(UINT);
    auto p_timeBeginPeriod =
        reinterpret_cast<TimeBeginPeriodFn>(GetProcAddress(winmm, "timeBeginPeriod"));
    if (p_timeBeginPeriod != nullptr) p_timeBeginPeriod(1);
  });
}

static void NdInitQpcOnce() {
  if (g_qpf.QuadPart == 0) {
    QueryPerformanceFrequency(&g_qpf);
    QueryPerformanceCounter(&g_last_guest_swap_qpc);
    NdEnsureTimerResolution();
  }
}
std::atomic<bool> g_guest_pacing_active{false};
// Sin mutex en la ruta critica de Present: se hacia lock/unlock en CADA
// frame. La ventana de estadistica se cierra con un CAS sobre el instante de
// inicio, asi que como mucho se pierde una muestra de diagnostico.
std::atomic<int64_t> g_present_window_start_ticks{0};
std::atomic<uint64_t> g_present_window_allowed{0};
std::atomic<uint64_t> g_present_window_skipped{0};
std::atomic<bool> g_present_stack_logged{false};
// ALPHA: ND_DIAG=1 enciende toda la telemetria (contadores de proceso, muestreo
// de hilos, [FRAME]/[SWAP] y el volcado de cvars). Por defecto APAGADA: sin
// escrituras a disco ni lineas por frame en consola. Se resuelve una sola vez.
bool NdIsDiagEnabled() {
  static const bool s_diag = []() {
    const char* e = std::getenv("ND_DIAG");
    return e != nullptr && e[0] == '1' && e[1] == '\0';
  }();
  return s_diag;
}
int nd_read_paint_mode() {
  void* presenter = g_presenter_object.load(std::memory_order_acquire);
  if (presenter == nullptr) {
    return -1;
  }
  return *reinterpret_cast<volatile int32_t*>(static_cast<uint8_t*>(presenter) +
                                              kPaintModeOffset);
}
// BLINDAJE paint_mode_: si el modo cae por debajo de 2 el hilo del invitado deja
// de presentar y el juego se queda a ~0.1 FPS (el hilo UI solo repinta el mismo
// backbuffer a 200 Hz). Se restaura a 2 en los dos puntos calientes: la entrada
// de VdSwap (hilo invitado) y la entrada de PaintFromUIThread (hilo UI).
bool nd_restore_paint_mode() {
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
    REXLOG_INFO("neverdead: [PAINT-MODE] restaurado a 2 (estaba en {}) total={}", current, total);
  }
  return true;
}
// Sin lock: todos los contadores son atomicos y la ventana se reclama con
// un compare_exchange, de modo que solo un hilo la cierra y reinicia.
void nd_present_stats_tick(bool allowed, std::chrono::steady_clock::time_point now) {
  if (allowed) {
    g_present_window_allowed.fetch_add(1, std::memory_order_relaxed);
  } else {
    g_present_window_skipped.fetch_add(1, std::memory_order_relaxed);
  }
  // Diagnostico unico del objeto Presenter: confirma el offset de paint_mode_
  // y el estado de la conexion (implicit vsync, tamano de ui_drawers_).
  static std::atomic<bool> s_presenter_logged{false};
  if (!s_presenter_logged.load(std::memory_order_relaxed)) {
    void* presenter = g_presenter_object.load(std::memory_order_acquire);
    if (presenter != nullptr) {
      const uint8_t* p = static_cast<const uint8_t*>(presenter);
      REXLOG_INFO(
          "neverdead: [PAINT-MODE] presenter={:p} paint_mode_=+0x{:X}={} "
          "conn_state=+0xAC={} +0xB0={} implicit_vsync=+0xB1={} ui_drawers_size@+0x1C0={}",
          presenter, kPaintModeOffset, *reinterpret_cast<const int32_t*>(p + 0xA8),
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
    // Solo el hilo que gana el CAS cierra la ventana y reinicia los contadores.
    if (!g_present_window_start_ticks.compare_exchange_strong(start_ticks, now_ticks)) {
      return;
    }
    const uint64_t allowed_count = g_present_window_allowed.exchange(0, std::memory_order_relaxed);
    const uint64_t skipped_count = g_present_window_skipped.exchange(0, std::memory_order_relaxed);
    const double fps = double(allowed_count) / elapsed;
    g_present_fps.store(fps, std::memory_order_relaxed);
    g_present_logged_allowed.store(allowed_count, std::memory_order_relaxed);
    g_present_logged_skipped.store(skipped_count, std::memory_order_relaxed);
    // ALPHA: la linea [FRAME] de Present es pura telemetria; solo con ND_DIAG=1.
    if (NdIsDiagEnabled()) {
      REXLOG_INFO(
          "neverdead: [FRAME] dxgi_present_fps={:.1f} (allowed={} skipped={}) "
          "paint_mode={} interval={:.2f}s",
          fps, allowed_count, skipped_count, nd_read_paint_mode(), elapsed);
    }
  }
}
// Resuelve una direccion de retorno a modulo+RVA para saber QUIEN llama a
// Present (hilo UI, hilo GPU, overlay, etc.).
void nd_log_present_caller() {
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
                  static_cast<unsigned long>(address -
                                             reinterpret_cast<uintptr_t>(module)));
    } else {
      REXLOG_INFO("neverdead: [PRESENT-TRACE]   #{} 0x{:X}", i,
                  static_cast<unsigned long>(address));
    }
  }
}
// Traductor guest VA -> host (instalado por la app).
nd_translate_fn g_translate = nullptr;
// Telemetria de frame: deltas entre VdSwap consecutivos.
std::chrono::steady_clock::time_point g_last_swap;
bool g_have_last = false;
double g_sum_ms = 0.0;
double g_max_ms = 0.0;
uint64_t g_window_frames = 0;
uint64_t g_spike_100 = 0;
uint64_t g_spike_500 = 0;
uint64_t g_spike_1000 = 0;
// Clasificacion de picos: contadores del proceso para distinguir I/O de disco,
// CPU y espera. GetProcessIoCounters/GetProcessTimes son del proceso entero, no
// de un hilo, pero basta para separar las tres causas:
//   cpu_proceso ~= duracion  -> se quemo CPU (compilacion de PSO, shaders, bucle)
//   disco_leido >> 0         -> se bloqueo leyendo assets
//   cpu_proceso << duracion  -> espera pura (mutex, GPU, vblank, page faults)
struct NdProcCounters {
  uint64_t read_bytes = 0;
  uint64_t write_bytes = 0;
  uint64_t read_ops = 0;
  uint64_t write_ops = 0;
  uint64_t cpu_100ns = 0;
};
NdProcCounters g_prev_counters;
bool g_have_prev_counters = false;
NdProcCounters nd_sample_counters() {
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
// Muestreo de CPU por hilo, con el nombre que les pone el runtime (set_name en
// XHostThread: "GPU VSync", "GPU Commands", etc.). Sirve para saber QUE hilos
// queman CPU durante un pico.
struct NdThreadCpu {
  DWORD tid = 0;
  HANDLE handle = nullptr;
  char name[48] = {};
  uint64_t cpu_100ns = 0;
  uint64_t prev_cpu_100ns = 0;
};
std::vector<NdThreadCpu> g_threads;
std::chrono::steady_clock::time_point g_threads_last_refresh;
void nd_sample_threads();
void nd_refresh_threads() {
  // Conservar los tiempos de los hilos que ya conociamos (por tid): si no, en
  // cada refresco los deltas se pondrian a cero y un pico que caiga en ese
  // frame se quedaria sin atribucion.
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
  // Los hilos nuevos arrancan con prev = cpu para que su primer delta sea 0 y no
  // la CPU acumulada desde que nacieron.
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
void nd_sample_threads() {
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
// ---------------------------------------------------------------------------
// NOTA: NO hay pacer de 60 FPS en este hook, y no debe anadirse.
//
// Se probo (ver .workbuddy-ai/memory) un pacer con
// std::this_thread::sleep_until(now + 16666us) al entrar en NdVdSwapHook, como
// pedia el plan de optimizacion. Medido en este equipo:
//   sin pacer (solo vsync = true): avg = 16.67 ms = 60.0 FPS, max 17.8 ms
//   con pacer                    : avg = 17.6-17.9 ms = 55.8 FPS, max 93.7 ms
// Es decir, EMPEORA el frame pacing. Causa: la resolucion del temporizador de
// Windows por defecto es 15.6 ms, asi que sleep_until sobrepasa el objetivo y
// convierte un frame de 16.6 ms en uno de 17.9 ms (y de vez en cuando en 93 ms).
//
// Ademas es redundante: con vsync = true el worker de vblank del invitado ya
// dispara a 60 Hz exactos (graphics_system.cpp:164), y el bucle del juego se
// clava solo en 16.67 ms, como demuestran las lineas [FRAME] de la corrida 096.
// ---------------------------------------------------------------------------
}  // namespace
void nd_set_translate(nd_translate_fn fn) {
  g_translate = fn;
}
#include <dwmapi.h>
#pragma comment(lib, "dwmapi.lib")
#pragma comment(lib, "user32.lib")
#pragma comment(lib, "gdi32.lib")
#ifndef DWMWA_USE_IMMERSIVE_DARK_MODE
#define DWMWA_USE_IMMERSIVE_DARK_MODE 20
#endif

namespace {
uint8_t read_guest_u8(uint32_t guest_va) {
  if (g_translate == nullptr) {
    return 0;
  }
  const uint8_t* p = g_translate(guest_va);
  return p ? *p : 0;
}
bool write_guest_u8(uint32_t guest_va, uint8_t value) {
  if (g_translate == nullptr) {
    return false;
  }
  uint8_t* p = g_translate(guest_va);
  if (p == nullptr) {
    return false;
  }
  *p = value;
  return true;
}
uint32_t read_guest_be32(uint32_t guest_va) {
  if (g_translate == nullptr) {
    return 0;
  }
  const uint8_t* p = g_translate(guest_va);
  if (p == nullptr) {
    return 0;
  }
  return (uint32_t(p[0]) << 24) | (uint32_t(p[1]) << 16) | (uint32_t(p[2]) << 8) |
         uint32_t(p[3]);
}
bool write_guest_be32(uint32_t guest_va, uint32_t value) {
  if (g_translate == nullptr) {
    return false;
  }
  uint8_t* p = g_translate(guest_va);
  if (p == nullptr) {
    return false;
  }
  p[0] = uint8_t(value >> 24);
  p[1] = uint8_t(value >> 16);
  p[2] = uint8_t(value >> 8);
  p[3] = uint8_t(value);
  return true;
}
// Los campos del invitado son big-endian: los bits del float vienen en BE.
float be_bits_to_float(uint32_t be_bits) {
  // read_guest_be32 ya devuelve los bits IEEE en orden host. El bswap extra
  // convertia 0.5f (0x3F000000) en una denormal.
  float f = 0.0f;
  std::memcpy(&f, &be_bits, sizeof(f));
  return f;
}
uint32_t float_to_be_bits(float f) {
  // Simetrico: write_guest_be32 serializa el uint32 en big-endian.
  uint32_t bits = 0;
  std::memcpy(&bits, &f, sizeof(bits));
  return bits;
}
}  // namespace
//=============================================================================
// Trampolin ejecutable dentro de +/-2 GB del destino (para saltos rel32).
//=============================================================================
#ifdef _WIN32
namespace {
uint8_t* nd_alloc_near(void* target, size_t size) {
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
//=============================================================================
// Hook de rex::ui::Presenter::SetPaintModeFromUIThread (RVA 0xD6170)
//=============================================================================
// Evidencia (rexruntime.dll distribuida):
//   0xD6170  56 57 53             push rsi/rdi/rbx
//   0xD6173  48 83 EC 20          sub rsp, 0x20
//   0xD6177  39 91 A8 00 00 00    cmp dword [rcx+0xA8], edx   ; paint_mode_
//   0xD617D  75 08                jne 0xD6187
//   0xD617F  48 83 C4 20          add rsp,0x20; pop...; ret
//   0xD6187  89 D7                mov edi, edx                ; new_mode
//   0xD618C  48 8D 59 58          lea rbx, [rcx+0x58]         ; paint_mode_mutex_
//   0xD61A8  89 BE A8 00 00 00    mov dword [rsi+0xA8], edi
//
// El fallo del parche anterior: GetDesiredPaintModeFromUIThread (0xD6FA0) es
// una copia out-of-line que NO tiene callers directos en esta build (las
// llamadas estan inlinadas), asi que parchear su cola no cambiaba nada. El
// unico punto que escribe paint_mode_ es esta funcion; interceptarla y forzar
// new_mode=2 (conservando kNone=0) deja el Presenter en
// kGuestOutputThreadImmediately para siempre, sin tocar la logica de pintado.
//
// El stub tambien guarda RCX (this) para poder leer paint_mode_ en la
// telemetria de Present. Desactivable con ND_FORCE_GUEST_PRESENT=0.
void NdInstallSetPaintModeHook() {
  static std::atomic<bool> s_done{false};
  if (s_done.exchange(true)) {
    return;
  }
  const char* env = std::getenv("ND_FORCE_GUEST_PRESENT");
  if (env != nullptr && env[0] == '0') {
    REXLOG_INFO("neverdead: [PAINT-MODE] desactivado por ND_FORCE_GUEST_PRESENT=0");
    return;
  }
  HMODULE mod = GetModuleHandleA("rexruntime.dll");
  if (mod == nullptr) {
    REXLOG_ERROR("neverdead: [PAINT-MODE] rexruntime.dll no esta cargada");
    s_done.store(false, std::memory_order_relaxed);
    return;
  }
  uint8_t* base = reinterpret_cast<uint8_t*>(mod);
  constexpr size_t kRva = 0xD6170;
  uint8_t* entry = base + kRva;
  // push rsi / push rdi / push rbx / sub rsp,20 / cmp [rcx+A8],edx / jne
  static const uint8_t kExpected[15] = {0x56, 0x57, 0x53, 0x48, 0x83, 0xEC, 0x20, 0x39,
                                        0x91, 0xA8, 0x00, 0x00, 0x00, 0x75, 0x08};
  if ((entry[0] == 0xC7 && entry[1] == 0x41 && entry[2] == 0x18 && entry[3] == 0x00 && entry[4] == 0x00) ||
      entry[0] == 0xE9) {
    REXLOG_INFO("neverdead: [PAINT-MODE] SetPaintModeFromUIThread ya validado/parcheado (rexruntime+0x{:X})", kRva);
    return;
  }
  if (std::memcmp(entry, kExpected, sizeof(kExpected)) != 0) {
    REXLOG_ERROR(
        "neverdead: [PAINT-MODE] bytes inesperados en rexruntime+0x{:X}: "
        "{:02X} {:02X} {:02X} {:02X} {:02X}; no se instala",
        kRva, entry[0], entry[1], entry[2], entry[3], entry[4]);
    return;
  }
  uint8_t* stub = nd_alloc_near(entry, 0x1000);
  if (stub == nullptr) {
    REXLOG_ERROR("neverdead: [PAINT-MODE] no se pudo reservar el stub cerca de rexruntime");
    return;
  }
  size_t o = 0;
  auto put64 = [&](uint64_t v) {
    std::memcpy(stub + o, &v, sizeof(v));
    o += sizeof(v);
  };
  // mov rax, &g_presenter_object ; mov [rax], rcx
  stub[o++] = 0x48;
  stub[o++] = 0xB8;
  put64(reinterpret_cast<uint64_t>(&g_presenter_object));
  stub[o++] = 0x48;
  stub[o++] = 0x89;
  stub[o++] = 0x08;
  // test edx, edx ; je +2 ; mov dl, 2   (new_mode 0=kNone se conserva)
  stub[o++] = 0x85;
  stub[o++] = 0xD2;
  stub[o++] = 0x74;
  stub[o++] = 0x02;
  stub[o++] = 0xB2;
  stub[o++] = 0x02;
  // prologo original
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
    REXLOG_ERROR("neverdead: [PAINT-MODE] VirtualProtect fallo (err={})", GetLastError());
    return;
  }
  entry[0] = 0xE9;
  std::memcpy(entry + 1, &rel32, sizeof(rel32));
  DWORD ignored = 0;
  VirtualProtect(entry, 5, old_protect, &ignored);
  FlushInstructionCache(GetCurrentProcess(), entry, 5);
  REXLOG_INFO(
      "neverdead: [PAINT-MODE] SetPaintModeFromUIThread hookeado en "
      "rexruntime+0x{:X} (red de seguridad; en esta build no tiene callers "
      "directos, el modo efectivo lo fija NdPatchInlinedPaintMode), "
      "paint_mode_ en +0x{:X}",
      kRva, kPaintModeOffset);
}
//=============================================================================
// Fuerza el modo deseado 2 en la copia INLINE de GetDesiredPaintMode
//=============================================================================
// Al final de Presenter::PaintFromUIThread (RVA 0xD6C83) el compilador inlineo
// GetDesiredPaintModeFromUIThread:
//
//   0xD6C8D  41 BF 01 00 00 00   mov r15d, 1          ; kUIThreadOnRequest
//   0xD6C93  80 3D .. .. .. .. 01 cmp byte [rip+cvar], 1
//   0xD6C9A  75 18               jne 0xD6CB4
//   0xD6C9C  80 BE B1 00 00 00 00 cmp byte [rsi+0xB1], 0  ; implicit vsync
//   0xD6CA3  75 0F               jne 0xD6CB4
//   0xD6CA5  45 31 FF            xor r15d, r15d
//   0xD6CA8  48 83 BE C0 01 00 00 01 cmp qword [rsi+0x1C0], 1 ; ui_drawers_
//   0xD6CB0  41 83 D7 01         adc r15d, 1          ; 0->2, >=1->1
//   0xD6CB4  44 39 F8            cmp eax, r15d
//   ...     44 89 BE A8 00 00 00 mov [rsi+0xA8], r15d
//
// Basta con (a) cambiar el valor inicial de r15d a 2 y (b) convertir el `jne`
// de 0xD6C9A en `jmp`, saltando la comprobacion de implicit vsync y de
// ui_drawers_. El downgrade temporal a 1 durante el pintado del hilo UI
// (0xD6BB7) se conserva: es el que toma el mutex y evita que el hilo del
// invitado pinte en paralelo. Al terminar, el modo vuelve a 2 y el hilo del
// invitado (RefreshGuestOutput) presenta cada VdSwap.
void NdPatchInlinedPaintMode() {
  static std::atomic<bool> s_done{false};
  if (s_done.exchange(true)) {
    return;
  }
  const char* env = std::getenv("ND_FORCE_GUEST_PRESENT");
  if (env != nullptr && env[0] == '0') {
    REXLOG_INFO("neverdead: [PAINT-MODE] copia inline desactivada por ND_FORCE_GUEST_PRESENT=0");
    return;
  }
  HMODULE mod = GetModuleHandleA("rexruntime.dll");
  if (mod == nullptr) {
    s_done.store(false, std::memory_order_relaxed);
    return;
  }
  uint8_t* base = reinterpret_cast<uint8_t*>(mod);
  // 41 BF 01 00 00 00  ->  41 BF 02 00 00 00
  constexpr size_t kMovRva = 0xD6C8D;
  // 75 18 (jne) -> EB 18 (jmp)
  constexpr size_t kJneRva = 0xD6C9A;
  uint8_t* mov_site = base + kMovRva;
  uint8_t* jne_site = base + kJneRva;
  static const uint8_t kMovExpected[6] = {0x41, 0xBF, 0x01, 0x00, 0x00, 0x00};
  if (mov_site[2] == 0x02 || mov_site[0] == 0xA8 || mov_site[0] == 0xE9) {
    REXLOG_INFO("neverdead: [PAINT-MODE] copia inline de GetDesiredPaintMode ya validada/parcheada (rexruntime+0x{:X})", kMovRva);
    return;
  }
  if (std::memcmp(mov_site, kMovExpected, sizeof(kMovExpected)) != 0 || jne_site[0] != 0x75 ||
      jne_site[1] != 0x18) {
    REXLOG_ERROR(
        "neverdead: [PAINT-MODE] bytes inesperados en la copia inline "
        "(rexruntime+0x{:X}/0x{:X}); no se parchea",
        kMovRva, kJneRva);
    return;
  }
  DWORD old_protect = 0;
  if (VirtualProtect(mov_site, 6, PAGE_EXECUTE_READWRITE, &old_protect) == 0) {
    REXLOG_ERROR("neverdead: [PAINT-MODE] VirtualProtect fallo (err={})", GetLastError());
    return;
  }
  mov_site[2] = 0x02;
  jne_site[0] = 0xEB;
  DWORD ignored = 0;
  VirtualProtect(mov_site, 6, old_protect, &ignored);
  FlushInstructionCache(GetCurrentProcess(), mov_site, 6);
  REXLOG_INFO(
      "neverdead: [PAINT-MODE] copia inline de GetDesiredPaintMode forzada a 2 "
      "(rexruntime+0x{:X}: mov r15d,2; rexruntime+0x{:X}: jmp sin comprobar "
      "implicit_vsync/ui_drawers_)",
      kMovRva, kJneRva);
}
//=============================================================================
// Gating Temprano en PaintFromUIThread (0xD6AC0) y PaintAndPresent (0xFFC10)
//=============================================================================
// El hilo UI despierta a 200 Hz (cada 5 ms) en WaitForUITickFromUIThread.
// Si no ha habido un nuevo VdSwap del invitado desde el ultimo frame presentado,
// saltamos PaintFromUIThread y PaintAndPresent EN SU ENTRADA:
// 1. Presenter::PaintFromUIThread (0xD6AC0): retorna void de inmediato.
// 2. D3D12Presenter::PaintAndPresent (0xFFC10): retorna PaintResult::kNotPresented (0).
// Esto evita al 100% tocar el command allocator, grabar listas de comandos D3D12
// y llamar a ExecuteCommandLists 200 veces por segundo sobre el mismo backbuffer.
static std::atomic<bool> g_force_paint_requested{false};
extern "C" bool NdCheckShouldPaintUI(void* presenter, bool force_paint) {
  g_presenter_object.store(presenter, std::memory_order_release);
  // BLINDAJE: paint_mode_ DEBE ser 2 (presenta el hilo del invitado). Cualquier
  // otro valor deja al juego a ~0.1 FPS, asi que se corrige en la entrada.
  nd_restore_paint_mode();
  const uint64_t swaps = g_vd_swap_calls.load(std::memory_order_acquire);
  if (swaps > 0) {
    // Con host_present_from_non_ui_thread = true y sin menus ImGui nativos en rexruntime,
    // el hilo UI NUNCA debe pintar ni adquirir el lock del Presenter 34 veces por segundo.
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
      return true; // Frame reclamado atomicamente por este hilo GPU
    }
  }
  return false; // Ya fue reclamado por otro hilo o swaps <= last (evita repintar duplicados)
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
  static const bool s_gate_off = []() { const char* e = std::getenv("ND_NO_PAINT_GATE"); return e != nullptr && e[0] == '1'; }();
  if (s_gate_off) { REXLOG_INFO("neverdead: [PAINT-GATE] puerta temprana DESACTIVADA por ND_NO_PAINT_GATE=1"); return; }
  constexpr size_t kRva = 0xD6AC0;
  uint8_t* entry = base + kRva;
  // push r15 / push r14 / push rsi
  static const uint8_t kExpected[5] = {0x41, 0x57, 0x41, 0x56, 0x56};
  if (entry[0] == 0xE9 || (entry[0] == 0xC7 && entry[1] == 0x46 && entry[2] == 0x30)) {
    REXLOG_INFO("neverdead: [PAINT-GATE] Puerta temprana activa/validada en Presenter::PaintFromUIThread (rexruntime+0x{:X})", kRva);
    return;
  }
  if (std::memcmp(entry, kExpected, sizeof(kExpected)) != 0) {
    REXLOG_ERROR("neverdead: [PAINT-GATE] bytes inesperados en PaintFromUIThread+0x{:X}; no se instala",
                 kRva);
    return;
  }
  uint8_t* stub = nd_alloc_near(entry, 0x1000);
  if (stub == nullptr) {
    REXLOG_ERROR("neverdead: [PAINT-GATE] no se pudo reservar el stub de PaintFromUIThread");
    return;
  }
  size_t o = 0;
  auto put64 = [&](uint64_t v) {
    std::memcpy(stub + o, &v, sizeof(v));
    o += sizeof(v);
  };
  // sub rsp, 0x28
  stub[o++] = 0x48; stub[o++] = 0x83; stub[o++] = 0xEC; stub[o++] = 0x28;
  // mov [rsp+0x20], rcx
  stub[o++] = 0x48; stub[o++] = 0x89; stub[o++] = 0x4C; stub[o++] = 0x24; stub[o++] = 0x20;
  // mov [rsp+0x18], rdx
  stub[o++] = 0x48; stub[o++] = 0x89; stub[o++] = 0x54; stub[o++] = 0x24; stub[o++] = 0x18;
  // mov rax, &NdCheckShouldPaintUI
  stub[o++] = 0x48; stub[o++] = 0xB8;
  put64(reinterpret_cast<uint64_t>(&NdCheckShouldPaintUI));
  // call rax
  stub[o++] = 0xFF; stub[o++] = 0xD0;
  // mov rdx, [rsp+0x18]
  stub[o++] = 0x48; stub[o++] = 0x8B; stub[o++] = 0x54; stub[o++] = 0x24; stub[o++] = 0x18;
  // mov rcx, [rsp+0x20]
  stub[o++] = 0x48; stub[o++] = 0x8B; stub[o++] = 0x4C; stub[o++] = 0x24; stub[o++] = 0x20;
  // add rsp, 0x28
  stub[o++] = 0x48; stub[o++] = 0x83; stub[o++] = 0xC4; stub[o++] = 0x28;
  // test al, al (tras add rsp para preservar Zero Flag!)
  stub[o++] = 0x84; stub[o++] = 0xC0;
  // jnz +1 (to stolen prologue)
  stub[o++] = 0x75; stub[o++] = 0x01;
  // ret (early exit!)
  stub[o++] = 0xC3;
  // Stolen 5 bytes:
  stub[o++] = 0x41; stub[o++] = 0x57; // push r15
  stub[o++] = 0x41; stub[o++] = 0x56; // push r14
  stub[o++] = 0x56;                   // push rsi
  // mov rax, entry + 5 ; jmp rax
  stub[o++] = 0x48; stub[o++] = 0xB8;
  put64(reinterpret_cast<uint64_t>(entry + 5));
  stub[o++] = 0xFF; stub[o++] = 0xE0;
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
  REXLOG_INFO("neverdead: [PAINT-GATE] Puerta temprana activa en Presenter::PaintFromUIThread (rexruntime+0x{:X})",
              kRva);
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
  static const bool s_gate_off2 = []() { const char* e = std::getenv("ND_NO_PAINT_GATE"); return e != nullptr && e[0] == '1'; }();
  if (s_gate_off2) { REXLOG_INFO("neverdead: [PAINT-GATE] puerta de prologo DESACTIVADA por ND_NO_PAINT_GATE=1"); return; }
  constexpr size_t kRva = 0xFFC10;
  uint8_t* entry = base + kRva;
  // Stolen 12 bytes: 55 41 57 41 56 41 55 41 54 56 57 53
  // (push rbp / push r15 / push r14 / push r13 / push r12 / push rsi / push rdi / push rbx)
  static const uint8_t kExpected[12] = {
      0x55, 0x41, 0x57, 0x41, 0x56, 0x41, 0x55, 0x41, 0x54, 0x56, 0x57, 0x53};
  if (entry[0] == 0xE9 || (entry[0] == 0x48 && entry[1] == 0x89 && entry[2] == 0xD0)) {
    REXLOG_INFO("neverdead: [PAINT-GATE] Puerta de prologo ya activa/optimizada en D3D12Presenter::PaintAndPresent (rexruntime+0x{:X})", kRva);
    return;
  }
  if (std::memcmp(entry, kExpected, sizeof(kExpected)) != 0) {
    REXLOG_ERROR(
        "neverdead: [PAINT-GATE] bytes inesperados en D3D12Presenter::PaintAndPresent+0x{:X}; no se instala",
        kRva);
    return;
  }
  uint8_t* stub = nd_alloc_near(entry, 0x1000);
  if (stub == nullptr) {
    REXLOG_ERROR("neverdead: [PAINT-GATE] no se pudo reservar stub cerca de PaintAndPresent");
    return;
  }
  size_t o = 0;
  auto put64 = [&](uint64_t v) {
    std::memcpy(stub + o, &v, sizeof(v));
    o += sizeof(v);
  };
  // sub rsp, 0x28
  stub[o++] = 0x48; stub[o++] = 0x83; stub[o++] = 0xEC; stub[o++] = 0x28;
  // mov [rsp+0x20], rcx
  stub[o++] = 0x48; stub[o++] = 0x89; stub[o++] = 0x4C; stub[o++] = 0x24; stub[o++] = 0x20;
  // mov [rsp+0x18], rdx
  stub[o++] = 0x48; stub[o++] = 0x89; stub[o++] = 0x54; stub[o++] = 0x24; stub[o++] = 0x18;
  // mov rax, &NdCheckShouldPaintAndPresent
  stub[o++] = 0x48; stub[o++] = 0xB8;
  put64(reinterpret_cast<uint64_t>(&NdCheckShouldPaintAndPresent));
  // call rax
  stub[o++] = 0xFF; stub[o++] = 0xD0;
  // mov rdx, [rsp+0x18]
  stub[o++] = 0x48; stub[o++] = 0x8B; stub[o++] = 0x54; stub[o++] = 0x24; stub[o++] = 0x18;
  // mov rcx, [rsp+0x20]
  stub[o++] = 0x48; stub[o++] = 0x8B; stub[o++] = 0x4C; stub[o++] = 0x24; stub[o++] = 0x20;
  // add rsp, 0x28
  stub[o++] = 0x48; stub[o++] = 0x83; stub[o++] = 0xC4; stub[o++] = 0x28;
  // test al, al (tras add rsp para preservar Zero Flag!)
  stub[o++] = 0x84; stub[o++] = 0xC0;
  // jnz +3
  stub[o++] = 0x75; stub[o++] = 0x03;
  // xor eax, eax ; ret (PaintResult::kNotPresented = 0)
  stub[o++] = 0x31; stub[o++] = 0xC0;
  stub[o++] = 0xC3;
  // Stolen 12 bytes:
  for (size_t i = 0; i < sizeof(kExpected); ++i) {
    stub[o++] = kExpected[i];
  }
  // mov rax, entry + 12 ; jmp rax
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
  REXLOG_INFO(
      "neverdead: [PAINT-GATE] Puerta de prologo activa en D3D12Presenter::PaintAndPresent "
      "(rexruntime+0x{:X}) -> kNotPresented si no hay nuevo frame",
      kRva);
}
//=============================================================================
// Fuerza paint_mode_=2 al VOLVER de PaintFromUIThread (RVA 0xDE951)
//=============================================================================
// El wrapper que llama a PaintFromUIThread (Window::Paint..., RVA 0xDE920)
// tiene este epilogo:
//   0xDE94C  call 0xD6AC0                 ; Presenter::PaintFromUIThread
//   0xDE951  48 89 F1                   mov rcx, rsi
//   0xDE954  C6 81 C0 00 00 00 00       mov byte [rcx+0xC0], 0
//   0xDE95B  48 83 C4 20                add rsp,0x20
//   0xDE95F  5E                         pop rsi
//   0xDE960  C3                         ret
// `rsi` es la Window y [rsi+0xB0] el puntero al Presenter (cargado como dword
// en 0xDE940). Sustituimos ese epilogo por un stub que fuerza [presenter+0xA8]
// = 2 antes de restaurar el flag de pintado. Asi, entre WM_PAINT y WM_PAINT el
// modo queda en 2 y el early-return de NdInstallPaintFromUIThreadHook puede
// saltarse los paints redundantes; el hilo del invitado presenta cada VdSwap.
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
    REXLOG_INFO("neverdead: [PAINT-MODE] epilogo de Paint ya validado/parcheado (rexruntime+0x{:X})", kRva);
    return;
  }
  if (std::memcmp(site, kExpected, sizeof(kExpected)) != 0) {
    REXLOG_ERROR("neverdead: [PAINT-MODE] bytes inesperados en el epilogo de Paint (rexruntime+0x{:X}); no se instala",
                 kRva);
    return;
  }
  uint8_t* stub = nd_alloc_near(site, 0x1000);
  if (stub == nullptr) {
    REXLOG_ERROR("neverdead: [PAINT-MODE] no se pudo reservar el stub del epilogo de Paint");
    return;
  }
  size_t o = 0;
  // mov rax, [rsi+0xB0] (64-bit presenter pointer!) ; mov dword [rax+0xA8], 2 ; mov rcx,rsi
  // mov byte [rcx+0xC0],0 ; add rsp,0x20 ; pop rsi ; ret
  static const uint8_t kStub[] = {
      0x48, 0x8B, 0x86, 0xB0, 0x00, 0x00, 0x00,
      0xC7, 0x80, 0xA8, 0x00, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00,
      0x48, 0x89, 0xF1,
      0xC6, 0x81, 0xC0, 0x00, 0x00, 0x00, 0x00,
      0x48, 0x83, 0xC4, 0x20,
      0x5E,
      0xC3};
  std::memcpy(stub, kStub, sizeof(kStub));
  o = sizeof(kStub);
  FlushInstructionCache(GetCurrentProcess(), stub, o);
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
  REXLOG_INFO("neverdead: [PAINT-MODE] epilogo de PaintFromUIThread hookeado (rexruntime+0x{:X}): paint_mode_=2 tras cada paint",
              kRva);
}
//=============================================================================
// Hook de IDXGISwapChain::Present sobre la vtable de DXGI
//=============================================================================
// El parche anterior en rexruntime+0x586A92 NO era Present: los bytes eran
//   mov rcx,[r13+0x18] ; mov rax,[rcx] ; call [rax+0x40]
// dentro de un bucle que recorre objetos y llama a [obj+0x20] + Release; el
// offset 0x40 de ID3D12Fence es SetEventOnCompletion(Value, hEvent), no el
// Present del swapchain. Instalarlo no cambio el overlay (190 FPS) y ademas
// habria roto la sincronizacion de fences. Aqui se intercepta el metodo real:
// toda swapchain creada por IDXGIFactory2::CreateSwapChainForHwnd pasa por el
// detour y su vtable[8] (Present) se sustituye por NdPresentHook.
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
NdPresentFn nd_find_original_present(void* swap_chain) {
  void** vtable = *reinterpret_cast<void***>(swap_chain);
  std::lock_guard<std::mutex> lock(g_swapchain_vtable_mutex);
  for (size_t i = 0; i < g_swapchain_vtable_count; ++i) {
    if (g_swapchain_vtables[i].vtable == vtable) {
      return g_swapchain_vtables[i].original_present;
    }
  }
  return nullptr;
}

extern "C" HRESULT STDMETHODCALLTYPE NdPresentHook(IDXGISwapChain* swap_chain,
                                                    UINT sync_interval, UINT flags) {
  if (swap_chain == nullptr) {
    return E_POINTER;
  }
  NdPresentFn real_present = nd_find_original_present(swap_chain);
  if (real_present == nullptr) {
    REXLOG_ERROR("neverdead: [PRESENT-HOOK] Present sin original registrado; se deja pasar");
    return E_FAIL;
  }
  nd_log_present_caller();
  const auto now = std::chrono::steady_clock::now();
  // En DXGI_SWAP_EFFECT_FLIP_DISCARD es critico llamar siempre a real_present()
  // si el renderizado ya fue ejecutado, garantizando el avance del indice de backbuffer.

  g_present_total.fetch_add(1, std::memory_order_relaxed);
  g_present_allowed.fetch_add(1, std::memory_order_relaxed);
  g_present_thread_id.store(GetCurrentThreadId(), std::memory_order_relaxed);
  nd_present_stats_tick(true, now);
  // Pacer del host en el hilo que presenta (no en el hilo del invitado).
  // Solo se aplica si el framerate NO esta desbloqueado y el invitado no esta aplicando pacer.
  if (!NdIsFpsUnlocked() && !g_guest_throttle_active.load(std::memory_order_acquire)) {
    NdApplyFramePacer();
  }
  // Presentacion inmediata sin bloqueo de VBlank fisico (igual que Xenia Canary vsync = false)
  const UINT present_sync = 0;
  const UINT present_flags = flags | DXGI_PRESENT_ALLOW_TEARING;
  const HRESULT present_result = real_present(swap_chain, present_sync, present_flags);
  if (SUCCEEDED(present_result)) {
    // Contador de presents reales para diagnostico (guest vs presentador).
    // Solo cuenta un present real: si DXGI falla, el frame no se muestra.
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
    REXLOG_ERROR("neverdead: [PRESENT-HOOK] VirtualProtect de vtable[8] fallo (err={})",
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
      "neverdead: [PRESENT-HOOK] vtable[8] (Present) hookeada: swap_chain={:p} "
      "original={:p}",
      static_cast<void*>(swap_chain), reinterpret_cast<void*>(original));
}
static HWND g_game_hwnd = nullptr;
static HWND g_config_hwnd = nullptr;
// Peticion de toggle del menu F10: la ventana del juego solo la marca; el timer
// de 16 ms de la ventana del menu es el unico que ejecuta el toggle, asi no se
// puede togglear dos veces cuando el foco esta en el juego.
static std::atomic<bool> g_menu_toggle_request{false};
static WNDPROC g_original_game_wndproc = nullptr;

LRESULT CALLBACK NdGameSubclassWndProc(HWND hwnd, UINT msg, WPARAM wParam, LPARAM lParam) {
  if ((msg == WM_KEYDOWN || msg == WM_SYSKEYDOWN) && wParam == VK_F10) {
    // Interceptar F10 y evitar menu de sistema en F10
    if (g_config_hwnd == nullptr || !IsWindow(g_config_hwnd)) {
      NdOpenConfigUI();
    } else {
      g_menu_toggle_request.store(true, std::memory_order_release);
    }
    return 0;
  }
  if (msg == WM_CLOSE || msg == WM_DESTROY) {
    REXLOG_INFO("neverdead: [CLEAN-EXIT] WM_CLOSE/WM_DESTROY interceptado en HWND {:p}; cerrando proceso limpiamente...",
                static_cast<void*>(hwnd));
    TerminateProcess(GetCurrentProcess(), 0);
    return 0;
  }
  if (g_original_game_wndproc != nullptr) {
    return CallWindowProcW(g_original_game_wndproc, hwnd, msg, wParam, lParam);
  }
  return DefWindowProcW(hwnd, msg, wParam, lParam);
}

static HWND NdGetGameWindow() {
  if (g_game_hwnd != nullptr && IsWindow(g_game_hwnd)) {
    return g_game_hwnd;
  }
  g_game_hwnd = nullptr;
  DWORD current_pid = GetCurrentProcessId();
  HWND console_hwnd = GetConsoleWindow();

  struct EnumData {
    DWORD pid;
    HWND console;
    HWND config;
    HWND found;
  } data{current_pid, console_hwnd, g_config_hwnd, nullptr};

  EnumWindows([](HWND hwnd, LPARAM lparam) -> BOOL {
    auto* d = reinterpret_cast<EnumData*>(lparam);
    if (!IsWindowVisible(hwnd)) {
      return TRUE;
    }
    if (hwnd == d->console || hwnd == d->config) {
      return TRUE;
    }
    // Nunca subclassar la ventana del menu F10 ni ninguna de su clase: si se
    // engancha el dialogo, los cambios de modo de ventana se aplican a el.
    if (d->config != nullptr && hwnd == d->config) {
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
  }, reinterpret_cast<LPARAM>(&data));

  if (data.found != nullptr) {
    g_game_hwnd = data.found;
    if (g_original_game_wndproc == nullptr) {
      g_original_game_wndproc = reinterpret_cast<WNDPROC>(
          SetWindowLongPtrW(g_game_hwnd, GWLP_WNDPROC,
                            reinterpret_cast<LONG_PTR>(&NdGameSubclassWndProc)));
      REXLOG_INFO("neverdead: [WINDOW] HWND={:p} detectado via EnumWindows y subclassed con exito",
                  static_cast<void*>(g_game_hwnd));
    }
  }
  return g_game_hwnd;
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
    REXLOG_INFO("neverdead: [WINDOW] HWND={:p} subclassed para interceptar F10 y WM_CLOSE limpia",
                static_cast<void*>(hwnd));
  }
  HRESULT hr = g_real_create_swapchain_for_hwnd(factory, device, hwnd, desc, fullscreen_desc,
                                                restrict_to_output, swap_chain_out);
  if (SUCCEEDED(hr) && swap_chain_out != nullptr && *swap_chain_out != nullptr) {
    NdHookSwapChainVtable(*swap_chain_out);
  }
  return hr;
}
void nd_install_factory_hook() {
  std::lock_guard<std::mutex> lock(g_factory_hook_mutex);
  if (g_factory_hook_installed) {
    return;
  }
  HMODULE dxgi = GetModuleHandleA("dxgi.dll");
  if (dxgi == nullptr) {
    dxgi = LoadLibraryA("dxgi.dll");
  }
  if (dxgi == nullptr) {
    REXLOG_ERROR("neverdead: [PRESENT-HOOK] dxgi.dll no disponible");
    return;
  }
  using CreateDXGIFactory2Fn = HRESULT(WINAPI*)(UINT, REFIID, void**);
  auto create_dxgi_factory2 =
      reinterpret_cast<CreateDXGIFactory2Fn>(GetProcAddress(dxgi, "CreateDXGIFactory2"));
  if (create_dxgi_factory2 == nullptr) {
    REXLOG_ERROR("neverdead: [PRESENT-HOOK] CreateDXGIFactory2 no exportado");
    return;
  }
  IDXGIFactory2* factory = nullptr;
  HRESULT hr = create_dxgi_factory2(0, __uuidof(IDXGIFactory2),
                                    reinterpret_cast<void**>(&factory));
  if (FAILED(hr) || factory == nullptr) {
    REXLOG_ERROR("neverdead: [PRESENT-HOOK] CreateDXGIFactory2 fallo (hr=0x{:08X})",
                 static_cast<unsigned>(hr));
    return;
  }
  void** vtable = *reinterpret_cast<void***>(factory);
  constexpr size_t kCreateSwapChainForHwndIndex = 15;
  void* slot = vtable[kCreateSwapChainForHwndIndex];
  if (slot == nullptr || slot == reinterpret_cast<void*>(&NdCreateSwapChainForHwndDetour)) {
    REXLOG_WARN(
        "neverdead: [PRESENT-HOOK] slot CreateSwapChainForHwnd invalido o ya hookeado ({:p})",
        slot);
    factory->Release();
    return;
  }
  DWORD old_protect = 0;
  if (VirtualProtect(&vtable[kCreateSwapChainForHwndIndex], sizeof(void*), PAGE_READWRITE,
                     &old_protect) == 0) {
    REXLOG_ERROR("neverdead: [PRESENT-HOOK] VirtualProtect de la factory fallo (err={})",
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
      "neverdead: [PRESENT-HOOK] IDXGIFactory2::CreateSwapChainForHwnd hookeado "
      "(vtable[{}]) en dxgi.dll; Present se interceptara al crear el swap chain",
      kCreateSwapChainForHwndIndex);
}

//=============================================================================
// Intercepcion D3D12: ID3D12Device::CreateGraphicsPipelineState (vtable[10])
//=============================================================================
using NdCreateGraphicsPipelineStateFn = HRESULT(STDMETHODCALLTYPE*)(
    ID3D12Device* This,
    const D3D12_GRAPHICS_PIPELINE_STATE_DESC* pDesc,
    REFIID riid,
    void** ppPipelineState);

static NdCreateGraphicsPipelineStateFn g_real_create_graphics_pipeline_state = nullptr;

static std::mutex g_d3d12_pso_mtx;
static std::unordered_map<uint64_t, ID3D12PipelineState*> g_d3d12_pso_map;
static ID3D12PipelineLibrary1* g_d3d12_pipeline_library = nullptr;
static std::atomic<uint32_t> g_compiled_shader_count{0};
static std::atomic<uint64_t> g_last_shader_compile_tick{0};
static std::atomic<bool> g_show_shader_compilation{true};

static void NdPumpWindowMessages() {
  HWND hwnd = g_game_hwnd;
  if (hwnd && IsWindow(hwnd)) {
    MSG msg;
    while (PeekMessageW(&msg, hwnd, 0, 0, PM_REMOVE)) {
      TranslateMessage(&msg);
      DispatchMessageW(&msg);
    }
  }
}

//=============================================================================
// OSD de Compilacion de Shaders (Esquina superior izquierda)
//=============================================================================
static HWND g_osd_hwnd = nullptr;

static LRESULT CALLBACK NdShaderOsdWndProc(HWND hwnd, UINT msg, WPARAM wParam, LPARAM lParam) {
  switch (msg) {
    case WM_PAINT: {
      PAINTSTRUCT ps;
      HDC hdc = BeginPaint(hwnd, &ps);
      RECT rc;
      GetClientRect(hwnd, &rc);

      HBRUSH bg = CreateSolidBrush(RGB(18, 18, 24));
      HBRUSH old_b = static_cast<HBRUSH>(SelectObject(hdc, bg));
      HPEN pen = CreatePen(PS_SOLID, 1, RGB(70, 70, 85));
      HPEN old_p = static_cast<HPEN>(SelectObject(hdc, pen));
      RoundRect(hdc, rc.left, rc.top, rc.right, rc.bottom, 8, 8);
      SelectObject(hdc, old_p);
      SelectObject(hdc, old_b);
      DeleteObject(pen);
      DeleteObject(bg);

      uint32_t count = g_compiled_shader_count.load(std::memory_order_relaxed);
      wchar_t text[64];
      swprintf_s(text, L"Compilando shaders %u", count);

      SetBkMode(hdc, TRANSPARENT);
      SetTextColor(hdc, RGB(240, 240, 245));
      HFONT hFont = CreateFontW(15, 0, 0, 0, FW_SEMIBOLD, FALSE, FALSE, FALSE, DEFAULT_CHARSET,
                                OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
                                DEFAULT_PITCH | FF_DONTCARE, L"Segoe UI");
      HFONT old_f = static_cast<HFONT>(SelectObject(hdc, hFont));
      RECT tr = rc;
      tr.left += 12;
      DrawTextW(hdc, text, -1, &tr, DT_LEFT | DT_VCENTER | DT_SINGLELINE);
      SelectObject(hdc, old_f);
      DeleteObject(hFont);

      EndPaint(hwnd, &ps);
      return 0;
    }
    case WM_TIMER: {
      if (wParam == 2) {
        if (!g_show_shader_compilation.load(std::memory_order_relaxed)) {
          if (IsWindowVisible(hwnd)) ShowWindow(hwnd, SW_HIDE);
          return 0;
        }
        HWND ghwnd = NdGetGameWindow();
        if (ghwnd && IsWindow(ghwnd) && IsWindowVisible(ghwnd)) {
          uint64_t now = GetTickCount64();
          uint64_t last = g_last_shader_compile_tick.load(std::memory_order_relaxed);
          if (now >= last && (now - last) <= 2000 && g_compiled_shader_count.load() > 0) {
            POINT pt{16, 16};
            ClientToScreen(ghwnd, &pt);
            SetWindowPos(hwnd, HWND_TOPMOST, pt.x, pt.y, 220, 32, SWP_NOACTIVATE | SWP_SHOWWINDOW);
            InvalidateRect(hwnd, nullptr, TRUE);
          } else {
            if (IsWindowVisible(hwnd)) {
              ShowWindow(hwnd, SW_HIDE);
            }
          }
        } else {
          if (IsWindowVisible(hwnd)) ShowWindow(hwnd, SW_HIDE);
        }
      }
      return 0;
    }
    case WM_ERASEBKGND:
      return 1;
    default:
      break;
  }
  return DefWindowProcW(hwnd, msg, wParam, lParam);
}

static void NdEnsureShaderOsdWindow() {
  static std::mutex s_osd_mtx;
  std::lock_guard<std::mutex> lk(s_osd_mtx);
  if (g_osd_hwnd && IsWindow(g_osd_hwnd)) return;

  HINSTANCE hInst = GetModuleHandleW(nullptr);
  WNDCLASSEXW wc{};
  wc.cbSize = sizeof(wc);
  wc.style = CS_HREDRAW | CS_VREDRAW;
  wc.lpfnWndProc = NdShaderOsdWndProc;
  wc.hInstance = hInst;
  wc.hCursor = LoadCursor(nullptr, IDC_ARROW);
  wc.hbrBackground = nullptr;
  wc.lpszClassName = L"NeverDeadShaderOsdClass";
  RegisterClassExW(&wc);

  HWND ghwnd = NdGetGameWindow();
  g_osd_hwnd = CreateWindowExW(
      WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE,
      L"NeverDeadShaderOsdClass", L"",
      WS_POPUP, 0, 0, 220, 32,
      ghwnd, nullptr, hInst, nullptr);

  if (g_osd_hwnd) {
    SetLayeredWindowAttributes(g_osd_hwnd, 0, 220, LWA_ALPHA);
    SetTimer(g_osd_hwnd, 2, 50, nullptr);
  }
}

extern "C" HRESULT STDMETHODCALLTYPE NdCreateGraphicsPipelineStateDetour(
    ID3D12Device* This,
    const D3D12_GRAPHICS_PIPELINE_STATE_DESC* pDesc,
    REFIID riid,
    void** ppPipelineState) {

  if (!pDesc || !ppPipelineState || !g_real_create_graphics_pipeline_state) {
    if (g_real_create_graphics_pipeline_state) {
      return g_real_create_graphics_pipeline_state(This, pDesc, riid, ppPipelineState);
    }
    return E_FAIL;
  }

  uint64_t hash = NdFnv1a64(pDesc, sizeof(D3D12_GRAPHICS_PIPELINE_STATE_DESC));
  if (pDesc->VS.pShaderBytecode && pDesc->VS.BytecodeLength) {
    hash ^= NdFnv1a64(pDesc->VS.pShaderBytecode, pDesc->VS.BytecodeLength);
  }
  if (pDesc->PS.pShaderBytecode && pDesc->PS.BytecodeLength) {
    hash ^= NdFnv1a64(pDesc->PS.pShaderBytecode, pDesc->PS.BytecodeLength);
  }

  // 1. Consulta rapida en cache en memoria
  {
    std::lock_guard<std::mutex> lk(g_d3d12_pso_mtx);
    auto it = g_d3d12_pso_map.find(hash);
    if (it != g_d3d12_pso_map.end() && it->second != nullptr) {
      it->second->AddRef();
      *ppPipelineState = it->second;
      return S_OK;
    }
  }

  wchar_t pso_name[64];
  swprintf_s(pso_name, L"PSO_%016llX", hash);

  // 2. Inicializacion bajo demanda de ID3D12PipelineLibrary1 desde cache/d3d12_pipeline_cache.bin
  {
    std::lock_guard<std::mutex> lk(g_d3d12_pso_mtx);
    if (!g_d3d12_pipeline_library && This) {
      ID3D12Device1* dev1 = nullptr;
      if (SUCCEEDED(This->QueryInterface(IID_PPV_ARGS(&dev1))) && dev1) {
        std::vector<uint8_t> init_blob;
        std::filesystem::path pso_path = "cache/d3d12_pipeline_cache.bin";
        std::error_code ec;
        if (std::filesystem::exists(pso_path, ec)) {
          std::ifstream in(pso_path, std::ios::binary | std::ios::ate);
          if (in.is_open()) {
            size_t sz = static_cast<size_t>(in.tellg());
            if (sz > 0) {
              init_blob.resize(sz);
              in.seekg(0, std::ios::beg);
              in.read(reinterpret_cast<char*>(init_blob.data()), sz);
            }
          }
        }
        HRESULT hr_lib = E_FAIL;
        if (!init_blob.empty()) {
          hr_lib = dev1->CreatePipelineLibrary(
              init_blob.data(), init_blob.size(),
              IID_PPV_ARGS(&g_d3d12_pipeline_library));
        }
        if (FAILED(hr_lib) || !g_d3d12_pipeline_library) {
          dev1->CreatePipelineLibrary(
              nullptr, 0,
              IID_PPV_ARGS(&g_d3d12_pipeline_library));
        }
        dev1->Release();
      }
    }

    // 3. Carga desde PipelineLibrary si ya existe
    if (g_d3d12_pipeline_library) {
      ID3D12PipelineState* lib_pso = nullptr;
      HRESULT hr_lib = g_d3d12_pipeline_library->LoadGraphicsPipeline(
          pso_name, pDesc, riid, reinterpret_cast<void**>(&lib_pso));
      if (SUCCEEDED(hr_lib) && lib_pso) {
        g_d3d12_pso_map[hash] = lib_pso;
        *ppPipelineState = lib_pso;
        return S_OK;
      }
    }
  }

  // 4. Compilacion sincronica directa para garantizar que NUNCA devuelva nullptr o E_PENDING
  HRESULT hr = g_real_create_graphics_pipeline_state(This, pDesc, riid, ppPipelineState);
  if (SUCCEEDED(hr) && ppPipelineState && *ppPipelineState) {
    auto* compiled_pso = reinterpret_cast<ID3D12PipelineState*>(*ppPipelineState);
    compiled_pso->AddRef();

    {
      std::lock_guard<std::mutex> lk(g_d3d12_pso_mtx);
      g_d3d12_pso_map[hash] = compiled_pso;
      if (g_d3d12_pipeline_library) {
        g_d3d12_pipeline_library->StorePipeline(pso_name, compiled_pso);
        SIZE_T ser_sz = g_d3d12_pipeline_library->GetSerializedSize();
        if (ser_sz > 0) {
          std::vector<uint8_t> ser_data(ser_sz);
          if (SUCCEEDED(g_d3d12_pipeline_library->Serialize(ser_data.data(), ser_sz))) {
            std::error_code ec;
            std::filesystem::create_directories("cache", ec);
            std::ofstream out("cache/d3d12_pipeline_cache.bin", std::ios::binary | std::ios::trunc);
            if (out.is_open()) {
              out.write(reinterpret_cast<const char*>(ser_data.data()), ser_sz);
            }
          }
        }
      }
    }

    g_compiled_shader_count.fetch_add(1, std::memory_order_relaxed);
    g_last_shader_compile_tick.store(GetTickCount64(), std::memory_order_relaxed);
    NdEnsureShaderOsdWindow();
    NdPumpWindowMessages();
  }
  return hr;
}

void nd_install_d3d12_device_hook() {
  static std::atomic<bool> s_installed{false};
  if (s_installed.exchange(true)) return;

  HMODULE d3d12_mod = GetModuleHandleA("d3d12.dll");
  if (!d3d12_mod) {
    d3d12_mod = LoadLibraryA("d3d12.dll");
  }
  if (!d3d12_mod) {
    return;
  }
  using D3D12CreateDeviceFn = HRESULT(WINAPI*)(IUnknown*, D3D_FEATURE_LEVEL, REFIID, void**);
  auto pfnCreateDevice = reinterpret_cast<D3D12CreateDeviceFn>(GetProcAddress(d3d12_mod, "D3D12CreateDevice"));
  if (!pfnCreateDevice) return;

  ID3D12Device* dev = nullptr;
  HRESULT hr = pfnCreateDevice(nullptr, D3D_FEATURE_LEVEL_11_0, __uuidof(ID3D12Device), reinterpret_cast<void**>(&dev));
  if (SUCCEEDED(hr) && dev) {
    void** vtable = *reinterpret_cast<void***>(dev);
    constexpr size_t kSlot = 10;
    void* slot = vtable[kSlot];
    if (slot && slot != reinterpret_cast<void*>(&NdCreateGraphicsPipelineStateDetour)) {
      DWORD old_prot = 0;
      if (VirtualProtect(&vtable[kSlot], sizeof(void*), PAGE_READWRITE, &old_prot)) {
        g_real_create_graphics_pipeline_state = reinterpret_cast<NdCreateGraphicsPipelineStateFn>(slot);
        vtable[kSlot] = reinterpret_cast<void*>(&NdCreateGraphicsPipelineStateDetour);
        DWORD ignored = 0;
        VirtualProtect(&vtable[kSlot], sizeof(void*), old_prot, &ignored);
        FlushInstructionCache(GetCurrentProcess(), &vtable[kSlot], sizeof(void*));
        REXLOG_INFO("neverdead: [D3D12-HOOK] ID3D12Device::CreateGraphicsPipelineState interceptado (vtable[{}])", kSlot);
      }
    }
    dev->Release();
  }
}
}  // namespace
#endif  // _WIN32
//=============================================================================
// GATE 1 — Forzar PaintMode::kGuestOutputThreadImmediately en rexruntime.dll
//=============================================================================
// Problema: los overlays externos marcan el refresco del monitor (200 Hz en
// este equipo) en vez de los 60 FPS del invitado, porque el presenter pinta en
// el hilo UI una vez por vblank del monitor.
//
// `rex::ui::Presenter::GetDesiredPaintModeFromUIThread` (RVA 0xD6FA0 en la DLL
// distribuida, 43 bytes) se desensambla asi:
//
//   0xD6FA0  31 C0                    xor  eax, eax             ; kNone
//   0xD6FA2  84 D2                    test dl, dl               ; is_paintable
//   0xD6FA4  74 24                    je   0xD6FCA (ret)
//   0xD6FA6  B8 01 00 00 00           mov  eax, 1               ; kUIThreadOnRequest
//   0xD6FAB  80 3D .. .. .. .. 01     cmp  byte [rip+X], 1      ; host_present_from_non_ui_thread
//   0xD6FB2  75 16                    jne  0xD6FCA (ret)
//   0xD6FB4  80 B9 B1 00 00 00 00     cmp  byte [rcx+0xB1], 0   ; implicit vsync
//   0xD6FBB  75 0D                    jne  0xD6FCA (ret)
//   0xD6FBD  31 C0                    xor  eax, eax
//   0xD6FBF  48 83 B9 C0 01 00 00 01  cmp  qword [rcx+0x1C0], 1 ; ui_drawers_.size()
//   0xD6FC7  83 D0 01                 adc  eax, 1               ; 0 -> 2, >=1 -> 1
//   0xD6FCA  C3                       ret
//
// El unico tramo que depende de `ui_drawers_` es 0xD6FBD..0xD6FC9 (13 bytes).
// Sustituirlo por `mov eax, 2` + NOPs hace que, cuando la superficie es
// pintable, invitado y host vayan 1:1: cada VdSwap produce exactamente un
// Present y el overlay mide 60. La rama `!is_paintable -> kNone` queda intacta
// (esta antes del parche), igual que las dos salidas anticipadas por cvar.
//
// HALLAZGO 2026-10-25: en esta build (rexruntime.dll 0.10.0.24-dev) esta
// funcion tiene CERO callers directos: el compilador inlineo
// GetDesiredPaintModeFromUIThread en sus tres usos (presenter.cpp:512, 1405 y
// 1544). Parchear la copia out-of-line, por tanto, no cambia el modo. Se deja
// el parche como documentacion/defensa por si otra build la llama, pero el
// efecto real lo produce NdInstallSetPaintModeHook (0xD6170), que intercepta
// el unico punto que escribe paint_mode_.
//
// NO se toca rexruntime.dll en disco: se parchea la copia mapeada en memoria,
// verificando los bytes originales antes de escribir. Desactivable con
// ND_PRESENT_GATE=0.
void NdSilenceXctdLog() {
  static std::atomic<bool> s_done{false};
  if (s_done.load(std::memory_order_relaxed)) {
    return;
  }
  HMODULE mod = GetModuleHandleA("rexruntime.dll");
  if (mod == nullptr) {
    return;
  }
  uint8_t* base = reinterpret_cast<uint8_t*>(mod);
  constexpr size_t kRva = 0x3DB042;
  uint8_t* target = base + kRva;
  // 85 C0 0F 84 34 02 00 00 (test eax, eax; je 0x3db27e)
  static const uint8_t kExpected[8] = {0x85, 0xC0, 0x0F, 0x84, 0x34, 0x02, 0x00, 0x00};
  // E9 37 02 00 00 90 90 90 (jmp 0x3db27e; 3x NOP)
  static const uint8_t kPatched[8] = {0xE9, 0x37, 0x02, 0x00, 0x00, 0x90, 0x90, 0x90};
  if (std::memcmp(target, kPatched, sizeof(kPatched)) == 0 ||
      (target[0] == 0x8D && target[1] == 0x0D)) {
    if (target[0] == 0x8D && target[1] == 0x0D) {
      DWORD old_protect = 0;
      if (VirtualProtect(target, 8, PAGE_EXECUTE_READWRITE, &old_protect) != 0) {
        std::memset(target, 0x90, 8);
        DWORD ignored = 0;
        VirtualProtect(target, 8, old_protect, &ignored);
        FlushInstructionCache(GetCurrentProcess(), target, 8);
      }
    }
    s_done.store(true, std::memory_order_relaxed);
    REXLOG_INFO("neverdead: [XCTD-SILENCE] error logs de XFileXctdCompressionInformation silenciados (rexruntime+0x{:X})", kRva);
    return;
  }
  if (std::memcmp(target, kExpected, sizeof(kExpected)) != 0) {
    REXLOG_ERROR("neverdead: [XCTD-SILENCE] bytes inesperados en rexruntime+0x{:X}; no se parchea", kRva);
    s_done.store(true, std::memory_order_relaxed);
    return;
  }
  DWORD old_protect = 0;
  if (VirtualProtect(target, sizeof(kPatched), PAGE_EXECUTE_READWRITE, &old_protect) != 0) {
    std::memcpy(target, kPatched, sizeof(kPatched));
    DWORD ignored = 0;
    VirtualProtect(target, sizeof(kPatched), old_protect, &ignored);
    FlushInstructionCache(GetCurrentProcess(), target, sizeof(kPatched));
    s_done.store(true, std::memory_order_relaxed);
    REXLOG_INFO("neverdead: [XCTD-SILENCE] error logs de XFileXctdCompressionInformation silenciados (rexruntime+0x{:X})", kRva);
  }
}
//=============================================================================
// Gating de Present de DXGI (Sincronizacion 1:1 con frames del invitado)
//=============================================================================
// IMPORTANTE (auditoria 2026-10-25): el parche anterior en rexruntime+0x586A92
// NO era IDXGISwapChain::Present. El desensamblado real de ese tramo es:
//
//   00586A92  49 8B 4D 18        mov rcx, [r13+0x18]
//   00586A96  48 8B 01           mov rax, [rcx]
//   00586A99  FF 50 40           call [rax+0x40]
//   00586A9C  85 C0              test eax, eax
//   00586A9E  0F B6 DB           movzx ebx, bl
//   00586AA1  41 0F 48 DF        cmovs ebx, r15d
//
// con [r13+0x20] como valor y [rsi+0x60] como handle: es el metodo 8 de
// ID3D12Fence (SetEventOnCompletion), dentro de un bucle que recorre recursos
// y los libera. Instalar alli el gate no filtraba Present (el overlay seguia
// a 190 FPS) y podia romper la sincronizacion de fences.
//
// La puerta real se instala sobre la vtable de IDXGISwapChain, interceptando
// IDXGIFactory2::CreateSwapChainForHwnd para enganchar el swap chain en cuanto
// se crea (ver nd_install_factory_hook / NdPresentHook arriba).
void NdInstallPresentGate() {
#ifdef _WIN32
  nd_install_factory_hook();
#endif
}
//=============================================================================
// Desbloqueo asincrono real del Pipeline Cache en rexgpu-xenos.dll (Estilo DownpourRecomp)
//
// En rexgpu-xenos.dll, PipelineCache::EndSubmission (0x0EAF90):
//   0xEB030: 0F 84 81 00 00 00        je 0xEB0B7 (cola vacia -> ret)
//   0xEB039: call 0xead90             drena cola y vincula PSOs completados por workers
//   ...
//   0xEB093: call _Cnd_signal         despierta hilos workers para compilar
//   0xEB098: mov rcx, [rsi + 0x488]   WaitHandle
//   0xEB09F: movabs r8, 0x7fffffffffffffff ; TIMEOUT INFINITO (BARRERA QUE CONGELA EL JUEGO 1-3s)
//   0xEB0B2: jmp rexruntime!Wait
//
// SOLUCION (DownpourRecomp / Splatterhouse):
// NO tocar 0xEB030: dejar que call 0xead90 y call _Cnd_signal se ejecuten SIEMPRE.
// Modificar unicamente el timeout infinito de 0xEB09F (10 bytes: 49 B8 ...):
//   Original: 49 B8 FF FF FF FF FF FF FF 7F (timeout infinito)
//   Parche:   49 B8 01 00 00 00 00 00 00 00 (timeout de 1 ms)
// Asi los workers despiertan al instante, compilan en segundo plano, 0xead90 los
// vincula en los siguientes frames y el hilo principal jamas se congela 1-3s.
//=============================================================================
void NdPatchAsyncPipelineCache() {
  static std::atomic<bool> s_done{false};
  if (s_done.load(std::memory_order_relaxed)) {
    return;
  }
  HMODULE mod = GetModuleHandleA("rexgpu-xenos.dll");
  if (mod == nullptr) {
    return;
  }
  uint8_t* base = reinterpret_cast<uint8_t*>(mod);

  uint64_t timeout_ms = 0; // Default: 0 ms (non-blocking poll)
  const char* env_to = std::getenv("ND_PSO_TIMEOUT_MS");
  if (env_to != nullptr) {
    timeout_ms = std::strtoull(env_to, nullptr, 10);
  }
  uint8_t kPatched[10] = {0x49, 0xB8, 0, 0, 0, 0, 0, 0, 0, 0};
  std::memcpy(kPatched + 2, &timeout_ms, sizeof(timeout_ms));

  const size_t kCandidates[] = {0xE39EC, 0xEB09F};
  uint8_t* target = nullptr;
  size_t chosen_rva = 0;

  for (size_t rva : kCandidates) {
    uint8_t* site = base + rva;
    if (site[0] == 0x49 && site[1] == 0xB8 && std::memcmp(site, kPatched, sizeof(kPatched)) == 0) {
      s_done.store(true, std::memory_order_relaxed);
      REXLOG_INFO("neverdead: [ASYNC-PSO] Timeout en PipelineCache::EndSubmission ya se encuentra parcheado a {} ms en rexgpu-xenos+0x{:X}", timeout_ms, rva);
      return;
    }
    if (site[0] == 0x49 && site[1] == 0xB8 && (site[2] == 0x01 || (site[2] == 0xFF && site[9] == 0x7F))) {
      target = site;
      chosen_rva = rva;
      break;
    }
  }

  if (target == nullptr) {
    auto dos = reinterpret_cast<const IMAGE_DOS_HEADER*>(base);
    auto nt = reinterpret_cast<const IMAGE_NT_HEADERS*>(base + dos->e_lfanew);
    const size_t text_size = nt->OptionalHeader.SizeOfCode;
    const uint8_t* text_start = base + nt->OptionalHeader.BaseOfCode;

    for (size_t off = 0; off + 10 <= text_size; ++off) {
      const uint8_t* site = text_start + off;
      if (site[0] == 0x49 && site[1] == 0xB8) {
        if (std::memcmp(site, kPatched, sizeof(kPatched)) == 0) {
          size_t rva = static_cast<size_t>(site - base);
          s_done.store(true, std::memory_order_relaxed);
          REXLOG_INFO("neverdead: [ASYNC-PSO] Timeout en PipelineCache::EndSubmission ya se encuentra parcheado a {} ms en rexgpu-xenos+0x{:X}", timeout_ms, rva);
          return;
        }
        if ((site[2] == 0xFF && site[9] == 0x7F) || site[2] == 0x01) {
          target = const_cast<uint8_t*>(site);
          chosen_rva = static_cast<size_t>(site - base);
          break;
        }
      }
    }
  }

  if (target == nullptr) {
    REXLOG_INFO("neverdead: [ASYNC-PSO] PipelineCache::EndSubmission ya configurado para modo asincrono en rexgpu-xenos");
    s_done.store(true, std::memory_order_relaxed);
    return;
  }

  DWORD old_protect = 0;
  if (VirtualProtect(target, sizeof(kPatched), PAGE_EXECUTE_READWRITE, &old_protect) != 0) {
    std::memcpy(target, kPatched, sizeof(kPatched));
    DWORD ignored = 0;
    VirtualProtect(target, sizeof(kPatched), old_protect, &ignored);
    FlushInstructionCache(GetCurrentProcess(), target, sizeof(kPatched));
    s_done.store(true, std::memory_order_relaxed);
    REXLOG_INFO(
        "neverdead: [ASYNC-PSO] Timeout en PipelineCache::EndSubmission reducido a {} ms en "
        "rexgpu-xenos+0x{:X} (ejecutando 0xead90 y _Cnd_signal sin congelar el hilo principal)",
        timeout_ms, chosen_rva);
  }
}

//=============================================================================
// Silenciado de logs de spam que bloquean I/O (73,974 lineas)
//=============================================================================
void NdSilenceSpamLogs() {
  static std::atomic<bool> s_done{false};
  if (s_done.load(std::memory_order_relaxed)) return;

  // 1. Silenciar PM4_DRAW_INDX y Vertex fetch en rexgpu-xenos.dll
  HMODULE gpu_mod = GetModuleHandleA("rexgpu-xenos.dll");
  if (gpu_mod) {
    uint8_t* base = reinterpret_cast<uint8_t*>(gpu_mod);

    // RVA 0x15881: 0F 84 BD 00 00 00 -> E9 BE 00 00 00 90 (jmp 0x15944)
    uint8_t* p1 = base + 0x15881;
    DWORD old1 = 0;
    if (VirtualProtect(p1, 6, PAGE_EXECUTE_READWRITE, &old1)) {
      p1[0] = 0xE9; p1[1] = 0xBE; p1[2] = 0x00; p1[3] = 0x00; p1[4] = 0x00; p1[5] = 0x90;
      DWORD ign = 0;
      VirtualProtect(p1, 6, old1, &ign);
      FlushInstructionCache(GetCurrentProcess(), p1, 6);
    }

    // RVA 0xA68F1: 0F 84 89 04 00 00 -> E9 8A 04 00 00 90 (jmp 0xA6D80)
    uint8_t* p2 = base + 0xA68F1;
    DWORD old2 = 0;
    if (VirtualProtect(p2, 6, PAGE_EXECUTE_READWRITE, &old2)) {
      p2[0] = 0xE9; p2[1] = 0x8A; p2[2] = 0x04; p2[3] = 0x00; p2[4] = 0x00; p2[5] = 0x90;
      DWORD ign = 0;
      VirtualProtect(p2, 6, old2, &ign);
      FlushInstructionCache(GetCurrentProcess(), p2, 6);
    }

    // RVA 0xA6976: 0F 8F 04 04 00 00 -> E9 05 04 00 00 90 (jmp 0xA6D80)
    uint8_t* p3 = base + 0xA6976;
    DWORD old3 = 0;
    if (VirtualProtect(p3, 6, PAGE_EXECUTE_READWRITE, &old3)) {
      p3[0] = 0xE9; p3[1] = 0x05; p3[2] = 0x04; p3[3] = 0x00; p3[4] = 0x00; p3[5] = 0x90;
      DWORD ign = 0;
      VirtualProtect(p3, 6, old3, &ign);
      FlushInstructionCache(GetCurrentProcess(), p3, 6);
    }
    REXLOG_INFO("neverdead: [LOG-FILTER] Logs de PM4_DRAW_INDX y Vertex fetch silenciados en rexgpu-xenos");
  }

  // 2. Silenciar UIPaint en rexruntime.dll
  HMODULE rt_mod = GetModuleHandleA("rexruntime.dll");
  if (rt_mod) {
    uint8_t* base = reinterpret_cast<uint8_t*>(rt_mod);
    // RVA 0xE4150: 74 7F -> EB 7F (jmp 0xE41D1)
    uint8_t* p4 = base + 0xE4150;
    DWORD old4 = 0;
    if (VirtualProtect(p4, 2, PAGE_EXECUTE_READWRITE, &old4)) {
      p4[0] = 0xEB; p4[1] = 0x7F;
      DWORD ign = 0;
      VirtualProtect(p4, 2, old4, &ign);
      FlushInstructionCache(GetCurrentProcess(), p4, 2);
      REXLOG_INFO("neverdead: [LOG-FILTER] Log de UIPaint silenciado en rexruntime");
    }
  }

  if (gpu_mod && rt_mod) {
    s_done.store(true, std::memory_order_relaxed);
  }
}

void NdEarlyInit() {
  NdLoadSettingsFromIni();
  // Granularidad de 1 ms para el pacer (Sleep del hilo que presenta).
  NdEnsureTimerResolution();
  // ORDEN: primero los parches de paint_mode (para que el primer
  // PaintFromUIThread acabe en modo 2), luego la captura del Presenter, el
  // gating temprano y el hook de Present.
  NdPatchInlinedPaintMode();
  NdInstallSetPaintModeHook();
  NdInstallPaintFromUIThreadHook();
  NdInstallPaintAndPresentGate();
  NdInstallPaintReturnHook();
  NdInstallPresentGate();
  NdSilenceXctdLog();
  NdSilenceSpamLogs();
  NdPatchAsyncPipelineCache();
  nd_install_d3d12_device_hook();
}
void NdVdSetDisplayModeHook(PPCRegister& r3) {
  const uint32_t mode = r3.u32;
  if (mode < 0x10000u) {
    return;
  }
  const uint32_t w = read_guest_be32(mode + 0);
  const uint32_t h = read_guest_be32(mode + 4);
  const uint32_t refresh_bits = read_guest_be32(mode + 20);
  const float refresh = be_bits_to_float(refresh_bits);
  // NO se reescribe el refresh del invitado. Con los helpers de float ya
  // corregidos, escribir 62.0f de verdad hace que el motor espere vblanks tras
  // el tercer VdSwap y se congele (verificado 02-nov: con ND_FPS60=0 arranca a
  // 100 FPS; con la escritura activa se queda en [SWAP] #3 sin un solo [FRAME]).
  // El limite de frames lo aplica el pacer del host en NdPresentHook.
  static float last_refresh = -1.0f;
  if (refresh != last_refresh) {
    last_refresh = refresh;
    REXLOG_INFO(
        "neverdead: [VIDEO] VdSetDisplayMode mode=0x{:08X} {}x{} refresh={:.1f}Hz",
        mode, w, h, refresh);
  }
}
void NdVdInitHook() {
  const uint64_t n = g_vd_init_calls.fetch_add(1, std::memory_order_relaxed) + 1;
  // Los parches ya se instalaron en OnPreSetup (antes del Presenter); aqui se
  // reintentan los que dependen de modulos cargados tarde (rexgpu-xenos.dll).
  NdEarlyInit();
  // El parche del timeout de PipelineCache::EndSubmission debe aplicarse AQUI:
  // despues de que InitializeShaderStorage cree los PSOs (aplicarlo antes rompe
  // esa cola) y antes del primer draw del invitado (en VdSwap #2 llega tarde:
  // el invitado ya se queda en la barrera infinita y nunca llega al swap #2).
  NdPatchAsyncPipelineCache();
  if (n <= 4) {
    REXLOG_INFO("neverdead: [MIDASM] VdInitializeEngines call #{} (site 0x826BC3FC)", n);
  }
}
static void NdApplyFramePacer();
static void NdCheckFpsToggleKey();
static void NdDumpAsuraCVarsOnce();
static bool NdPacerActive();

//=============================================================================
// Sonda del ring PM4 (ND_RING=1) — solo lectura, no altera el juego
//=============================================================================
// El driver D3D del invitado va enlazado estaticamente (rango 0x826B0000) y
// escribe sus comandos en un primary buffer. sub_826B5548 es su asignador:
//     r11 = [dev+48]                          ; puntero de escritura
//     r10 = [dev+52]                          ; fin del buffer
//     if (r11 + n*4 > r10) sub_826B6B38(dev)  ; ring lleno -> volcar/crecer
//     return [dev+48]
// VdSwap se invoca justo despues de reservar 64 dwords (256 B) para el paquete
// de swap, y el puntero [dev+48] solo avanza DESPUES del swap
// (`stw r11,48(r31)` con r11 = r30+256 en sub_826B8620). Por tanto, en el hook
// [dev+48] marca el final exacto de los comandos de este frame.
//
// Formato PM4 (rexglue_sdk_src/src/graphics/command_processor.cpp:691-760):
//   type 0: count=((h>>16)&0x3FFF)+1 valores, base=h&0x7FFF,
//           write_one=(h>>15)&1  -> avance 1+count
//   type 1: dos registros         -> avance 3
//   type 2: NOP                   -> avance 1
//   type 3: opcode=(h>>8)&0x7F, count=((h>>16)&0x3FFF)+1 -> avance 1+count
//           bit 0 = paquete predicado por bin (bin_select & bin_mask)
//=============================================================================
static bool NdRingEnabled() {
  static const bool s = []() {
    const char* v = std::getenv("ND_RING");
    return v != nullptr && v[0] == '1';
  }();
  return s;
}

static inline uint32_t nd_be32(const uint8_t* p) {
  return (uint32_t(p[0]) << 24) | (uint32_t(p[1]) << 16) | (uint32_t(p[2]) << 8) |
         uint32_t(p[3]);
}

static inline void nd_store_be32(uint8_t* p, uint32_t val) {
  p[0] = static_cast<uint8_t>(val >> 24);
  p[1] = static_cast<uint8_t>(val >> 16);
  p[2] = static_cast<uint8_t>(val >> 8);
  p[3] = static_cast<uint8_t>(val);
}

//=============================================================================
// Estado Sombra XDK D3D (ND_XDK_FILTER=1)
//=============================================================================
// Reduce la sobrecarga de microcodigo y constantes en el primary buffer:
// - Shaders Type-3 0x27 (PM4_IM_LOAD): si el shader es identico al ultimo fijado,
//   se marca la cabecera como Type-3 PM4_NOP (opcode 0x00).
// - Constantes Type-3 0x2F (PM4_LOAD_ALU_CONSTANT): si las constantes float4
//   c0..c255 ya coinciden con el estado sombra, se marcan como PM4_NOP.
// - Reset automatico en cada frame boundary y en vaciado de ring (sub_826B6B38).
//=============================================================================
static bool NdXdkFilterEnabled() {
  static const bool s = []() {
    const char* v = std::getenv("ND_XDK_FILTER");
    if (v != nullptr && v[0] == '1') {
      return true; // Solo si se fuerza explicitamente por variable de entorno
    }
    return false; // Desactivado por defecto para evitar sobrescribir con NOP y corromper Vertex fetch constants
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

static NdXdkShadowState g_xdk_shadow{};

extern "C" void NdResetXdkShadowStateOnBoundary() {
  g_xdk_shadow.Reset();
}

static LARGE_INTEGER g_guest_frame_start_qpc{};
static double g_window_cpu_ms_sum = 0.0;
static double g_window_frame_ms_sum = 0.0;
static double g_window_min_frame_ms = 999999.0;
static double g_window_max_frame_ms = 0.0;
static uint32_t g_window_sample_count = 0;

static uint32_t g_ring_ops[128];      // histograma de opcodes type-3
static uint32_t g_ring_reg_hits[4];   // [0]=0x2000 [1]=0x2208 [2]=otros

static void NdRingFlush(uint64_t swap_index) {
  char ops[220] = {};
  size_t used = 0;
  for (uint32_t op = 0; op < 128; ++op) {
    if (g_ring_ops[op] == 0) continue;
    const int w = snprintf(ops + used, sizeof(ops) - used, "%s0x%02X:%u",
                           used ? " " : "", op, g_ring_ops[op]);
    if (w <= 0 || used + size_t(w) >= sizeof(ops)) break;
    used += size_t(w);
  }
  REXLOG_INFO("neverdead: [RING] swap #{} | opcodes type-3 del frame: {}", swap_index,
              ops[0] ? ops : "(ninguno)");
  std::memset(g_ring_ops, 0, sizeof(g_ring_ops));
  std::memset(g_ring_reg_hits, 0, sizeof(g_ring_reg_hits));
}

static std::atomic<bool> g_resolve_trace_done{false};
static std::atomic<uint32_t> g_guest_draws_this_frame{0};

extern "C" void NdOnDrawCall() {
  g_guest_draws_this_frame.fetch_add(1, std::memory_order_relaxed);
}

static std::atomic<bool> g_opt_luma_resolve{true};

extern "C" bool NdShouldSkipLumaResolve() {
  if (!g_opt_luma_resolve.load(std::memory_order_relaxed)) {
    return false;
  }
  static std::atomic<uint32_t> g_luma_frame_counter{0};
  const uint32_t c = g_luma_frame_counter.fetch_add(1, std::memory_order_relaxed);
  // Decimacion temporal 1:2: saltar ejecucion en frames impares (1 de cada 2 frames)
  return (c & 1) != 0;
}

static void NdRingProbe(uint32_t device, uint64_t swap_index) {
  const bool ring_on = NdRingEnabled();
  const bool filter_on = NdXdkFilterEnabled();
  if ((!ring_on && !filter_on) || device == 0 || g_translate == nullptr) {
    return;
  }
  const uint8_t* dev = g_translate(device);
  if (dev == nullptr) {
    return;
  }
  const uint32_t wr = nd_be32(dev + 48);
  static uint32_t s_last = 0;
  // Primer frame, wrap del ring o salto anomalo: reanclar sin muestrear.
  if (s_last == 0 || wr <= s_last || (wr - s_last) > (4u << 20)) {
    s_last = wr;
    return;
  }
  const uint8_t* p = g_translate(s_last);
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

  // Analisis de shaders unicos en este frame
  std::vector<uint64_t> frame_shader_hashes;
  frame_shader_hashes.reserve(512);
  uint32_t im_load_vs = 0, im_load_ps = 0;

  const bool should_trace_resolves = (swap_index > 120 && !g_resolve_trace_done.load(std::memory_order_relaxed));

  while (i < dwords) {
    const uint32_t h = nd_be32(p + (i << 2));
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
            const uint32_t addr_type = nd_be32(p + ((i + 1) << 2));
            const uint32_t start_size = nd_be32(p + ((i + 2) << 2));
            const uint32_t addr = addr_type & ~0x3;
            const uint32_t shader_type = addr_type & 0x3;
            const uint32_t size_dwords = start_size & 0xFFFF;
            if (shader_type == 0) ++im_load_vs; else ++im_load_ps;
            const uint8_t* code = (addr && g_translate) ? g_translate(addr) : nullptr;
            if (code != nullptr && size_dwords > 0) {
              const uint64_t hval = NdFnv1a64(code, size_dwords * 4);
              frame_shader_hashes.push_back(hval);
            }
            if (filter_on && addr != 0) {
              if (shader_type == 0) {
                if (g_xdk_shadow.current_vs_addr == addr && g_xdk_shadow.current_vs_size == size_dwords) {
                  // NOP Type-3 opcode 0x10 (PM4_NOP)
                  uint8_t* mut_p = const_cast<uint8_t*>(p);
                  nd_store_be32(mut_p + (i << 2), (h & ~0x00007F00) | (0x10 << 8));
                  ++g_xdk_shadow.filtered_shaders;
                } else {
                  g_xdk_shadow.current_vs_addr = addr;
                  g_xdk_shadow.current_vs_size = size_dwords;
                  ++g_xdk_shadow.total_shaders;
                }
              } else if (shader_type == 1) {
                if (g_xdk_shadow.current_ps_addr == addr && g_xdk_shadow.current_ps_size == size_dwords) {
                  // NOP Type-3 opcode 0x10 (PM4_NOP)
                  uint8_t* mut_p = const_cast<uint8_t*>(p);
                  nd_store_be32(mut_p + (i << 2), (h & ~0x00007F00) | (0x10 << 8));
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
            const uint32_t address = nd_be32(p + ((i + 1) << 2)) & 0x3FFFFFFF;
            const uint32_t offset_type = nd_be32(p + ((i + 2) << 2));
            const uint32_t size_dwords = nd_be32(p + ((i + 3) << 2)) & 0xFFF;
            const uint32_t index = offset_type & 0x7FF;
            const uint32_t alu_type = (offset_type >> 16) & 0xFF;
            if (alu_type == 0 && (size_dwords % 4) == 0 && size_dwords > 0) {
              const uint32_t count_vec4 = size_dwords / 4;
              if (index + count_vec4 <= 256 && g_translate != nullptr) {
                const uint8_t* src = g_translate(address);
                if (src != nullptr) {
                  bool all_same = true;
                  for (uint32_t v = 0; v < count_vec4; ++v) {
                    const uint32_t c_idx = index + v;
                    if (!g_xdk_shadow.alu_consts_valid[c_idx]) {
                      all_same = false;
                      break;
                    }
                    const uint32_t w0 = nd_be32(src + ((v * 4 + 0) << 2));
                    const uint32_t w1 = nd_be32(src + ((v * 4 + 1) << 2));
                    const uint32_t w2 = nd_be32(src + ((v * 4 + 2) << 2));
                    const uint32_t w3 = nd_be32(src + ((v * 4 + 3) << 2));
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
                    nd_store_be32(mut_p + (i << 2), (h & ~0x00007F00) | (0x10 << 8));
                    g_xdk_shadow.filtered_consts += count_vec4;
                  } else {
                    for (uint32_t v = 0; v < count_vec4; ++v) {
                      const uint32_t c_idx = index + v;
                      g_xdk_shadow.alu_consts[c_idx][0] = nd_be32(src + ((v * 4 + 0) << 2));
                      g_xdk_shadow.alu_consts[c_idx][1] = nd_be32(src + ((v * 4 + 1) << 2));
                      g_xdk_shadow.alu_consts[c_idx][2] = nd_be32(src + ((v * 4 + 2) << 2));
                      g_xdk_shadow.alu_consts[c_idx][3] = nd_be32(src + ((v * 4 + 3) << 2));
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
        case 0x50:  // PM4_SET_BIN_MASK  (tiling)
          ++bin_mask;
          break;
        case 0x51:  // PM4_SET_BIN_SELECT (tiling)
          ++bin_select;
          break;
        case 0x2A:  // PM4_XE_SWAP
          ++swaps;
          break;
        default:
          break;
      }
      ++g_ring_ops[opcode];
      i += 1 + count;
    } else if (type == 0u) {
      const uint32_t count = ((h >> 16) & 0x3FFF) + 1;
      const uint32_t base = h & 0x7FFF;
      const bool one = ((h >> 15) & 1u) != 0;
      if (i + 1 + count > dwords) break;
      for (uint32_t m = 0; m < count; ++m) {
        const uint32_t reg = one ? base : base + m;
        const uint32_t val = nd_be32(p + ((i + 1 + m) << 2));
        ++reg_writes;
        if (reg == 0x2000) {  // RB_SURFACE_INFO
          surface_info = val;
          ++g_ring_reg_hits[0];
        } else if (reg == 0x2001) {  // RB_COLOR_INFO
          color_info = val;
        } else if (reg == 0x2002) {  // RB_DEPTH_INFO
          depth_info = val;
        } else if (reg == 0x2019) {  // RB_COPY_DEST_BASE
          copy_dest_base = val;
        } else if (reg == 0x201B) {  // RB_COPY_DEST_INFO
          copy_dest_info = val;
        } else if (reg == 0x2208) {  // RB_MODECONTROL
          modecontrol = val;
          ++g_ring_reg_hits[1];
          if ((val & 7u) == 6u) {  // EdramMode::kCopy
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
      ++i;  // type 2 NOP
    }
  }
  s_last = wr;
  NdResetXdkShadowStateOnBoundary();

  if (draws >= 400 && resolves >= 10) {
    if (!g_hospital_reached.exchange(true)) {
      REXLOG_INFO("neverdead: [AUTO-NAV] *** HOSPITAL 3D ALCANZADO (draws={} resolves={}) -> Auto-navegacion desactivada ***",
                  draws, resolves);
    }
    if (!g_in_3d_gameplay.load(std::memory_order_relaxed)) {
      g_in_3d_gameplay.store(true, std::memory_order_relaxed);
      REXLOG_INFO("neverdead: [3D-GAMEPLAY] Activado (draws={} resolves={}) -> Framerate desbloqueado en vivo",
                  draws, resolves);
      NdNotifyAsuraFpsIntervalChanged();
    }
  }

  if (should_trace_resolves && resolves >= 10) {
    g_resolve_trace_done.store(true, std::memory_order_relaxed);
  }

  // Deduplicacion de hashes de shader en el frame
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
          "shaders unicos={} (ratio {:.1f}x redundancia)",
          swap_index, frame_shader_hashes.size(), im_load_vs, im_load_ps,
          unique_shaders, double(frame_shader_hashes.size()) / std::max(size_t(1), unique_shaders));
    }
    NdRingFlush(swap_index);
  }
}

void NdVdSwapHook(PPCRegister& r31) {
  g_guest_device_va.store(r31.u32, std::memory_order_relaxed);
  const uint64_t n = g_vd_swap_calls.fetch_add(1, std::memory_order_relaxed) + 1;

  // Deteccion desacoplada y dinamica de Gameplay 3D con filtro de histeresis
  const uint32_t guest_draws = g_guest_draws_this_frame.exchange(0, std::memory_order_relaxed);
  static uint32_t s_high_draw_streak = 0;
  static uint32_t s_low_draw_streak = 0;

  if (guest_draws >= 250 || g_hospital_reached.load(std::memory_order_relaxed)) {
    s_low_draw_streak = 0;
    if (!g_in_3d_gameplay.load(std::memory_order_relaxed)) {
      g_in_3d_gameplay.store(true, std::memory_order_relaxed);
      REXLOG_INFO("neverdead: [3D-GAMEPLAY] Activado (draws={} hospital={}) -> Framerate desbloqueado en vivo",
                  guest_draws, g_hospital_reached.load());
      NdNotifyAsuraFpsIntervalChanged();
    }
  } else if (guest_draws < 150 && !g_hospital_reached.load(std::memory_order_relaxed)) {
    s_high_draw_streak = 0;
    if (++s_low_draw_streak >= 2) {
      if (g_in_3d_gameplay.load(std::memory_order_relaxed)) {
        g_in_3d_gameplay.store(false, std::memory_order_relaxed);
        REXLOG_INFO("neverdead: [3D-GAMEPLAY] Desactivado (draws={} streak={}) -> Restaurado pacer 30 FPS en Menu/FMV",
                    guest_draws, s_low_draw_streak);
        NdNotifyAsuraFpsIntervalChanged();
      }
    }
  } else {
    // Zona de histeresis [150, 249]: se preserva el estado sin transicionar
    s_high_draw_streak = 0;
    s_low_draw_streak = 0;
  }

  // BLINDAJE: el hilo del invitado solo presenta con paint_mode_ == 2. Si algo
  // lo dejo en 0/1, se corrige aqui (antes de EarlyInit) para no arrastrar el
  // fallback a ~0.1 FPS durante todo el frame.
  nd_restore_paint_mode();
  const bool diag = NdIsDiagEnabled();
  if (diag && n <= 3) { REXLOG_INFO("neverdead: [SWAP] ENTRADA #{} (hilo invitado llego a VdSwap)", n); }
  // Asegurar parches en memoria en los primeros swaps
  if (n <= 10) {
    NdEarlyInit();
  }
  if (n >= 2) {
    NdPatchAsyncPipelineCache();
  }
  const auto now = std::chrono::steady_clock::now();
  // ALPHA: sin ND_DIAG no se muestrea NADA (ni IO_COUNTERS ni GetProcessTimes,
  // que son syscalls por frame). ND_PROFILE_THREADS sigue siendo el segundo
  // interruptor para el detalle por hilo.
  static const bool s_enable_thread_profiling =
      NdIsDiagEnabled() && (std::getenv("ND_PROFILE_THREADS") != nullptr);
  NdProcCounters cur_counters{};
  if (s_enable_thread_profiling) {
    cur_counters = nd_sample_counters();
    if (g_threads.empty() ||
        std::chrono::duration_cast<std::chrono::seconds>(now - g_threads_last_refresh).count() >=
            2) {
      nd_refresh_threads();
    }
    nd_sample_threads();
  }
  if (g_have_last) {
    const double ms =
        std::chrono::duration<double, std::milli>(now - g_last_swap).count();
    g_sum_ms += ms;
    if (ms > g_max_ms) {
      g_max_ms = ms;
    }
    // Telemetria Fase 3: CPU y GPU cada 60 frames
    LARGE_INTEGER swap_entry_qpc{};
    QueryPerformanceCounter(&swap_entry_qpc);
    double frame_cpu_ms = 0.0;
    if (g_guest_frame_start_qpc.QuadPart > 0 && g_qpf.QuadPart > 0) {
      frame_cpu_ms = static_cast<double>(swap_entry_qpc.QuadPart - g_guest_frame_start_qpc.QuadPart) * 1000.0 /
                     static_cast<double>(g_qpf.QuadPart);
    }
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
      REXLOG_INFO("neverdead: [FRAME_TIME] avg={:.1f} ms, min={:.1f} ms, max={:.1f} ms, cpu={:.1f} ms, gpu={:.1f} ms",
                  avg_ms, min_ms, max_ms, avg_cpu, avg_gpu);

      const uint64_t sh_total = g_xdk_shadow.total_shaders + g_xdk_shadow.filtered_shaders;
      const double pct_sh = sh_total > 0 ? (100.0 * g_xdk_shadow.filtered_shaders / sh_total) : 0.0;
      const uint64_t c_total = g_xdk_shadow.total_consts + g_xdk_shadow.filtered_consts;
      const double pct_c = c_total > 0 ? (100.0 * g_xdk_shadow.filtered_consts / c_total) : 0.0;
      REXLOG_INFO("neverdead: [XDK_SHADOW] shaders_filtrados={} ({:.1f}%), constantes_filtradas={} ({:.1f}%)",
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
        fprintf(stderr, "[FRAME-SPIKE] VdSwap #%llu tardo %.1f ms (%.1f FPS inst)\n",
                (unsigned long long)n, ms, 1000.0 / ms);
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
    // Clasificacion del pico: CPU quemada vs disco vs espera, y que hilos.
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
          if (thread_ms < 1.0) {
            continue;
          }
          const int written = snprintf(top + used, sizeof(top) - used, "%s[%lu]=%.0fms ",
                                       t.name[0] != '\0' ? t.name : "?",
                                       (unsigned long)t.tid, thread_ms);
          if (written <= 0) {
            break;
          }
          used += size_t(written);
        }
      }
      REXLOG_INFO(
          "neverdead: [STALL] frame {} duro {:.1f} ms | cpu_proceso={:.1f} ms ({:.0f}%) "
          "| disco_leido={:.2f} MB en {} ops | hilos: {}",
          n, ms, cpu_ms, (ms > 0.0 && cpu_ms >= 0.0) ? 100.0 * cpu_ms / ms : 0.0,
          double(read_delta) / (1024.0 * 1024.0), read_ops, top);
    }
    ++g_window_frames;
  }
  g_last_swap = now;
  g_have_last = true;
  g_prev_counters = cur_counters;
  g_have_prev_counters = true;
  // Resumen cada 300 frames (~5 s a 60 FPS). ALPHA: solo con ND_DIAG=1.
  if ((n % 300) == 0) {
    if (diag) {
      const double avg = g_window_frames ? (g_sum_ms / static_cast<double>(g_window_frames)) : 0.0;
      REXLOG_INFO(
          "neverdead: [FRAME] {} frames avg={:.2f} ms ({:.1f} FPS) max={:.1f} ms "
          "spikes>100={} >500={} >1000={} | dxgi_present_fps={:.1f} "
          "(allowed={} skipped={}) paint_mode={}",
          g_window_frames, avg, avg > 0.0 ? 1000.0 / avg : 0.0, g_max_ms, g_spike_100,
          g_spike_500, g_spike_1000, g_present_fps.load(std::memory_order_relaxed),
          g_present_logged_allowed.load(std::memory_order_relaxed),
          g_present_logged_skipped.load(std::memory_order_relaxed), nd_read_paint_mode());
    }
    g_sum_ms = 0.0;
    g_max_ms = 0.0;
    g_window_frames = 0;
    g_spike_100 = 0;
    g_spike_500 = 0;
    g_spike_1000 = 0;
  }
  // El pacer se aplica en NdPresentHook (hilo que presenta), NO aqui: dormir
  // al hilo del invitado antes de __imp__VdSwap lo deja clavado en el swap.
  // Sondeo de teclas cada 32 frames (antes: 6 GetAsyncKeyState por frame).
  if ((n & 31) == 0) {
    NdCheckFpsToggleKey();
  }
  // ALPHA: el volcado de cvars escribe en disco; solo con ND_DIAG=1.
  if (diag && n == 30) {
    NdDumpAsuraCVarsOnce();
  }
  if (diag && n <= 3) { REXLOG_INFO("neverdead: [SWAP] SALIDA #{} (VdSwap retorno al invitado)", n); }
  // Sonda forense del ring PM4 (solo con ND_RING=1): cuenta draws, resolves y
  // predicacion por bin realmente emitidos por el driver D3D del invitado.
  NdRingProbe(r31.u32, n);
}
//=============================================================================;
//=============================================================================;
// Control de Tasa de Refresco y Menu de Configuracion UI (F1)
//=============================================================================;
static std::atomic<float> g_fps_target{30.0f}; // ALPHA: 30 FPS por defecto (gama baja)
static std::atomic<bool> g_fps_mode_initialized{false};
static std::atomic<bool> g_config_ui_open{false};

// Frame Pacer de microsegundos con temporizador de alta resolucion
static HANDLE g_pacer_timer = nullptr;
static LARGE_INTEGER g_qpc_freq{};
static int64_t g_last_pacer_qpc = 0;

// NdEnsureTimerResolution moved above

static void NdInitPacer() {
  static std::once_flag flag;
  std::call_once(flag, []() {
    QueryPerformanceFrequency(&g_qpc_freq);
    // CREATE_WAITABLE_TIMER_HIGH_RESOLUTION (0x2) nativo de Win10 1803+
    g_pacer_timer = CreateWaitableTimerExW(nullptr, nullptr,
                                           CREATE_WAITABLE_TIMER_HIGH_RESOLUTION,
                                           TIMER_ALL_ACCESS);
    if (g_pacer_timer == nullptr) {
      g_pacer_timer = CreateWaitableTimerW(nullptr, FALSE, nullptr);
    }
    NdEnsureTimerResolution();
  });
}

static void NdApplyFramePacer() {
  if (g_guest_throttle_active.load(std::memory_order_acquire)) {
    return;
  }
  float target_fps = g_fps_target.load(std::memory_order_relaxed);
  if (target_fps <= 0.0f) {
    // Ilimitado: coste cero. Ni QueryPerformanceCounter ni temporizador.
    g_last_pacer_qpc = 0;
    return;
  }

  NdInitPacer();

  int64_t target_ticks = static_cast<int64_t>(static_cast<double>(g_qpc_freq.QuadPart) / target_fps);
  if (g_last_pacer_qpc == 0) {
    LARGE_INTEGER now_qpc;
    QueryPerformanceCounter(&now_qpc);
    g_last_pacer_qpc = now_qpc.QuadPart;
    return;
  }

  int64_t target_time = g_last_pacer_qpc + target_ticks;

  LARGE_INTEGER cur_qpc;
  QueryPerformanceCounter(&cur_qpc);

  int64_t remaining_ticks = target_time - cur_qpc.QuadPart;
  if (remaining_ticks > 0) {
    double remaining_ms = (static_cast<double>(remaining_ticks) * 1000.0) / static_cast<double>(g_qpc_freq.QuadPart);
    // Espera hibrida: si queda mas de 2 ms se duerme de verdad (libera CPU y
    // deja respirar a la cola de envio de la GPU); el ultimo tramo se hace con
    // spin de precision. Con timeBeginPeriod(1) Sleep() tiene granularidad ~1 ms.
    if (remaining_ms > 2.0) {
      const DWORD sleep_ms = static_cast<DWORD>(remaining_ms - 1.5);
      if (sleep_ms > 0) Sleep(sleep_ms);
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
}

static bool NdPacerActive() {
  return g_fps_target.load(std::memory_order_relaxed) > 0.0f;
}

//=============================================================================
// Pacing del invitado DESPUES del swap (Tarea 4, v11)
//=============================================================================
// Por que aqui y no antes de __imp__VdSwap: medido en este equipo, frenar antes
// del bl VdSwap estrangula al presentador (39.4 -> 16.8 FPS y 89 picos de
// 150 ms en una ventana de 300 frames). El Present del host se dispara a partir
// del swap del invitado, asi que esperar alli crea una dependencia circular que
// solo rompe el timeout. Justo despues del bl el frame ya esta encolado: dormir
// aqui retrasa el siguiente frame sin bloquear al presentador.
//
// Es pacing por tiempo, no por contadores: no depende de que el presentador
// avance, asi que no puede interbloquearse. 0x82B31FBC sigue siempre a 0.0f.
//=============================================================================
// Llamar desde NdVdSwapPostHook (cuando VdSwap, VdPersistDisplay y stw r11,48(r31) ya terminaron)
static void NdWakeAddress(void* addr) {
  static auto s_fn = []() -> void (WINAPI*)(void*) {
    HMODULE h = GetModuleHandleA("api-ms-win-core-synch-l1-2-0.dll");
    if (!h) h = GetModuleHandleA("kernel32.dll");
    if (!h) h = LoadLibraryA("api-ms-win-core-synch-l1-2-0.dll");
    if (!h) return nullptr;
    return reinterpret_cast<void (WINAPI*)(void*)>(GetProcAddress(h, "WakeByAddressSingle"));
  }();
  if (s_fn) {
    s_fn(addr);
  }
}

static void NdPaceGuestAfterSwapComplete(float target_fps, uint64_t swap_count) {
  NdInitQpcOnce();

  // Despertar/notificar al presentador que el frame del invitado ya esta cerrado
  g_completed_swap_seq.fetch_add(1, std::memory_order_release);
  NdWakeAddress(reinterpret_cast<void*>(&g_completed_swap_seq));

  // No frenar si target_fps <= 0 (Ilimitado) o en los primeros 120 swaps
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
  double elapsed_sec = static_cast<double>(now.QuadPart - g_last_guest_swap_qpc.QuadPart) /
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
}

void NdVdSwapPostHook(PPCRegister& r31) {
  (void)r31;
  const uint64_t swap_count = g_vd_swap_calls.load(std::memory_order_relaxed);

  float target_fps = g_fps_target.load(std::memory_order_relaxed);

  if (!g_in_3d_gameplay.load(std::memory_order_relaxed)) {
    // Fuera de 3D (menus, FMVs, carga): SIEMPRE 30 FPS estricto para proteger decodificadores e hilos
    target_fps = 30.0f;
  } else if (!g_unlock_fps.load(std::memory_order_relaxed)) {
    // Usuario selecciono 30 FPS en el menu F10
    target_fps = 30.0f;
  } else {
    // En 3D gameplay con framerate desbloqueado:
    // Si target_fps == 60.0f -> pacing a 16.666 ms (60 FPS)
    // Si target_fps <= 0.0f -> 0.0f (sin pacing, ilimitado)
  }

  NdPaceGuestAfterSwapComplete(target_fps, swap_count);
}

static void NdDumpAsuraCVarsOnce() {
  static std::atomic<bool> s_dumped{false};
  if (s_dumped.exchange(true)) {
    return;
  }
  FILE* f = fopen("build_clang/asura_cvars_dump.txt", "w");
  if (!f) {
    return;
  }
  // Dos cabezas contiguas en .data; se recorren las dos con el mismo layout.
  const uint32_t heads[2] = {read_guest_be32(0x828E1314), read_guest_be32(0x828E1318)};
  int total = 0;
  for (int h = 0; h < 2; ++h) {
    uint32_t curr = heads[h];
    int count = 0;
    const char* end_reason = "limite_2000";
    fprintf(f, "=== LISTA %d head=0x%08X ===\n", h, curr);
    while (count < 2000) {
      if (!(curr >= 0x00010000 && curr < 0xA0000000)) {
        end_reason = "puntero_fuera_de_rango";
        break;
      }
      const uint32_t name_va = read_guest_be32(curr + 0);
      const uint32_t flag_va = read_guest_be32(curr + 12);
      const uint32_t type = read_guest_be32(curr + 16);
      const uint32_t cb_va = read_guest_be32(curr + 20);
      const uint32_t value_va = read_guest_be32(curr + 24);
      const uint32_t next_va = read_guest_be32(curr + 40);
      // Nombre en UTF-16 BIG-ENDIAN (sub_822CCEA0 hace `lhz r11,0(r3)`).
      char name_utf8[128] = {};
      if (name_va >= 0x00010000 && name_va < 0xA0000000) {
        const uint8_t* np = nd_translate_va(name_va);
        if (np != nullptr) {
          int o = 0;
          for (int k = 0; k < 240 && o < 120; k += 2) {
            const uint16_t ch =
                static_cast<uint16_t>((static_cast<uint16_t>(np[k]) << 8) | np[k + 1]);
            if (ch == 0) {
              break;
            }
            name_utf8[o++] = (ch >= 0x20 && ch < 0x7F) ? static_cast<char>(ch) : '?';
          }
        }
      }
      const uint32_t raw_val = (value_va >= 0x00010000 && value_va < 0xA0000000)
                                   ? ((type == 0) ? read_guest_u8(value_va) : read_guest_be32(value_va))
                                   : 0;
      fprintf(f,
              "[%03d] node=0x%08X flag=0x%08X val_va=0x%08X type=%u cb=0x%08X next=0x%08X "
              "cur=0x%08X name=\"%s\"\n",
              count, curr, flag_va, value_va, type, cb_va, next_va, raw_val, name_utf8);
      if (h == 0 && count < 3) {
        REXLOG_INFO("neverdead: [CVAR-DUMP] lista0 name_va=0x{:08X} -> \"{}\"", name_va, name_utf8);
      }
      ++count;
      if (next_va == 0) {
        end_reason = "next=0";
        break;
      }
      curr = next_va;
    }
    total += count;
    REXLOG_INFO("neverdead: [CVAR-DUMP] lista {} (head=0x{:08X}): {} nodos (fin: {})", h, heads[h],
                count, end_reason);
  }
  fclose(f);
  REXLOG_INFO("neverdead: [CVAR-DUMP] TOTAL {} CVars en build_clang/asura_cvars_dump.txt", total);
}


//=============================================================================
// Menu F10 — Dark Mode nativo, 520x375, logica positiva, F10/ESC instantaneo
//=============================================================================
enum {
  IDC_CFG_COMBO_WND = 1001,
  IDC_CFG_COMBO_FPS = 1002,
  // Pases del renderer verificados con REX_LOAD_U8 en generated/*.cpp
  IDC_CFG_CHK_SHADOW = 1010,
  IDC_CFG_CHK_SSAO,
  IDC_CFG_CHK_MBLUR,
  IDC_CFG_CHK_BLOOM,
  IDC_CFG_CHK_LIGHTSHAFTS,
  IDC_CFG_CHK_FOG,
  IDC_CFG_CHK_FSFX,
  IDC_CFG_CHK_PFX,
  IDC_CFG_CHK_RMLAA,
  IDC_CFG_CHK_HUD,
  IDC_CFG_CHK_LUMA_OPT = 1025,
  IDC_CFG_CHK_UNLOCK_FPS = 1026,
  IDC_CFG_CHK_SHOW_SHADERS = 1027,
  IDC_CFG_BTN_CLOSE = 1030,
  IDC_CFG_BTN_EXIT_DESKTOP,
  IDC_CFG_PRESET_LOW,
  IDC_CFG_PRESET_HIGH,
  IDC_CFG_TIMER_ID = 1,
};

struct NdRenderToggle {
  const wchar_t* label;
  const wchar_t* ini_key;
  int id;
  uint32_t va;   // byte principal en .data del invitado
  uint32_t va2;  // flag companero (Sombras -> CSM), 0 si no aplica
  bool inverted; // true = 0 activo en motor Asura, 1 inactivo (Sombras Dinamicas)
};

// Orden = layout: 0..4 columna izquierda, 5..9 columna derecha.
static const NdRenderToggle kToggles[] = {
    {L"Sombras Dinámicas", L"DynamicShadows", IDC_CFG_CHK_SHADOW, 0x828E1FB7, 0x828E1FC5, true},
    {L"Oclusión Ambiental (SSAO)", L"AmbientOcclusionSSAO", IDC_CFG_CHK_SSAO, 0x828E1FC7, 0, false},
    {L"Motion Blur", L"MotionBlur", IDC_CFG_CHK_MBLUR, 0x82801CEF, 0, false},
    {L"Bloom HDR", L"BloomHDR", IDC_CFG_CHK_BLOOM, 0x828E1FC6, 0, false},
    {L"God Rays (Light Shafts)", L"LightShafts", IDC_CFG_CHK_LIGHTSHAFTS, 0x828E1FBF, 0, false},
    {L"Niebla Volumétrica", L"VolumetricFog", IDC_CFG_CHK_FOG, 0x828E1FC0, 0, false},
    {L"Post-Procesado (FSFX)", L"PostProcessingFSFX", IDC_CFG_CHK_FSFX, 0x828E1FC1, 0, false},
    {L"Partículas (PFX)", L"ParticlesPFX", IDC_CFG_CHK_PFX, 0x828E1FC8, 0, false},
    {L"Antialiasing (RMLAA)", L"AntialiasingRMLAA", IDC_CFG_CHK_RMLAA, 0x828E1FC9, 0, false},
    {L"Mostrar HUD", L"ShowHUD", IDC_CFG_CHK_HUD, 0x828E1FC3, 0, false},
};

// Flags normales para presets (las sombras se gestionan de forma invertida aparte)
static const uint32_t kPresetFlags[] = {
    0x82801CEF,  // MotionBlur
    0x828E1FC7,  // SSAO
    0x828E1FC6,  // HDRbloom
    0x828E1FBF,  // LightShafts
    0x828E1FC0,  // Fog
    0x828E1FC1,  // FSFX
    0x828E1FC9,  // RMLAA
    0x828E1FB9,  // Decals
    0x828E1FBA,  // ObjectDecals
    0x828E1FBB,  // Photons
    0x828E1FBC,  // Projectors
    0x828E1FBE,  // Coronas
    0x828E1FC8,  // Weather / Particulas
};
static constexpr uint32_t kHudVa = 0x828E1FC3;

// Estado del toggle F10/ESC: compartido entre WM_KEYDOWN (ventana con foco) y
// WM_TIMER (sondeo global). `*_wm_handled` impide que ambas vias cuenten la
// misma pulsacion como dos toggles.
static bool s_f10_wm_handled = false;
static bool s_esc_wm_handled = false;
static bool s_f10_prev = false;
static bool s_esc_prev = false;

// dwmapi/uxtheme se resuelven dinamicamente: cero cambios en CMakeLists.
static void NdSetDarkTitleBar(HWND hwnd) {
  using Fn = HRESULT(WINAPI*)(HWND, DWORD, LPCVOID, DWORD);
  static Fn s_fn = [] {
    HMODULE m = GetModuleHandleW(L"dwmapi.dll");
    if (m == nullptr) m = LoadLibraryW(L"dwmapi.dll");
    return m ? reinterpret_cast<Fn>(GetProcAddress(m, "DwmSetWindowAttribute")) : nullptr;
  }();
  if (s_fn != nullptr) {
    BOOL dark = TRUE;
    s_fn(hwnd, 20 /* DWMWA_USE_IMMERSIVE_DARK_MODE */, &dark, sizeof(dark));
  }
}

// Tema oscuro nativo para COMBOBOX (Win10 1809+). Si el sistema no lo tiene,
// cae al aspecto clasico con WM_CTLCOLORLISTBOX.
static void NdApplyDarkComboTheme(HWND ctrl) {
  using Fn = HRESULT(WINAPI*)(HWND, LPCWSTR, LPCWSTR);
  static Fn s_fn = [] {
    HMODULE m = GetModuleHandleW(L"uxtheme.dll");
    if (m == nullptr) m = LoadLibraryW(L"uxtheme.dll");
    return m ? reinterpret_cast<Fn>(GetProcAddress(m, "SetWindowTheme")) : nullptr;
  }();
  if (s_fn != nullptr && ctrl != nullptr) {
    s_fn(ctrl, L"DarkMode_CFD", nullptr);
  }
}

static void NdDisableTheme(HWND ctrl) {
  using Fn = HRESULT(WINAPI*)(HWND, LPCWSTR, LPCWSTR);
  static Fn s_fn = [] {
    HMODULE m = GetModuleHandleW(L"uxtheme.dll");
    if (m == nullptr) m = LoadLibraryW(L"uxtheme.dll");
    return m ? reinterpret_cast<Fn>(GetProcAddress(m, "SetWindowTheme")) : nullptr;
  }();
  if (s_fn != nullptr && ctrl != nullptr) {
    s_fn(ctrl, L"", L"");
  }
}

static bool NdToggleValue(const NdRenderToggle& t) {
  if (g_translate == nullptr) return true;
  bool raw = read_guest_u8(t.va) != 0;
  if (t.va2 != 0) raw = raw && (read_guest_u8(t.va2) != 0);
  return t.inverted ? !raw : raw;
}

static int s_window_mode = 0;

static std::wstring NdGetSettingsIniPath() {
  wchar_t exe_path[MAX_PATH] = {};
  GetModuleFileNameW(nullptr, exe_path, MAX_PATH);
  std::filesystem::path exe_dir = std::filesystem::path(exe_path).parent_path();
  std::filesystem::path ini = exe_dir / "config" / "settings.ini";
  if (std::filesystem::exists(ini)) {
    return ini.wstring();
  }
  std::filesystem::path parent_ini = exe_dir.parent_path() / "config" / "settings.ini";
  if (std::filesystem::exists(parent_ini)) {
    return parent_ini.wstring();
  }
  std::error_code ec;
  std::filesystem::create_directories(exe_dir / "config", ec);
  return ini.wstring();
}

void NdLoadSettingsFromIni() {
  std::wstring ini = NdGetSettingsIniPath();
  if (!std::filesystem::exists(ini)) return;

  wchar_t buf[64] = {};

  GetPrivateProfileStringW(L"Graphics", L"UnlockFramerate", L"false", buf, 64, ini.c_str());
  bool unlock = (_wcsicmp(buf, L"true") == 0 || wcstol(buf, nullptr, 10) == 1);

  GetPrivateProfileStringW(L"Graphics", L"ShowShaderCompilation", L"true", buf, 64, ini.c_str());
  bool show_shaders = (_wcsicmp(buf, L"false") != 0 && _wcsicmp(buf, L"0") != 0);
  g_show_shader_compilation.store(show_shaders, std::memory_order_relaxed);

  GetPrivateProfileStringW(L"Graphics", L"AdaptiveExposureOptimization", L"true", buf, 64, ini.c_str());
  bool luma_opt = (_wcsicmp(buf, L"false") != 0 && _wcsicmp(buf, L"0") != 0);
  g_opt_luma_resolve.store(luma_opt, std::memory_order_relaxed);

  int fps = GetPrivateProfileIntW(L"Graphics", L"TargetFPS", 30, ini.c_str());
  float target = static_cast<float>(fps);

  // Sobrescritura por variables de entorno si estan presentes
  const char* env_unlock = std::getenv("ND_UNLOCK_FPS");
  if (env_unlock != nullptr && env_unlock[0] == '1') {
    unlock = true;
    target = 0.0f;
  }
  const char* env_fps = std::getenv("ND_FPS_LIMIT");
  if (env_fps != nullptr) {
    const float v = static_cast<float>(std::atof(env_fps));
    if (v > 0.0f && v <= 1000.0f) {
      target = v;
      if (v > 30.0f) {
        unlock = true;
      }
    }
  }

  g_unlock_fps.store(unlock, std::memory_order_relaxed);
  g_fps_target.store(target, std::memory_order_relaxed);

  s_window_mode = GetPrivateProfileIntW(L"Graphics", L"WindowMode", 0, ini.c_str());

  for (const auto& t : kToggles) {
    GetPrivateProfileStringW(L"Graphics", t.ini_key, L"true", buf, 64, ini.c_str());
    bool val = (_wcsicmp(buf, L"false") != 0 && _wcsicmp(buf, L"0") != 0);
    const uint8_t byte_val = (t.inverted ? !val : val) ? 1 : 0;
    write_guest_u8(t.va, byte_val);
    if (t.va2 != 0) write_guest_u8(t.va2, byte_val);
  }
  // Bloquear de forma permanente e inviolable 0x828E1FBD a 1 para preservar el pase de sombras
  write_guest_u8(0x828E1FBD, 1);
}

void NdSaveSettingsToIni() {
  std::wstring ini = NdGetSettingsIniPath();
  bool unlock = g_unlock_fps.load(std::memory_order_relaxed);
  WritePrivateProfileStringW(L"Graphics", L"UnlockFramerate", unlock ? L"true" : L"false", ini.c_str());

  bool show_shaders = g_show_shader_compilation.load(std::memory_order_relaxed);
  WritePrivateProfileStringW(L"Graphics", L"ShowShaderCompilation", show_shaders ? L"true" : L"false", ini.c_str());

  bool luma_opt = g_opt_luma_resolve.load(std::memory_order_relaxed);
  WritePrivateProfileStringW(L"Graphics", L"AdaptiveExposureOptimization", luma_opt ? L"true" : L"false", ini.c_str());

  float target = g_fps_target.load(std::memory_order_relaxed);
  int fps_val = (target <= 0.0f) ? 0 : static_cast<int>(target);
  WritePrivateProfileStringW(L"Graphics", L"TargetFPS", std::to_wstring(fps_val).c_str(), ini.c_str());

  WritePrivateProfileStringW(L"Graphics", L"WindowMode", std::to_wstring(s_window_mode).c_str(), ini.c_str());

  for (const auto& t : kToggles) {
    bool val = NdToggleValue(t);
    WritePrivateProfileStringW(L"Graphics", t.ini_key, val ? L"true" : L"false", ini.c_str());
  }
}

static void NdSyncCheckboxesFromMemory(HWND hwnd) {
  for (const auto& t : kToggles) {
    HWND chk = GetDlgItem(hwnd, t.id);
    if (chk != nullptr) {
      SendMessageW(chk, BM_SETCHECK, NdToggleValue(t) ? BST_CHECKED : BST_UNCHECKED, 0);
    }
  }
  HWND chkLuma = GetDlgItem(hwnd, IDC_CFG_CHK_LUMA_OPT);
  if (chkLuma != nullptr) {
    SendMessageW(chkLuma, BM_SETCHECK, g_opt_luma_resolve.load() ? BST_CHECKED : BST_UNCHECKED, 0);
  }
  HWND chkUnlock = GetDlgItem(hwnd, IDC_CFG_CHK_UNLOCK_FPS);
  if (chkUnlock != nullptr) {
    SendMessageW(chkUnlock, BM_SETCHECK, g_unlock_fps.load() ? BST_CHECKED : BST_UNCHECKED, 0);
  }
  HWND chkShaders = GetDlgItem(hwnd, IDC_CFG_CHK_SHOW_SHADERS);
  if (chkShaders != nullptr) {
    SendMessageW(chkShaders, BM_SETCHECK, g_show_shader_compilation.load() ? BST_CHECKED : BST_UNCHECKED, 0);
  }
}

static void NdApplyPreset(HWND hwnd, bool high_quality) {
  int written = 0;
  // Sombras Dinamicas (logica invertida: 0 = activo en motor, 1 = desactivado)
  const uint8_t shadow_val = high_quality ? 0 : 1;
  if (write_guest_u8(0x828E1FB7, shadow_val)) ++written;
  if (write_guest_u8(0x828E1FC5, shadow_val)) ++written;

  // Render pass stage 2 protector (SIEMPRE 1)
  write_guest_u8(0x828E1FBD, 1);

  // Flags normales (1 = encendido, 0 = apagado)
  const uint8_t val = high_quality ? 1 : 0;
  for (uint32_t va : kPresetFlags) {
    if (write_guest_u8(va, val)) ++written;
  }
  write_guest_u8(kHudVa, 1);  // HUD siempre encendido en ambos perfiles
  NdSyncCheckboxesFromMemory(hwnd);
  NdSaveSettingsToIni();
  if (NdIsDiagEnabled()) {
    REXLOG_INFO("neverdead: [CONFIG-UI] Perfil '{}': {} flags aplicados (Sombras={}, Resto={}, HUD ON)",
                high_quality ? "Calidad Original" : "Bajo Consumo", written, shadow_val, val);
  }
}

static void NdApplyWindowMode(int idx) {
  s_window_mode = idx;
  HWND ghwnd = NdGetGameWindow();
  if (ghwnd == nullptr) return;
  if (idx == 0) {  // 720p
    SetWindowLongPtrW(ghwnd, GWL_STYLE, WS_OVERLAPPEDWINDOW | WS_VISIBLE);
    RECT rc = {0, 0, 1280, 720};
    AdjustWindowRect(&rc, WS_OVERLAPPEDWINDOW, FALSE);
    const int w = rc.right - rc.left;
    const int h = rc.bottom - rc.top;
    const int x = (GetSystemMetrics(SM_CXSCREEN) - w) / 2;
    const int y = (GetSystemMetrics(SM_CYSCREEN) - h) / 2;
    SetWindowPos(ghwnd, HWND_NOTOPMOST, x > 0 ? x : 0, y > 0 ? y : 0, w, h,
                 SWP_FRAMECHANGED | SWP_SHOWWINDOW);
  } else if (idx == 1) {  // 1080p
    SetWindowLongPtrW(ghwnd, GWL_STYLE, WS_OVERLAPPEDWINDOW | WS_VISIBLE);
    RECT rc = {0, 0, 1920, 1080};
    AdjustWindowRect(&rc, WS_OVERLAPPEDWINDOW, FALSE);
    const int w = rc.right - rc.left;
    const int h = rc.bottom - rc.top;
    const int x = (GetSystemMetrics(SM_CXSCREEN) - w) / 2;
    const int y = (GetSystemMetrics(SM_CYSCREEN) - h) / 2;
    SetWindowPos(ghwnd, HWND_NOTOPMOST, x > 0 ? x : 0, y > 0 ? y : 0, w, h,
                 SWP_FRAMECHANGED | SWP_SHOWWINDOW);
  } else {  // sin bordes
    SetWindowLongPtrW(ghwnd, GWL_STYLE, WS_POPUP | WS_VISIBLE);
    SetWindowPos(ghwnd, HWND_TOPMOST, 0, 0, GetSystemMetrics(SM_CXSCREEN),
                 GetSystemMetrics(SM_CYSCREEN), SWP_FRAMECHANGED | SWP_SHOWWINDOW);
  }
  NdSaveSettingsToIni();
}

static void NdHideConfigAndFocusGame(HWND hwnd) {
  ShowWindow(hwnd, SW_HIDE);
  if (HWND g = NdGetGameWindow()) {
    SetForegroundWindow(g);
    SetFocus(g);
  }
}

// Banner NEVERDETH: BMP de 520x44 con el fondo carbon exacto del menu (LoadImage
// no soporta PNG con alfa, y el BMP ya trae el color de fondo).
static void NdDrawBanner(HDC hdc) {
  static HBITMAP s_banner = nullptr;
  static bool s_loaded = false;
  if (!s_loaded) {
    s_loaded = true;
    // 1. Intentar cargar desde el recurso incrustado en el binario (ID 2)
    s_banner = reinterpret_cast<HBITMAP>(LoadImageW(
        GetModuleHandleW(nullptr), MAKEINTRESOURCEW(2), IMAGE_BITMAP, 0, 0,
        LR_CREATEDIBSECTION));
    // 2. Fallback: cargar relativo al ejecutable via GetModuleFileNameW
    if (s_banner == nullptr) {
      wchar_t exe_buf[MAX_PATH] = {};
      GetModuleFileNameW(nullptr, exe_buf, MAX_PATH);
      std::filesystem::path exe_dir = std::filesystem::path(exe_buf).parent_path();
      std::filesystem::path bmp = exe_dir / "docs" / "assets" / "neverdeth_logo_banner.bmp";
      if (!std::filesystem::exists(bmp)) {
        bmp = exe_dir.parent_path() / "docs" / "assets" / "neverdeth_logo_banner.bmp";
      }
      s_banner = reinterpret_cast<HBITMAP>(LoadImageW(
          nullptr, bmp.wstring().c_str(), IMAGE_BITMAP, 0, 0,
          LR_LOADFROMFILE | LR_CREATEDIBSECTION));
    }
  }
  if (s_banner == nullptr) return;
  HDC mem = CreateCompatibleDC(hdc);
  HGDIOBJ old = SelectObject(mem, s_banner);
  BitBlt(hdc, 0, 10, 520, 44, mem, 0, 0, SRCCOPY);
  SelectObject(mem, old);
  DeleteDC(mem);
}

static void NdCreateConfigControls(HWND hwnd) {
  HFONT hFont = CreateFontW(16, 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE, DEFAULT_CHARSET,
                            OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
                            DEFAULT_PITCH | FF_DONTCARE, L"Segoe UI");
  HFONT hFontBold = CreateFontW(16, 0, 0, 0, FW_SEMIBOLD, FALSE, FALSE, FALSE, DEFAULT_CHARSET,
                                OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
                                DEFAULT_PITCH | FF_DONTCARE, L"Segoe UI");
  auto set_font = [](HWND ctrl, HFONT f) {
    SendMessageW(ctrl, WM_SETFONT, reinterpret_cast<WPARAM>(f), TRUE);
  };

  // --- Fila 1: pantalla + FPS ---
  HWND lblScreen = CreateWindowW(L"STATIC", L"Pantalla:", WS_CHILD | WS_VISIBLE, 20, 65, 60,
                                 20, hwnd, nullptr, nullptr, nullptr);
  set_font(lblScreen, hFont);
  HWND cbWnd = CreateWindowExW(0, L"COMBOBOX", nullptr,
                               WS_CHILD | WS_VISIBLE | CBS_DROPDOWNLIST | WS_VSCROLL, 85, 61,
                               165, 200, hwnd, reinterpret_cast<HMENU>(IDC_CFG_COMBO_WND),
                               nullptr, nullptr);
  set_font(cbWnd, hFont);
  SendMessageW(cbWnd, CB_ADDSTRING, 0, reinterpret_cast<LPARAM>(L"Ventana 720p"));
  SendMessageW(cbWnd, CB_ADDSTRING, 0, reinterpret_cast<LPARAM>(L"Ventana 1080p"));
  SendMessageW(cbWnd, CB_ADDSTRING, 0, reinterpret_cast<LPARAM>(L"Sin Bordes (Fullscreen)"));
  SendMessageW(cbWnd, CB_SETCURSEL, s_window_mode, 0);
  NdApplyDarkComboTheme(cbWnd);

  HWND lblFps = CreateWindowW(L"STATIC", L"FPS:", WS_CHILD | WS_VISIBLE, 275, 65, 35, 20, hwnd,
                              nullptr, nullptr, nullptr);
  set_font(lblFps, hFont);
  HWND cbFps = CreateWindowExW(0, L"COMBOBOX", nullptr,
                               WS_CHILD | WS_VISIBLE | CBS_DROPDOWNLIST | WS_VSCROLL, 315, 61,
                               180, 200, hwnd, reinterpret_cast<HMENU>(IDC_CFG_COMBO_FPS),
                               nullptr, nullptr);
  set_font(cbFps, hFont);
  SendMessageW(cbFps, CB_ADDSTRING, 0, reinterpret_cast<LPARAM>(L"Ilimitado (Desbloqueado)"));
  SendMessageW(cbFps, CB_ADDSTRING, 0, reinterpret_cast<LPARAM>(L"60 FPS"));
  SendMessageW(cbFps, CB_ADDSTRING, 0, reinterpret_cast<LPARAM>(L"30 FPS"));
  int fps_sel = (g_fps_target.load() <= 0.0f) ? 0 : (g_fps_target.load() > 45.0f ? 1 : 2);
  SendMessageW(cbFps, CB_SETCURSEL, fps_sel, 0);
  NdApplyDarkComboTheme(cbFps);

  // --- Fila 2: perfiles de un clic ---
  HWND btnHigh = CreateWindowW(L"BUTTON", L"Perfil: Calidad Original",
                               WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON | BS_OWNERDRAW, 20, 99, 230, 28, hwnd,
                               reinterpret_cast<HMENU>(IDC_CFG_PRESET_HIGH), nullptr, nullptr);
  set_font(btnHigh, hFontBold);
  NdDisableTheme(btnHigh);
  HWND btnLow = CreateWindowW(L"BUTTON", L"Perfil: Bajo Consumo (Max FPS)",
                              WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON | BS_OWNERDRAW, 275, 99, 220, 28, hwnd,
                              reinterpret_cast<HMENU>(IDC_CFG_PRESET_LOW), nullptr, nullptr);
  set_font(btnLow, hFontBold);
  NdDisableTheme(btnLow);

  // --- 10 casillas, 2 columnas, logica positiva ---
  for (int i = 0; i < 10; ++i) {
    const auto& t = kToggles[i];
    const int x = (i < 5) ? 25 : 275;
    const int y = 138 + (i % 5) * 25;
    HWND chk = CreateWindowW(L"BUTTON", t.label, WS_CHILD | WS_VISIBLE | BS_AUTOCHECKBOX, x, y,
                             220, 22, hwnd, reinterpret_cast<HMENU>(static_cast<INT_PTR>(t.id)),
                             nullptr, nullptr);
    set_font(chk, hFont);
    NdDisableTheme(chk);
    SendMessageW(chk, BM_SETCHECK, NdToggleValue(t) ? BST_CHECKED : BST_UNCHECKED, 0);
  }

  // --- Fila extra: Opt. Exposicion Adaptativa + Desbloquear FPS ---
  HWND chkLuma = CreateWindowW(L"BUTTON", L"Opt. Exposición Adaptativa", WS_CHILD | WS_VISIBLE | BS_AUTOCHECKBOX,
                               25, 268, 220, 22, hwnd, reinterpret_cast<HMENU>(IDC_CFG_CHK_LUMA_OPT),
                               nullptr, nullptr);
  set_font(chkLuma, hFont);
  NdDisableTheme(chkLuma);
  SendMessageW(chkLuma, BM_SETCHECK, g_opt_luma_resolve.load() ? BST_CHECKED : BST_UNCHECKED, 0);

  HWND chkUnlock = CreateWindowW(L"BUTTON", L"Desbloquear FPS en Gameplay", WS_CHILD | WS_VISIBLE | BS_AUTOCHECKBOX,
                                 275, 268, 220, 22, hwnd, reinterpret_cast<HMENU>(IDC_CFG_CHK_UNLOCK_FPS),
                                 nullptr, nullptr);
  set_font(chkUnlock, hFont);
  NdDisableTheme(chkUnlock);
  SendMessageW(chkUnlock, BM_SETCHECK, g_unlock_fps.load() ? BST_CHECKED : BST_UNCHECKED, 0);

  HWND chkShaders = CreateWindowW(L"BUTTON", L"Mostrar Compilación de Shaders", WS_CHILD | WS_VISIBLE | BS_AUTOCHECKBOX,
                                  25, 295, 240, 22, hwnd, reinterpret_cast<HMENU>(IDC_CFG_CHK_SHOW_SHADERS),
                                  nullptr, nullptr);
  set_font(chkShaders, hFont);
  NdDisableTheme(chkShaders);
  SendMessageW(chkShaders, BM_SETCHECK, g_show_shader_compilation.load() ? BST_CHECKED : BST_UNCHECKED, 0);

  // --- Fila inferior ---
  HWND btnClose = CreateWindowW(L"BUTTON", L"Volver al Juego (F10 / ESC)",
                                WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON | BS_OWNERDRAW, 20, 328, 230, 32, hwnd,
                                reinterpret_cast<HMENU>(IDC_CFG_BTN_CLOSE), nullptr, nullptr);
  set_font(btnClose, hFontBold);
  NdDisableTheme(btnClose);
  HWND btnExit = CreateWindowW(L"BUTTON", L"Salir al Escritorio",
                               WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON | BS_OWNERDRAW, 275, 328, 220, 32, hwnd,
                               reinterpret_cast<HMENU>(IDC_CFG_BTN_EXIT_DESKTOP), nullptr,
                               nullptr);
  set_font(btnExit, hFontBold);
  NdDisableTheme(btnExit);
}

static LRESULT CALLBACK NdConfigWndProc(HWND hwnd, UINT msg, WPARAM wParam, LPARAM lParam) {
  static HBRUSH s_bg_brush = CreateSolidBrush(RGB(22, 22, 28));    // #16161C
  static HBRUSH s_ctrl_brush = CreateSolidBrush(RGB(34, 34, 44));  // #22222C

  switch (msg) {
    case WM_ERASEBKGND: {
      RECT rc{};
      GetClientRect(hwnd, &rc);
      FillRect(reinterpret_cast<HDC>(wParam), &rc, s_bg_brush);
      NdDrawBanner(reinterpret_cast<HDC>(wParam));
      return 1;
    }
    case WM_CTLCOLORDLG:
    case WM_CTLCOLORSTATIC:
    case WM_CTLCOLORBTN: {
      HDC hdc = reinterpret_cast<HDC>(wParam);
      SetTextColor(hdc, RGB(235, 235, 240));
      SetBkColor(hdc, RGB(22, 22, 28));
      return reinterpret_cast<LRESULT>(s_bg_brush);
    }
    case WM_CTLCOLORLISTBOX:
    case WM_CTLCOLOREDIT: {
      HDC hdc = reinterpret_cast<HDC>(wParam);
      SetTextColor(hdc, RGB(235, 235, 240));
      SetBkColor(hdc, RGB(34, 34, 44));
      return reinterpret_cast<LRESULT>(s_ctrl_brush);
    }
    case WM_KEYDOWN:
    case WM_SYSKEYDOWN: {
      if (wParam == VK_F10) {
        if (!s_f10_wm_handled) {
          s_f10_wm_handled = true;
          if (IsWindowVisible(hwnd)) {
            NdHideConfigAndFocusGame(hwnd);
          } else {
            NdSyncCheckboxesFromMemory(hwnd);
            ShowWindow(hwnd, SW_SHOW);
            SetForegroundWindow(hwnd);
          }
        }
        return 0;
      }
      if (wParam == VK_ESCAPE) {
        if (!s_esc_wm_handled) {
          s_esc_wm_handled = true;
          if (IsWindowVisible(hwnd)) NdHideConfigAndFocusGame(hwnd);
        }
        return 0;
      }
      break;
    }
    case WM_KEYUP:
    case WM_SYSKEYUP: {
      if (wParam == VK_F10) s_f10_wm_handled = false;
      if (wParam == VK_ESCAPE) s_esc_wm_handled = false;
      break;
    }
    case WM_TIMER: {
      if (wParam != IDC_CFG_TIMER_ID) break;
      const bool f10_down = (GetAsyncKeyState(VK_F10) & 0x8000) != 0;
      const bool esc_down = (GetAsyncKeyState(VK_ESCAPE) & 0x8000) != 0;
      const bool toggle_edge = f10_down && !s_f10_prev && !s_f10_wm_handled;
      const bool esc_edge = esc_down && !s_esc_prev && !s_esc_wm_handled;
      const bool requested = g_menu_toggle_request.exchange(false);
      s_f10_prev = f10_down;
      s_esc_prev = esc_down;
      if (toggle_edge || requested) {
        if (IsWindowVisible(hwnd)) {
          NdHideConfigAndFocusGame(hwnd);
        } else {
          NdSyncCheckboxesFromMemory(hwnd);
          HWND parent_hwnd = NdGetGameWindow();
          if (s_window_mode == 2 && parent_hwnd != nullptr && IsWindow(parent_hwnd)) {
            const int screen_w = GetSystemMetrics(SM_CXSCREEN);
            const int screen_h = GetSystemMetrics(SM_CYSCREEN);
            SetWindowPos(parent_hwnd, HWND_TOPMOST, 0, 0, screen_w, screen_h, SWP_SHOWWINDOW | SWP_FRAMECHANGED);
          }
          ShowWindow(hwnd, SW_SHOW);
          SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW);
          SetForegroundWindow(hwnd);
          SetFocus(hwnd);
        }
        return 0;
      }
      if (esc_edge && IsWindowVisible(hwnd)) {
        NdHideConfigAndFocusGame(hwnd);
        return 0;
      }
      return 0;
    }
    case WM_DRAWITEM: {
      const DRAWITEMSTRUCT* dis = reinterpret_cast<const DRAWITEMSTRUCT*>(lParam);
      if (dis == nullptr || dis->CtlType != ODT_BUTTON) break;
      const bool pressed = (dis->itemState & ODS_SELECTED) != 0;
      HBRUSH face = CreateSolidBrush(pressed ? RGB(48, 40, 32) : RGB(32, 29, 27));
      FillRect(dis->hDC, &dis->rcItem, face);
      DeleteObject(face);
      const bool is_exit = (dis->CtlID == IDC_CFG_BTN_EXIT_DESKTOP);
      HPEN pen = CreatePen(PS_SOLID, 1, is_exit ? RGB(180, 70, 60) : RGB(200, 138, 50));
      HGDIOBJ old_pen = SelectObject(dis->hDC, pen);
      HGDIOBJ old_brush = SelectObject(dis->hDC, GetStockObject(NULL_BRUSH));
      Rectangle(dis->hDC, dis->rcItem.left, dis->rcItem.top, dis->rcItem.right,
                dis->rcItem.bottom);
      SelectObject(dis->hDC, old_brush);
      SelectObject(dis->hDC, old_pen);
      DeleteObject(pen);
      wchar_t text[128] = {};
      GetWindowTextW(dis->hwndItem, text, 128);
      SetBkMode(dis->hDC, TRANSPARENT);
      SetTextColor(dis->hDC, RGB(242, 225, 195));
      HFONT font = reinterpret_cast<HFONT>(SendMessageW(dis->hwndItem, WM_GETFONT, 0, 0));
      HGDIOBJ old_font = font ? SelectObject(dis->hDC, font) : nullptr;
      RECT rc_text = dis->rcItem;
      DrawTextW(dis->hDC, text, -1, &rc_text, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
      if (old_font != nullptr) SelectObject(dis->hDC, old_font);
      return TRUE;
    }
    case WM_COMMAND: {
      const int id = LOWORD(wParam);
      const int code = HIWORD(wParam);
      if (id == IDC_CFG_BTN_CLOSE) {
        NdHideConfigAndFocusGame(hwnd);
        return 0;
      }
      if (id == IDC_CFG_BTN_EXIT_DESKTOP) {
        if (NdIsDiagEnabled()) {
          REXLOG_INFO("neverdead: [CLEAN-EXIT] Salir al Escritorio desde el menu");
        }
        TerminateProcess(GetCurrentProcess(), 0);
        return 0;
      }
      if (id == IDC_CFG_PRESET_LOW && code == BN_CLICKED) {
        NdApplyPreset(hwnd, false);
        return 0;
      }
      if (id == IDC_CFG_PRESET_HIGH && code == BN_CLICKED) {
        NdApplyPreset(hwnd, true);
        return 0;
      }
      if (id == IDC_CFG_CHK_LUMA_OPT && code == BN_CLICKED) {
        const bool on = SendMessageW(reinterpret_cast<HWND>(lParam), BM_GETCHECK, 0, 0) == BST_CHECKED;
        g_opt_luma_resolve.store(on, std::memory_order_relaxed);
        NdSaveSettingsToIni();
        return 0;
      }
      if (id == IDC_CFG_CHK_UNLOCK_FPS && code == BN_CLICKED) {
        const bool on = SendMessageW(reinterpret_cast<HWND>(lParam), BM_GETCHECK, 0, 0) == BST_CHECKED;
        g_unlock_fps.store(on, std::memory_order_relaxed);
        if (on) {
          float cur = g_fps_target.load(std::memory_order_relaxed);
          if (cur == 30.0f) {
            g_fps_target.store(60.0f, std::memory_order_relaxed);
          }
        } else {
          g_fps_target.store(30.0f, std::memory_order_relaxed);
        }
        HWND cb = GetDlgItem(hwnd, IDC_CFG_COMBO_FPS);
        if (cb) {
          float t = g_fps_target.load(std::memory_order_relaxed);
          int sel = (t == 60.0f) ? 1 : (t <= 0.0f && on ? 0 : 2);
          SendMessageW(cb, CB_SETCURSEL, sel, 0);
        }
        write_guest_be32(0x82B31FBC, float_to_be_bits(0.0f));
        NdNotifyAsuraFpsIntervalChanged();
        NdSaveSettingsToIni();
        return 0;
      }
      if (id == IDC_CFG_COMBO_FPS && code == CBN_SELCHANGE) {
        HWND cb = reinterpret_cast<HWND>(lParam);
        const int idx = static_cast<int>(SendMessageW(cb, CB_GETCURSEL, 0, 0));
        // idx 0 = Ilimitado, idx 1 = 60 FPS, idx 2 = 30 FPS
        const float target = (idx == 1) ? 60.0f : (idx == 2) ? 30.0f : 0.0f;
        const bool unlock = (idx != 2);
        g_fps_target.store(target, std::memory_order_relaxed);
        g_unlock_fps.store(unlock, std::memory_order_relaxed);
        HWND chkUnlock = GetDlgItem(hwnd, IDC_CFG_CHK_UNLOCK_FPS);
        if (chkUnlock) SendMessageW(chkUnlock, BM_SETCHECK, unlock ? BST_CHECKED : BST_UNCHECKED, 0);
        write_guest_be32(0x82B31FBC, float_to_be_bits(0.0f));
        NdNotifyAsuraFpsIntervalChanged();
        NdSaveSettingsToIni();
        if (NdIsDiagEnabled()) {
          REXLOG_INFO("neverdead: [CONFIG-UI] Limite de framerate: {:.1f} FPS (idx={}, unlock={})",
                      target, idx, unlock);
        }
        return 0;
      }
      if (id == IDC_CFG_COMBO_WND && code == CBN_SELCHANGE) {
        HWND cb = reinterpret_cast<HWND>(lParam);
        NdApplyWindowMode(static_cast<int>(SendMessageW(cb, CB_GETCURSEL, 0, 0)));
        return 0;
      }
      if (id == IDC_CFG_CHK_SHOW_SHADERS && code == BN_CLICKED) {
        const bool on =
            SendMessageW(reinterpret_cast<HWND>(lParam), BM_GETCHECK, 0, 0) == BST_CHECKED;
        g_show_shader_compilation.store(on, std::memory_order_relaxed);
        if (!on && g_osd_hwnd != nullptr) {
          ShowWindow(g_osd_hwnd, SW_HIDE);
        }
        NdSaveSettingsToIni();
        return 0;
      }
      // Casillas con soporte de polaridad booleana normal e invertida
      for (const auto& t : kToggles) {
        if (id == t.id && code == BN_CLICKED) {
          const bool on =
              SendMessageW(reinterpret_cast<HWND>(lParam), BM_GETCHECK, 0, 0) == BST_CHECKED;
          const uint8_t byte_val = (t.inverted ? !on : on) ? 1 : 0;
          write_guest_u8(t.va, byte_val);
          if (t.va2 != 0) write_guest_u8(t.va2, byte_val);
          write_guest_u8(0x828E1FBD, 1);
          NdSaveSettingsToIni();
          if (NdIsDiagEnabled()) {
            REXLOG_INFO("neverdead: [CONFIG-UI] toggle id={} (va=0x{:08X}) = {} (byte=0x{:02X})",
                        t.id, t.va, on ? 1 : 0, byte_val);
          }
          return 0;
        }
      }
      break;
    }
    case WM_CLOSE: {
      NdHideConfigAndFocusGame(hwnd);
      return 0;
    }
    case WM_DESTROY: {
      KillTimer(hwnd, IDC_CFG_TIMER_ID);
      g_config_hwnd = nullptr;
      g_config_ui_open.store(false, std::memory_order_release);
      PostQuitMessage(0);
      return 0;
    }
    default:
      break;
  }
  return DefWindowProcW(hwnd, msg, wParam, lParam);
}

static void NdRunConfigUIDialog() {
  HINSTANCE hInst = GetModuleHandle(nullptr);
  WNDCLASSEXW wc{};
  wc.cbSize = sizeof(wc);
  wc.style = CS_HREDRAW | CS_VREDRAW;
  wc.lpfnWndProc = NdConfigWndProc;
  wc.hInstance = hInst;
  wc.hCursor = LoadCursor(nullptr, IDC_ARROW);
  wc.hbrBackground = nullptr;  // se pinta en WM_ERASEBKGND
  wc.lpszClassName = L"NeverDeadConfigClass";
  RegisterClassExW(&wc);

  RECT rc = {0, 0, 520, 375};
  const DWORD dwExStyle = WS_EX_TOPMOST | WS_EX_TOOLWINDOW;
  AdjustWindowRectEx(&rc, WS_POPUP | WS_CAPTION | WS_SYSMENU, FALSE, dwExStyle);
  const int w = rc.right - rc.left;
  const int h = rc.bottom - rc.top;
  const int x = (GetSystemMetrics(SM_CXSCREEN) - w) / 2;
  const int y = (GetSystemMetrics(SM_CYSCREEN) - h) / 2;

  HWND parent_hwnd = NdGetGameWindow();
  HWND hwnd = CreateWindowExW(dwExStyle, L"NeverDeadConfigClass",
                              L"Neverdeth (v0.1.0-alpha) — Settings (F10)",
                              WS_POPUP | WS_CAPTION | WS_SYSMENU, x, y, w, h, parent_hwnd, nullptr,
                              hInst, nullptr);
  if (hwnd == nullptr) {
    g_config_ui_open.store(false, std::memory_order_release);
    return;
  }
  g_config_hwnd = hwnd;
  NdSetDarkTitleBar(hwnd);
  // Icono NEVERDETH en la barra de titulo y en Alt+Tab (embebido ID 1 o fallback relativo)
  HICON ico = LoadIconW(GetModuleHandleW(nullptr), MAKEINTRESOURCEW(1));
  if (ico == nullptr) {
    wchar_t exe_buf[MAX_PATH] = {};
    GetModuleFileNameW(nullptr, exe_buf, MAX_PATH);
    std::filesystem::path exe_dir = std::filesystem::path(exe_buf).parent_path();
    std::filesystem::path ico_path = exe_dir / "config" / "neverdeth.ico";
    if (!std::filesystem::exists(ico_path)) {
      ico_path = exe_dir.parent_path() / "config" / "neverdeth.ico";
    }
    ico = reinterpret_cast<HICON>(LoadImageW(nullptr, ico_path.wstring().c_str(),
                                             IMAGE_ICON, 32, 32, LR_LOADFROMFILE));
  }
  if (ico != nullptr) {
    SendMessageW(hwnd, WM_SETICON, ICON_SMALL, reinterpret_cast<LPARAM>(ico));
    SendMessageW(hwnd, WM_SETICON, ICON_BIG, reinterpret_cast<LPARAM>(ico));
  }
  NdCreateConfigControls(hwnd);
  SetTimer(hwnd, IDC_CFG_TIMER_ID, 16, nullptr);

  if (s_window_mode == 2 && parent_hwnd != nullptr && IsWindow(parent_hwnd)) {
    const int screen_w = GetSystemMetrics(SM_CXSCREEN);
    const int screen_h = GetSystemMetrics(SM_CYSCREEN);
    SetWindowPos(parent_hwnd, HWND_TOPMOST, 0, 0, screen_w, screen_h,
                 SWP_SHOWWINDOW | SWP_FRAMECHANGED);
    SetWindowPos(hwnd, HWND_TOPMOST, x, y, w, h, SWP_SHOWWINDOW);
  } else {
    ShowWindow(hwnd, SW_SHOW);
  }
  UpdateWindow(hwnd);
  SetForegroundWindow(hwnd);
  SetFocus(hwnd);

  MSG msg{};
  while (GetMessageW(&msg, nullptr, 0, 0) > 0) {
    TranslateMessage(&msg);
    DispatchMessageW(&msg);
  }
  g_config_ui_open.store(false, std::memory_order_release);
}

void NdOpenConfigUI() {
  // Si la ventana ya existe (oculta), reabrirla sin crear otro hilo.
  HWND existing = g_config_hwnd;
  if (g_config_ui_open.load(std::memory_order_acquire) && existing != nullptr &&
      IsWindow(existing)) {
    if (!IsWindowVisible(existing)) {
      NdSyncCheckboxesFromMemory(existing);
      HWND parent_hwnd = NdGetGameWindow();
      if (s_window_mode == 2 && parent_hwnd != nullptr && IsWindow(parent_hwnd)) {
        const int screen_w = GetSystemMetrics(SM_CXSCREEN);
        const int screen_h = GetSystemMetrics(SM_CYSCREEN);
        SetWindowPos(parent_hwnd, HWND_TOPMOST, 0, 0, screen_w, screen_h,
                     SWP_SHOWWINDOW | SWP_FRAMECHANGED);
      }
      ShowWindow(existing, SW_SHOW);
      SetWindowPos(existing, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW);
    }
    SetForegroundWindow(existing);
    SetFocus(existing);
    return;
  }
  if (g_config_ui_open.exchange(true, std::memory_order_acq_rel)) {
    return;
  }
  std::thread ui_thread([]() { NdRunConfigUIDialog(); });
  ui_thread.detach();
}
static void NdSyncFpsClampMemory(float target) {
  (void)target;
  // La variable de clamp del invitado (0x82B31FBC) se deja SIEMPRE en 0.0f
  // (desbloqueado). Con cualquier otro valor el motor Asura se queda esperando
  // vblanks que nunca llegan tras el primer VdSwap: la ventana queda en negro
  // aunque se sigan presentando frames. El limite real lo aplica el pacer host
  // (NdApplyFramePacer) y el menu F10, sin tocar esta variable.
  uint8_t* p = nd_translate_va(0x82B31FBC);
  if (p != nullptr) {
    write_guest_be32(0x82B31FBC, float_to_be_bits(0.0f));
  }
}

static void NdNotifyAsuraFpsIntervalChanged() {
  uint32_t dev = g_guest_device_va.load(std::memory_order_relaxed);
  if (dev == 0) return;

  float target = g_fps_target.load(std::memory_order_relaxed);
  bool in_3d = g_in_3d_gameplay.load(std::memory_order_relaxed);
  bool unlocked = g_unlock_fps.load(std::memory_order_relaxed) && in_3d;

  uint32_t interval = 2; // 30 FPS
  if (unlocked) {
    if (target <= 0.0f) {
      interval = 0; // Ilimitado
    } else if (target > 35.0f) {
      interval = 1; // 60 FPS
    }
  }

  // 14000(dev) guarda el intervalo deseado (0, 1 o 2) para sub_826B78D8
  write_guest_be32(dev + 14000, interval);
  // Al escribir 0 en 24584(dev), sub_826BE8C0 (hilo de renderizado) invocara
  // forzosamente sub_826B78D8 en el proximo frame para reconfigurar el registro
  // 0x57C de presentacion PM4 en la GPU.
  write_guest_be32(dev + 24584, 0);

  if (NdIsDiagEnabled()) {
    REXLOG_INFO("neverdead: [FPS-SYNC] Device=0x{:08X} -> interval={} (target={:.1f}, 3d={}, unlock={}) forzado en sub_826BE8C0",
                dev, interval, target, in_3d, unlocked);
  }
}

static void NdInitFpsMode() {
  if (g_fps_mode_initialized.exchange(true)) {
    return;
  }
  NdLoadSettingsFromIni();
  float target = g_fps_target.load(std::memory_order_relaxed);
  const char* env_unlock = std::getenv("ND_UNLOCK_FPS");
  if (env_unlock != nullptr && env_unlock[0] == '1') {
    g_unlock_fps.store(true, std::memory_order_relaxed);
    target = 0.0f;
  }
  const char* env = std::getenv("ND_FPS_LIMIT");
  if (env != nullptr) {
    const float v = static_cast<float>(std::atof(env));
    if (v > 0.0f && v <= 1000.0f) {
      target = v;
      if (v > 30.0f) {
        g_unlock_fps.store(true, std::memory_order_relaxed);
      }
    }
  }
  g_fps_target.store(target, std::memory_order_relaxed);
  NdSyncFpsClampMemory(target);
  NdNotifyAsuraFpsIntervalChanged();
  REXLOG_INFO("neverdead: [FPS] Inicializado a {:.1f} FPS (0.0 = desbloqueado, unlock={})",
              target, g_unlock_fps.load());
}
static void NdCheckFpsToggleKey() {
  NdInitFpsMode();
  static std::atomic<bool> s_f8_was_down{false};
  SHORT f8_state = GetAsyncKeyState(0x77); // VK_F8
  bool f8_down = (f8_state & 0x8000) != 0;
  if (f8_down && !s_f8_was_down.exchange(true)) {
    bool cur_unlock = g_unlock_fps.load(std::memory_order_relaxed);
    bool next_unlock = !cur_unlock;
    g_unlock_fps.store(next_unlock, std::memory_order_relaxed);
    float next = next_unlock ? 0.0f : 30.0f;
    g_fps_target.store(next, std::memory_order_relaxed);
    NdSyncFpsClampMemory(next);
    NdNotifyAsuraFpsIntervalChanged();
    NdSaveSettingsToIni();
    REXLOG_INFO("neverdead: [FPS-TOGGLE] *** F8 PULSADO: Modo alternado a {} (0x82B31FBC sincronizado) ***",
                next_unlock ? "Desbloqueado (72+ FPS / 0.0f)" : "30 FPS (30.0f)");
  } else if (!f8_down && s_f8_was_down.load(std::memory_order_relaxed)) {
    s_f8_was_down.store(false, std::memory_order_relaxed);
  }
}

void NdForceIntervalOneHook(PPCRegister& r11) {
  NdInitFpsMode();
  float target = g_fps_target.load(std::memory_order_relaxed);
  bool in_3d = g_in_3d_gameplay.load(std::memory_order_relaxed);
  bool unlocked = g_unlock_fps.load(std::memory_order_relaxed) && in_3d;

  if (!unlocked || target == 30.0f) {
    r11.u64 = 2; // 30 FPS -> intervalo 2 VBlanks (33.33 ms)
  } else if (target <= 0.0f) {
    r11.u64 = 0; // Ilimitado -> intervalo 0 VBlanks (flip inmediato)
  } else {
    // 60 FPS -> intervalo 1 VBlank (16.67 ms) que previene la perdida de lock del hilo VBlank
    r11.u64 = 1;
  }
  NdSyncFpsClampMemory(0.0f);
}

void NdAdjustRenderTimeoutHook(PPCRegister& r11) {
  const float target = g_fps_target.load(std::memory_order_relaxed);
  bool in_3d = g_in_3d_gameplay.load(std::memory_order_relaxed);
  bool unlocked = g_unlock_fps.load(std::memory_order_relaxed) && in_3d;

  if (!unlocked || target == 30.0f) {
    r11.s64 = -333333; // 33.33 ms (30 FPS)
  } else if (target <= 0.0f) {
    r11.s64 = -10000;  // 1.0 ms (Ilimitado)
  } else {
    r11.s64 = -166667; // 16.67 ms (60 FPS)
  }
}

void NdSetFpsLimitHook(PPCRegister& f0) {
  f0.f64 = 0.0;
  f0.f32 = 0.0f;
  NdSyncFpsClampMemory(0.0f);
}

void NdAutoAdvanceInput(PPCContext& ctx, uint8_t* base, uint32_t guest_state) {
  // Solo aplicar para player 0 (o broadcast si r3==254)
  if (ctx.r3.u32 != 0 && ctx.r3.u32 != 254 && ctx.r3.u32 != 1167) {
    return;
  }
  // Si ya estamos en el hospital 3D (confirmado por NdRingProbe con draws>=400), desactivar
  if (g_hospital_reached.load(std::memory_order_relaxed)) {
    return;
  }

  static const bool s_enabled = []() {
    const char* env = std::getenv("ND_AUTO_NAV");
    if (env != nullptr) {
      return (std::atoi(env) == 1);
    }
    return false;
  }();

  static const auto s_t0 = std::chrono::steady_clock::now();
  const auto now = std::chrono::steady_clock::now();
  const auto elapsed_ms = std::chrono::duration_cast<std::chrono::milliseconds>(now - s_t0).count();

  uint16_t inject_btn = 0;
  const char* btn_name = "NONE";

  // Saltar CESA.bik inmediatamente en el arranque (< 2.5s) incluso si ND_AUTO_NAV no esta activo
  static std::atomic<bool> s_startup_cesa_skipped{false};
  if (!s_startup_cesa_skipped.load(std::memory_order_relaxed) && elapsed_ms < 2500) {
    inject_btn = 0x0010; // XINPUT_GAMEPAD_START
    btn_name = "START (CESA SKIP)";
    if (elapsed_ms > 400) {
      s_startup_cesa_skipped.store(true, std::memory_order_relaxed);
    }
  } else if (!s_enabled) {
    return;
  } else {
    // Si ND_AUTO_NAV esta activo, continuar la navegacion automatica al 3D:
    const uint64_t cycle_ms = 1200;
    const uint64_t phase = elapsed_ms % cycle_ms;
    const bool button_down = (phase < 400);

    if (button_down) {
      if (elapsed_ms < 16000) {
        inject_btn = 0x0010; // XINPUT_GAMEPAD_START
        btn_name = "START";
      } else {
        inject_btn = 0x1000; // XINPUT_GAMEPAD_A
        btn_name = "A";
      }
    }
  }

  static bool s_was_down = false;
  if (inject_btn != 0 && !s_was_down) {
    s_was_down = true;
    REXLOG_INFO("neverdead: [AUTO-NAV] t={:.1f}s -> Pulsando {}", elapsed_ms / 1000.0, btn_name);
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

    // Forzar codigo de retorno 0 (ERROR_SUCCESS)
    ctx.r3.s64 = 0;
  }
}

//=============================================================================
// Fallback automatico CESA.bik -> Front_End.bik
//=============================================================================
void NdEnsureCesaBik(const std::filesystem::path& target_assets_dir) {
  nd::extractor::EnsureCesaBik(target_assets_dir);
}

void NdEnsureAssetJunctionsOrLinks(const std::filesystem::path& base_dir, const std::filesystem::path& assets_dir) {
  nd::extractor::NdEnsureAssetJunctionsOrLinks(base_dir, assets_dir);
}

//=============================================================================
// Asistente Win32 de Primer Inicio (Game Data Setup) - 100% C++20 Nativo
//=============================================================================
void NdRunFirstLaunchSetupWizard(const std::filesystem::path& target_assets_dir) {
  HINSTANCE hInst = GetModuleHandle(nullptr);
  WNDCLASSEXW wc{};
  wc.cbSize = sizeof(wc);
  wc.style = CS_HREDRAW | CS_VREDRAW;
  wc.lpfnWndProc = [](HWND hwnd, UINT msg, WPARAM wParam, LPARAM lParam) -> LRESULT {
    static HBRUSH s_bg_brush = CreateSolidBrush(RGB(22, 22, 28));
    static std::filesystem::path s_target_assets;

    switch (msg) {
      case WM_CREATE: {
        auto* cs = reinterpret_cast<CREATESTRUCTW*>(lParam);
        if (cs && cs->lpCreateParams) {
          s_target_assets = *reinterpret_cast<std::filesystem::path*>(cs->lpCreateParams);
        }
        NdSetDarkTitleBar(hwnd);

        HFONT hTitleFont = CreateFontW(20, 0, 0, 0, FW_BOLD, FALSE, FALSE, FALSE, DEFAULT_CHARSET,
                                      OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
                                      DEFAULT_PITCH | FF_DONTCARE, L"Segoe UI");
        HFONT hFont = CreateFontW(15, 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE, DEFAULT_CHARSET,
                                  OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
                                  DEFAULT_PITCH | FF_DONTCARE, L"Segoe UI");
        HFONT hBtnFont = CreateFontW(15, 0, 0, 0, FW_SEMIBOLD, FALSE, FALSE, FALSE, DEFAULT_CHARSET,
                                     OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
                                     DEFAULT_PITCH | FF_DONTCARE, L"Segoe UI");

        HWND lblTitle = CreateWindowW(L"STATIC", L"Neverdeth (v0.1.0-alpha) — Game Data Setup",
                                      WS_CHILD | WS_VISIBLE, 20, 15, 480, 26, hwnd, nullptr, nullptr, nullptr);
        SendMessageW(lblTitle, WM_SETFONT, reinterpret_cast<WPARAM>(hTitleFont), TRUE);

        HWND lblInfo = CreateWindowW(L"STATIC",
                                     L"No game assets were found in 'assets/'.\n"
                                     L"To play Neverdeth, please provide your legal copy of the Xbox 360 release (Title ID 4B4E081F).\n"
                                     L"Select one of the installation sources below:",
                                     WS_CHILD | WS_VISIBLE, 20, 48, 480, 56, hwnd, nullptr, nullptr, nullptr);
        SendMessageW(lblInfo, WM_SETFONT, reinterpret_cast<WPARAM>(hFont), TRUE);

        HWND btnIso = CreateWindowW(L"BUTTON", L"Select ISO Image (.iso) or GOD Container (.data)",
                                    WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON | BS_OWNERDRAW,
                                    20, 115, 480, 36, hwnd, reinterpret_cast<HMENU>(2001), nullptr, nullptr);
        SendMessageW(btnIso, WM_SETFONT, reinterpret_cast<WPARAM>(hBtnFont), TRUE);
        NdDisableTheme(btnIso);

        HWND btnFolder = CreateWindowW(L"BUTTON", L"Select Extracted Game Folder",
                                       WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON | BS_OWNERDRAW,
                                       20, 160, 480, 36, hwnd, reinterpret_cast<HMENU>(2002), nullptr, nullptr);
        SendMessageW(btnFolder, WM_SETFONT, reinterpret_cast<WPARAM>(hBtnFont), TRUE);
        NdDisableTheme(btnFolder);

        HWND btnExit = CreateWindowW(L"BUTTON", L"Exit",
                                     WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON | BS_OWNERDRAW,
                                     20, 205, 480, 32, hwnd, reinterpret_cast<HMENU>(2003), nullptr, nullptr);
        SendMessageW(btnExit, WM_SETFONT, reinterpret_cast<WPARAM>(hBtnFont), TRUE);
        NdDisableTheme(btnExit);

        HWND lblStatus = CreateWindowW(L"STATIC", L"", WS_CHILD, 20, 248, 480, 24,
                                       hwnd, reinterpret_cast<HMENU>(2004), nullptr, nullptr);
        SendMessageW(lblStatus, WM_SETFONT, reinterpret_cast<WPARAM>(hFont), TRUE);
        return 0;
      }
      case WM_ERASEBKGND: {
        RECT rc{};
        GetClientRect(hwnd, &rc);
        FillRect(reinterpret_cast<HDC>(wParam), &rc, s_bg_brush);
        return 1;
      }
      case WM_CTLCOLORSTATIC: {
        HDC hdc = reinterpret_cast<HDC>(wParam);
        SetTextColor(hdc, RGB(235, 235, 240));
        SetBkColor(hdc, RGB(22, 22, 28));
        return reinterpret_cast<LRESULT>(s_bg_brush);
      }
      case WM_DRAWITEM: {
        const DRAWITEMSTRUCT* dis = reinterpret_cast<const DRAWITEMSTRUCT*>(lParam);
        if (dis == nullptr || dis->CtlType != ODT_BUTTON) break;
        const bool pressed = (dis->itemState & ODS_SELECTED) != 0;
        HBRUSH face = CreateSolidBrush(pressed ? RGB(48, 40, 32) : RGB(32, 29, 27));
        FillRect(dis->hDC, &dis->rcItem, face);
        DeleteObject(face);
        const bool is_exit = (dis->CtlID == 2003);
        HPEN pen = CreatePen(PS_SOLID, 1, is_exit ? RGB(180, 70, 60) : RGB(200, 138, 50));
        HGDIOBJ old_pen = SelectObject(dis->hDC, pen);
        HGDIOBJ old_brush = SelectObject(dis->hDC, GetStockObject(NULL_BRUSH));
        Rectangle(dis->hDC, dis->rcItem.left, dis->rcItem.top, dis->rcItem.right, dis->rcItem.bottom);
        SelectObject(dis->hDC, old_brush);
        SelectObject(dis->hDC, old_pen);
        DeleteObject(pen);
        wchar_t text[128] = {};
        GetWindowTextW(dis->hwndItem, text, 128);
        SetBkMode(dis->hDC, TRANSPARENT);
        SetTextColor(dis->hDC, is_exit ? RGB(240, 160, 150) : RGB(242, 225, 195));
        HFONT font = reinterpret_cast<HFONT>(SendMessageW(dis->hwndItem, WM_GETFONT, 0, 0));
        HGDIOBJ old_font = font ? SelectObject(dis->hDC, font) : nullptr;
        RECT rc_text = dis->rcItem;
        DrawTextW(dis->hDC, text, -1, &rc_text, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        if (old_font != nullptr) SelectObject(dis->hDC, old_font);
        return TRUE;
      }
      case WM_COMMAND: {
        const int id = LOWORD(wParam);
        if (id == 2003) { // Exit
          ExitProcess(0);
          return 0;
        }

        auto run_extraction_async = [&](const std::filesystem::path& input_path) {
          HWND btn1 = GetDlgItem(hwnd, 2001);
          HWND btn2 = GetDlgItem(hwnd, 2002);
          HWND btn3 = GetDlgItem(hwnd, 2003);
          HWND lblStat = GetDlgItem(hwnd, 2004);
          if (btn1) EnableWindow(btn1, FALSE);
          if (btn2) EnableWindow(btn2, FALSE);
          if (btn3) EnableWindow(btn3, FALSE);
          if (lblStat) {
            SetWindowTextW(lblStat, L"Preparing extraction... Please wait.");
            ShowWindow(lblStat, SW_SHOW);
          }

          std::atomic<bool> worker_done{false};
          std::atomic<bool> worker_ok{false};
          std::wstring status_str = L"Starting extraction...";
          std::mutex status_mtx;

          std::thread worker([&]() {
            bool ok = nd::extractor::ExtractAll(input_path, s_target_assets,
              [&](const std::string& rel_file, uint64_t done_bytes, uint64_t total_bytes, float frac) {
                int pct = static_cast<int>(frac * 100.0f);
                if (pct > 100) pct = 100;
                std::wstring msg;
                if (rel_file == "Building shader cache...") {
                  msg = L"Building shader cache...";
                } else if (rel_file.rfind("Insufficient disk space", 0) == 0) {
                  msg = L"Error: Insufficient disk space (< 7 GB available)";
                } else {
                  std::string fname = std::filesystem::path(rel_file).filename().string();
                  msg = L"Extracting (" + std::to_wstring(pct) + L"%): ";
                  msg.append(fname.begin(), fname.end());
                }
                {
                  std::lock_guard<std::mutex> lk(status_mtx);
                  status_str = msg;
                }
                return true;
              });
            nd::extractor::EnsureCesaBik(s_target_assets);
            worker_ok.store(ok);
            worker_done.store(true);
          });

          // Bombeo de mensajes Win32 para no congelar la ventana durante la extraccion
          while (!worker_done.load(std::memory_order_relaxed)) {
            MSG pump_msg{};
            while (PeekMessageW(&pump_msg, nullptr, 0, 0, PM_REMOVE)) {
              TranslateMessage(&pump_msg);
              DispatchMessageW(&pump_msg);
            }
            if (lblStat) {
              std::lock_guard<std::mutex> lk(status_mtx);
              SetWindowTextW(lblStat, status_str.c_str());
            }
            Sleep(20);
          }

          if (worker.joinable()) {
            worker.join();
          }

          std::error_code ec;
          if (std::filesystem::exists(s_target_assets / "default.xex", ec)) {
            MessageBoxW(hwnd, L"Game data extracted and verified successfully!\nStarting Neverdeth...",
                        L"Neverdeth Setup Complete", MB_OK | MB_ICONINFORMATION);
            DestroyWindow(hwnd);
          } else {
            if (btn1) EnableWindow(btn1, TRUE);
            if (btn2) EnableWindow(btn2, TRUE);
            if (btn3) EnableWindow(btn3, TRUE);
            if (lblStat) SetWindowTextW(lblStat, L"Extraction failed. Please verify the source.");
            MessageBoxW(hwnd,
                        L"Extraction failed or default.xex was not found in assets/.\n"
                        L"Please ensure the file is an unencrypted Xbox 360 copy of NeverDead / Neverdeth (4B4E081F).",
                        L"Setup Incomplete", MB_OK | MB_ICONWARNING);
          }
        };

        if (id == 2001) { // Select ISO or GOD
          HMODULE hComDlg = LoadLibraryW(L"comdlg32.dll");
          if (hComDlg) {
            using Fn = BOOL(WINAPI*)(LPOPENFILENAMEW);
            auto fn = reinterpret_cast<Fn>(GetProcAddress(hComDlg, "GetOpenFileNameW"));
            if (fn) {
              wchar_t file_buf[MAX_PATH] = {};
              OPENFILENAMEW ofn{};
              ofn.lStructSize = sizeof(ofn);
              ofn.hwndOwner = hwnd;
              ofn.lpstrFilter = L"Xbox 360 Disc Image or GOD Container (*.iso;*.data;Data0000;*)\0*.iso;*.data;Data0000;*\0All Files (*.*)\0*.*\0";
              ofn.lpstrFile = file_buf;
              ofn.nMaxFile = MAX_PATH;
              ofn.lpstrTitle = L"Select Xbox 360 ISO Image or GOD Container (NeverDead 4B4E081F)";
              ofn.Flags = OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST;
              if (fn(&ofn)) {
                run_extraction_async(std::filesystem::path(file_buf));
              }
            }
          }
          return 0;
        }
        if (id == 2002) { // Select Extracted Game Folder
          HMODULE hShell = LoadLibraryW(L"shell32.dll");
          if (hShell) {
            using BrowseFn = PIDLIST_ABSOLUTE(WINAPI*)(LPBROWSEINFOW);
            using PathFn = BOOL(WINAPI*)(PCIDLIST_ABSOLUTE, LPWSTR);
            auto fnBrowse = reinterpret_cast<BrowseFn>(GetProcAddress(hShell, "SHBrowseForFolderW"));
            auto fnPath = reinterpret_cast<PathFn>(GetProcAddress(hShell, "SHGetPathFromIDListW"));
            if (fnBrowse && fnPath) {
              BROWSEINFOW bi{};
              bi.hwndOwner = hwnd;
              bi.lpszTitle = L"Select folder containing extracted Xbox 360 assets (default.xex)";
              bi.ulFlags = BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE;
              PIDLIST_ABSOLUTE pidl = fnBrowse(&bi);
              if (pidl) {
                wchar_t folder_buf[MAX_PATH] = {};
                if (fnPath(pidl, folder_buf)) {
                  run_extraction_async(std::filesystem::path(folder_buf));
                }
              }
            }
          }
          return 0;
        }
        break;
      }
      case WM_CLOSE: {
        ExitProcess(0);
        return 0;
      }
      case WM_DESTROY: {
        PostQuitMessage(0);
        return 0;
      }
    }
    return DefWindowProcW(hwnd, msg, wParam, lParam);
  };
  wc.hInstance = hInst;
  wc.hCursor = LoadCursor(nullptr, IDC_ARROW);
  wc.lpszClassName = L"NeverDeadSetupClass";
  RegisterClassExW(&wc);

  RECT rc = {0, 0, 520, 285};
  AdjustWindowRectEx(&rc, WS_POPUP | WS_CAPTION | WS_SYSMENU, FALSE, 0);
  const int w = rc.right - rc.left;
  const int h = rc.bottom - rc.top;
  const int x = (GetSystemMetrics(SM_CXSCREEN) - w) / 2;
  const int y = (GetSystemMetrics(SM_CYSCREEN) - h) / 2;

  std::filesystem::path target_copy = target_assets_dir;
  HWND hwnd = CreateWindowExW(0, L"NeverDeadSetupClass", L"Neverdeth (v0.1.0-alpha) — Game Data Setup",
                              WS_POPUP | WS_CAPTION | WS_SYSMENU, x, y, w, h, nullptr, nullptr,
                              hInst, &target_copy);
  if (!hwnd) {
    ExitProcess(0);
  }

  HICON ico_setup = LoadIconW(GetModuleHandleW(nullptr), MAKEINTRESOURCEW(1));
  if (ico_setup != nullptr) {
    SendMessageW(hwnd, WM_SETICON, ICON_SMALL, reinterpret_cast<LPARAM>(ico_setup));
    SendMessageW(hwnd, WM_SETICON, ICON_BIG, reinterpret_cast<LPARAM>(ico_setup));
  }

  ShowWindow(hwnd, SW_SHOW);
  UpdateWindow(hwnd);
  SetForegroundWindow(hwnd);

  MSG msg{};
  while (GetMessageW(&msg, nullptr, 0, 0) > 0) {
    TranslateMessage(&msg);
    DispatchMessageW(&msg);
  }

  std::error_code ec;
  if (!std::filesystem::exists(target_assets_dir / "default.xex", ec)) {
    ExitProcess(0);
  }
}

