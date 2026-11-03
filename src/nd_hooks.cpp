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
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <tlhelp32.h>
// La puerta de Present se instala sobre la vtable de IDXGISwapChain, no sobre
// un call-site adivinado de rexruntime.dll (ver NdInstallPresentGate).
#include <dxgi.h>
#include <dxgi1_2.h>
#endif
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <utility>
#include <string>
#include <thread>
#include <vector>
#include <rex/logging.h>
// Declaracion anticipada global: NdPresentHook (dentro del namespace anonimo de
// mas abajo) aplica el pacer, pero NdApplyFramePacer se define despues.
static void NdApplyFramePacer();
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
  if (force_paint) {
    g_force_paint_requested.store(true, std::memory_order_release);
    return true;
  }
  const uint64_t swaps = g_vd_swap_calls.load(std::memory_order_acquire);
  if (swaps > 0) {
    return false; // Con host_present_from_non_ui_thread = true, el hilo UI nunca pinta frames del juego
  }
  return true; // swaps == 0 (permite pintar la ventana antes del primer swap de juego)
}
extern "C" bool NdCheckShouldPaintAndPresent() {
  if (g_force_paint_requested.exchange(false, std::memory_order_acq_rel)) {
    return true;
  }
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
  nd_present_stats_tick(true, now);
  // Pacer del host en el hilo que presenta (no en el hilo del invitado).
  NdApplyFramePacer();
  // Presentacion inmediata sin bloqueo de VBlank fisico (igual que Xenia Canary vsync = false)
  const UINT present_sync = 0;
  const UINT present_flags = flags | DXGI_PRESENT_ALLOW_TEARING;
  return real_present(swap_chain, present_sync, present_flags);
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
// Peticion de toggle del menu F1: la ventana del juego solo la marca; el timer
// de 16 ms de la ventana del menu es el unico que ejecuta el toggle, asi no se
// puede togglear dos veces cuando el foco esta en el juego.
static std::atomic<bool> g_menu_toggle_request{false};
static WNDPROC g_original_game_wndproc = nullptr;

LRESULT CALLBACK NdGameSubclassWndProc(HWND hwnd, UINT msg, WPARAM wParam, LPARAM lParam) {
  if (msg == WM_KEYDOWN && wParam == VK_F1) {
    // Primer F1: hay que crear la ventana (y su timer) antes de que exista
    // nada que consuma la peticion. A partir de ahi manda el timer.
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
    // Nunca subclassar la ventana del menu F1 ni ninguna de su clase: si se
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
    REXLOG_INFO("neverdead: [WINDOW] HWND={:p} subclassed para interceptar F1 y WM_CLOSE limpia",
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
  constexpr size_t kRvaTimeout = 0xEB09F;
  uint8_t* target = base + kRvaTimeout;
  // movabs r8, 0x7fffffffffffffff
  static const uint8_t kExpected[10] = {
      0x49, 0xB8, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x7F};
  uint64_t timeout_ms = 1; // Default: 1 ms. Con 0 ms la cola asincrona de PSOs se
                           // rompe en el 2o swap: ventana negra y ni un frame.
                           // 1 ms es el valor que usa DownpourRecomp.
  const char* env_to = std::getenv("ND_PSO_TIMEOUT_MS");
  if (env_to != nullptr) {
    timeout_ms = std::strtoull(env_to, nullptr, 10);
  }
  uint8_t kPatched[10] = {0x49, 0xB8, 0, 0, 0, 0, 0, 0, 0, 0};
  std::memcpy(kPatched + 2, &timeout_ms, sizeof(timeout_ms));
  if (std::memcmp(target, kExpected, sizeof(kExpected)) != 0) {
    REXLOG_ERROR("neverdead: [ASYNC-PSO] bytes inesperados en rexgpu-xenos+0x{:X}; no se parchea", kRvaTimeout);
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
        timeout_ms, kRvaTimeout);
  }
}
//=============================================================================
void NdEarlyInit() {
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

void NdVdSwapHook() {
  const uint64_t n = g_vd_swap_calls.fetch_add(1, std::memory_order_relaxed) + 1;
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
}
//=============================================================================;
//=============================================================================;
// Control de Tasa de Refresco y Menu de Configuracion UI (F1)
//=============================================================================;
static std::atomic<float> g_fps_target{0.0f}; // 0.0f = Ilimitado por defecto
static std::atomic<bool> g_fps_mode_initialized{false};
static std::atomic<bool> g_config_ui_open{false};

// Frame Pacer de microsegundos con temporizador de alta resolucion
static HANDLE g_pacer_timer = nullptr;
static LARGE_INTEGER g_qpc_freq{};
static int64_t g_last_pacer_qpc = 0;

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
    HMODULE winmm = LoadLibraryA("winmm.dll");
    if (winmm != nullptr) {
      using TimeBeginPeriodFn = UINT(WINAPI*)(UINT);
      auto p_timeBeginPeriod = reinterpret_cast<TimeBeginPeriodFn>(GetProcAddress(winmm, "timeBeginPeriod"));
      if (p_timeBeginPeriod != nullptr) {
        p_timeBeginPeriod(1);
      }
    }
  });
}

static void NdApplyFramePacer() {
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
    if (remaining_ms > 1.5 && g_pacer_timer != nullptr) {
      int64_t sleep_100ns = static_cast<int64_t>((remaining_ms - 1.0) * 10000.0);
      LARGE_INTEGER due_time;
      due_time.QuadPart = -sleep_100ns;
      if (SetWaitableTimer(g_pacer_timer, &due_time, 0, nullptr, nullptr, FALSE)) {
        WaitForSingleObject(g_pacer_timer, INFINITE);
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
// Menu F1 — Dark Mode nativo, 520x320, logica positiva, F1/ESC instantaneo
//=============================================================================
// Spec v8: cero "echoing" visual (sin hexadecimales, sin "Desactivar" repetido,
// sin parrafos de ayuda), casillas con logica positiva (marcada = efecto ON) y
// toggle F1/ESC por timer de 16 ms en la propia ventana, asi funciona aunque el
// menu tenga el foco.
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
  IDC_CFG_BTN_CLOSE = 1030,
  IDC_CFG_BTN_EXIT_DESKTOP,
  IDC_CFG_PRESET_LOW,
  IDC_CFG_PRESET_HIGH,
  IDC_CFG_TIMER_ID = 1,
};

struct NdRenderToggle {
  const wchar_t* label;
  int id;
  uint32_t va;   // byte principal en .data del invitado
  uint32_t va2;  // flag companero (Sombras -> CSM), 0 si no aplica
};

// Orden = layout: 0..4 columna izquierda, 5..9 columna derecha.
static const NdRenderToggle kToggles[] = {
    {L"Sombras Dinámicas", IDC_CFG_CHK_SHADOW, 0x828E1FB7, 0x828E1FC5},
    {L"Oclusión Ambiental (SSAO)", IDC_CFG_CHK_SSAO, 0x828E1FC7, 0},
    {L"Motion Blur", IDC_CFG_CHK_MBLUR, 0x82801CEF, 0},
    {L"Bloom HDR", IDC_CFG_CHK_BLOOM, 0x828E1FC6, 0},
    {L"God Rays (Light Shafts)", IDC_CFG_CHK_LIGHTSHAFTS, 0x828E1FBF, 0},
    {L"Niebla Volumétrica", IDC_CFG_CHK_FOG, 0x828E1FC0, 0},
    {L"Post-Procesado (FSFX)", IDC_CFG_CHK_FSFX, 0x828E1FC1, 0},
    {L"Partículas (PFX)", IDC_CFG_CHK_PFX, 0x828E1FBD, 0},
    {L"Antialiasing (RMLAA)", IDC_CFG_CHK_RMLAA, 0x828E1FC9, 0},
    {L"Mostrar HUD", IDC_CFG_CHK_HUD, 0x828E1FC3, 0},
};

// 16 flags que tocan los presets (9 efectos + CSM + 6 de overdraw). El HUD va aparte.
static const uint32_t kPresetFlags[] = {
    0x828E1FB7,  // Shadows
    0x828E1FC5,  // CSM
    0x82801CEF,  // MotionBlur
    0x828E1FC7,  // SSAO
    0x828E1FC6,  // HDRbloom
    0x828E1FBF,  // LightShafts
    0x828E1FC0,  // Fog
    0x828E1FC1,  // FSFX
    0x828E1FBD,  // PFX
    0x828E1FC9,  // RMLAA
    0x828E1FB9,  // Decals
    0x828E1FBA,  // ObjectDecals
    0x828E1FBB,  // Photons
    0x828E1FBC,  // Projectors
    0x828E1FBE,  // Coronas
    0x828E1FC8,  // Weather
};
static constexpr uint32_t kHudVa = 0x828E1FC3;

// Estado del toggle F1/ESC: compartido entre WM_KEYDOWN (ventana con foco) y
// WM_TIMER (sondeo global). `*_wm_handled` impide que ambas vias cuenten la
// misma pulsacion como dos toggles.
static bool s_f1_wm_handled = false;
static bool s_esc_wm_handled = false;
static bool s_f1_prev = false;
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
  const bool on = read_guest_u8(t.va) != 0;
  if (t.va2 == 0) return on;
  return on && read_guest_u8(t.va2) != 0;
}

static void NdSyncCheckboxesFromMemory(HWND hwnd) {
  for (const auto& t : kToggles) {
    HWND chk = GetDlgItem(hwnd, t.id);
    if (chk != nullptr) {
      SendMessageW(chk, BM_SETCHECK, NdToggleValue(t) ? BST_CHECKED : BST_UNCHECKED, 0);
    }
  }
}

static void NdApplyPreset(HWND hwnd, bool high_quality) {
  const uint8_t val = high_quality ? 1 : 0;
  int written = 0;
  for (uint32_t va : kPresetFlags) {
    if (write_guest_u8(va, val)) ++written;
  }
  write_guest_u8(kHudVa, 1);  // HUD siempre encendido en ambos perfiles
  NdSyncCheckboxesFromMemory(hwnd);
  if (NdIsDiagEnabled()) {
    REXLOG_INFO("neverdead: [CONFIG-UI] Perfil '{}': {} flags = {} (HUD ON)",
                high_quality ? "Calidad Original" : "Bajo Consumo", written, val);
  }
}

static void NdApplyWindowMode(int idx) {
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
    SetWindowPos(ghwnd, HWND_TOP, 0, 0, GetSystemMetrics(SM_CXSCREEN),
                 GetSystemMetrics(SM_CYSCREEN), SWP_FRAMECHANGED | SWP_SHOWWINDOW);
  }
}

static void NdHideConfigAndFocusGame(HWND hwnd) {
  ShowWindow(hwnd, SW_HIDE);
  if (HWND g = NdGetGameWindow()) SetForegroundWindow(g);
}

// Banner NEVERDETH: BMP de 520x44 con el fondo carbon exacto del menu (LoadImage
// no soporta PNG con alfa, y el BMP ya trae el color de fondo).
static void NdDrawBanner(HDC hdc) {
  static HBITMAP s_banner = nullptr;
  static bool s_loaded = false;
  if (!s_loaded) {
    s_loaded = true;
    s_banner = reinterpret_cast<HBITMAP>(LoadImageW(
        nullptr, L"docs/assets/neverdeth_logo_banner.bmp", IMAGE_BITMAP, 0, 0,
        LR_LOADFROMFILE | LR_CREATEDIBSECTION));
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
  SendMessageW(cbWnd, CB_SETCURSEL, 0, 0);
  NdDisableTheme(cbWnd);

  HWND lblFps = CreateWindowW(L"STATIC", L"FPS:", WS_CHILD | WS_VISIBLE, 275, 65, 35, 20, hwnd,
                              nullptr, nullptr, nullptr);
  set_font(lblFps, hFont);
  HWND cbFps = CreateWindowExW(0, L"COMBOBOX", nullptr,
                               WS_CHILD | WS_VISIBLE | CBS_DROPDOWNLIST | WS_VSCROLL, 315, 61,
                               180, 200, hwnd, reinterpret_cast<HMENU>(IDC_CFG_COMBO_FPS),
                               nullptr, nullptr);
  set_font(cbFps, hFont);
  SendMessageW(cbFps, CB_ADDSTRING, 0, reinterpret_cast<LPARAM>(L"Ilimitado"));
  SendMessageW(cbFps, CB_ADDSTRING, 0, reinterpret_cast<LPARAM>(L"60 FPS"));
  SendMessageW(cbFps, CB_ADDSTRING, 0, reinterpret_cast<LPARAM>(L"30 FPS"));
  SendMessageW(cbFps, CB_SETCURSEL, 0, 0);
  NdDisableTheme(cbFps);

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
    const int y = 143 + (i % 5) * 26;
    HWND chk = CreateWindowW(L"BUTTON", t.label, WS_CHILD | WS_VISIBLE | BS_AUTOCHECKBOX, x, y,
                             220, 22, hwnd, reinterpret_cast<HMENU>(static_cast<INT_PTR>(t.id)),
                             nullptr, nullptr);
    set_font(chk, hFont);
    NdDisableTheme(chk);  // sin tema visual: respeta WM_CTLCOLORSTATIC
    SendMessageW(chk, BM_SETCHECK, NdToggleValue(t) ? BST_CHECKED : BST_UNCHECKED, 0);
  }

  // --- Fila inferior ---
  HWND btnClose = CreateWindowW(L"BUTTON", L"Volver al Juego (F1 / ESC)",
                                WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON | BS_OWNERDRAW, 20, 287, 230, 32, hwnd,
                                reinterpret_cast<HMENU>(IDC_CFG_BTN_CLOSE), nullptr, nullptr);
  set_font(btnClose, hFontBold);
  NdDisableTheme(btnClose);
  HWND btnExit = CreateWindowW(L"BUTTON", L"Salir al Escritorio",
                               WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON | BS_OWNERDRAW, 275, 287, 220, 32, hwnd,
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
      // Via directa: el mensaje llega a la ventana con foco. El timer cubre el
      // caso contrario (foco en el juego). El flag "handled" evita el doble
      // toggle cuando ambas vias ven la misma pulsacion.
      if (wParam == VK_F1) {
        if (!s_f1_wm_handled) {
          s_f1_wm_handled = true;
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
      if (wParam == VK_F1) s_f1_wm_handled = false;
      if (wParam == VK_ESCAPE) s_esc_wm_handled = false;
      break;
    }
    case WM_TIMER: {
      if (wParam != IDC_CFG_TIMER_ID) break;
      // Toggle global por sondeo: cubre el caso "el foco esta en el juego".
      // La peticion del subclass del juego se funde con la deteccion de flanco.
      const bool f1_down = (GetAsyncKeyState(VK_F1) & 0x8000) != 0;
      const bool esc_down = (GetAsyncKeyState(VK_ESCAPE) & 0x8000) != 0;
      const bool f1_edge = f1_down && !s_f1_prev && !s_f1_wm_handled;
      const bool esc_edge = esc_down && !s_esc_prev && !s_esc_wm_handled;
      const bool requested = g_menu_toggle_request.exchange(false);
      s_f1_prev = f1_down;
      s_esc_prev = esc_down;
      if (f1_edge || requested) {
        if (IsWindowVisible(hwnd)) {
          NdHideConfigAndFocusGame(hwnd);
        } else {
          NdSyncCheckboxesFromMemory(hwnd);
          ShowWindow(hwnd, SW_SHOW);
          SetForegroundWindow(hwnd);
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
      // Fondo carbon (mas claro al pulsar) + borde bronce; "Salir" en rojo.
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
          REXLOG_INFO("neverdead: [CLEAN-EXIT] Salir al Escritorio desde el menu F1");
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
      if (id == IDC_CFG_COMBO_FPS && code == CBN_SELCHANGE) {
        HWND cb = reinterpret_cast<HWND>(lParam);
        const int idx = static_cast<int>(SendMessageW(cb, CB_GETCURSEL, 0, 0));
        const float target = (idx == 1) ? 60.0f : (idx == 2) ? 30.0f : 0.0f;
        g_fps_target.store(target, std::memory_order_relaxed);
        // NUNCA escribir el limite en 0x82B31FBC: con un valor distinto de 0 el
        // motor Asura se queda esperando vblanks tras el primer VdSwap.
        write_guest_be32(0x82B31FBC, float_to_be_bits(0.0f));
        if (NdIsDiagEnabled()) {
          REXLOG_INFO("neverdead: [CONFIG-UI] Limite de framerate: {:.1f} FPS (pacer host)",
                      target);
        }
        return 0;
      }
      if (id == IDC_CFG_COMBO_WND && code == CBN_SELCHANGE) {
        HWND cb = reinterpret_cast<HWND>(lParam);
        NdApplyWindowMode(static_cast<int>(SendMessageW(cb, CB_GETCURSEL, 0, 0)));
        return 0;
      }
      // Casillas: logica positiva (marcada = efecto encendido).
      for (const auto& t : kToggles) {
        if (id == t.id && code == BN_CLICKED) {
          const bool on =
              SendMessageW(reinterpret_cast<HWND>(lParam), BM_GETCHECK, 0, 0) == BST_CHECKED;
          write_guest_u8(t.va, on ? 1 : 0);
          if (t.va2 != 0) write_guest_u8(t.va2, on ? 1 : 0);
          if (NdIsDiagEnabled()) {
            // t.label es wchar_t*: fmt no acepta punteros anchos, se loguea el id.
            REXLOG_INFO("neverdead: [CONFIG-UI] toggle id={} (va=0x{:08X}) = {}", t.id, t.va,
                        on ? 1 : 0);
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

  // 520x320 de area cliente exacta, centrada en pantalla.
  RECT rc = {0, 0, 520, 370};
  AdjustWindowRectEx(&rc, WS_POPUP | WS_CAPTION | WS_SYSMENU, FALSE, 0);
  const int w = rc.right - rc.left;
  const int h = rc.bottom - rc.top;
  const int x = (GetSystemMetrics(SM_CXSCREEN) - w) / 2;
  const int y = (GetSystemMetrics(SM_CYSCREEN) - h) / 2;

  HWND hwnd = CreateWindowExW(0, L"NeverDeadConfigClass", L"NeverDeth — Ajustes (F1)",
                              WS_POPUP | WS_CAPTION | WS_SYSMENU, x, y, w, h, nullptr, nullptr,
                              hInst, nullptr);
  if (hwnd == nullptr) {
    g_config_ui_open.store(false, std::memory_order_release);
    return;
  }
  g_config_hwnd = hwnd;
  NdSetDarkTitleBar(hwnd);
  // Icono NEVERDETH en la barra de titulo y en Alt+Tab.
  HICON ico = reinterpret_cast<HICON>(LoadImageW(nullptr, L"config/neverdeth.ico",
                                                 IMAGE_ICON, 32, 32, LR_LOADFROMFILE));
  if (ico != nullptr) {
    SendMessageW(hwnd, WM_SETICON, ICON_SMALL, reinterpret_cast<LPARAM>(ico));
    SendMessageW(hwnd, WM_SETICON, ICON_BIG, reinterpret_cast<LPARAM>(ico));
  }
  NdCreateConfigControls(hwnd);
  SetTimer(hwnd, IDC_CFG_TIMER_ID, 16, nullptr);

  ShowWindow(hwnd, SW_SHOW);
  UpdateWindow(hwnd);
  SetForegroundWindow(hwnd);

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
      ShowWindow(existing, SW_SHOW);
    }
    SetForegroundWindow(existing);
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
  // (NdApplyFramePacer) y el menu F1, sin tocar esta variable.
  uint8_t* p = nd_translate_va(0x82B31FBC);
  if (p != nullptr) {
    write_guest_be32(0x82B31FBC, float_to_be_bits(0.0f));
  }
}

static void NdInitFpsMode() {
  if (g_fps_mode_initialized.exchange(true)) {
    return;
  }
  // 0.0f = sin limite. Es el modo con el que el motor Asura SI avanza: con
  // 60.0f el invitado se queda esperando vblanks que nunca llegan tras el
  // primer VdSwap y la ventana queda en negro (ver docs/HANDOFF).
  float target = 0.0f;
  const char* env = std::getenv("ND_FPS_LIMIT");
  if (env != nullptr) {
    const float v = static_cast<float>(std::atof(env));
    if (v > 0.0f && v <= 1000.0f) {
      target = v;
    }
  }
  g_fps_target.store(target, std::memory_order_relaxed);
  NdSyncFpsClampMemory(target);
  REXLOG_INFO("neverdead: [FPS] Inicializado a {:.1f} FPS (0.0 = desbloqueado)", target);
}
static void NdCheckFpsToggleKey() {
  NdInitFpsMode();
  static std::atomic<bool> s_f8_was_down{false};
  SHORT f8_state = GetAsyncKeyState(0x77); // VK_F8
  bool f8_down = (f8_state & 0x8000) != 0;
  if (f8_down && !s_f8_was_down.exchange(true)) {
    float cur = g_fps_target.load(std::memory_order_relaxed);
    float next = (cur > 0.0f && cur <= 35.0f) ? 0.0f : 30.0f;
    g_fps_target.store(next, std::memory_order_relaxed);
    NdSyncFpsClampMemory(next);
    REXLOG_INFO("neverdead: [FPS-TOGGLE] *** F8 PULSADO: Modo alternado a {} (0x82B31FBC sincronizado) ***",
                (next <= 0.0f) ? "Desbloqueado (72+ FPS / 0.0f)" : "30 FPS (30.0f)");
  } else if (!f8_down && s_f8_was_down.load(std::memory_order_relaxed)) {
    s_f8_was_down.store(false, std::memory_order_relaxed);
  }
  // F1 ya NO se sondea aqui: lo gestiona el timer de 16 ms de la ventana del
  // menu (funciona con el foco en el juego o en el menu). Ver NdConfigWndProc.
}

void NdForceIntervalOneHook(PPCRegister& r11) {
  NdInitFpsMode();
  r11.u64 = 0;
  NdSyncFpsClampMemory(0.0f);
}

void NdAdjustRenderTimeoutHook(PPCRegister& r11) {
  const float target = g_fps_target.load(std::memory_order_relaxed);
  r11.s64 = (target > 0.0f && target <= 35.0f) ? -333333 : -250000;
}

void NdSetFpsLimitHook(PPCRegister& f0) {
  f0.f64 = 0.0;
  f0.f32 = 0.0f;
  NdSyncFpsClampMemory(0.0f);
}
