// neverdead - ReXGlue Recompiled Project
//
// Customize your app by overriding virtual hooks from rex::ReXApp.

#pragma once

#include <bit>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <string>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#endif

#include <rex/logging.h>
#include <rex/rex_app.h>
#include <rex/filesystem/vfs.h>

#include "nd_import_diag.h"
#include "nd_ke_tls_fix.h"
#include "nd_hooks.h"

class NeverdeadApp : public rex::ReXApp {
 public:
  using rex::ReXApp::ReXApp;

  static std::unique_ptr<rex::ui::WindowedApp> Create(
      rex::ui::WindowedAppContext& ctx) {
    return std::unique_ptr<NeverdeadApp>(new NeverdeadApp(ctx, "neverdead",
        PPCImageConfig));
  }

  // FIX(neverdead-xex-image): sin este override el XEX nunca se vuelca a la
  // arena guest y .rdata/.data quedan sin mapear: el slot en 0x82144940 lee
  // 0xFFFFFBFF y loc_824D9E94 muere en `Call to invalid or unregistered
  // function` (exit 0xC0000409). Fijar el alias esperado del asset basta; el
  // SDK mapea las secciones desde default.xex (9.5MB en assets/).
  void OnLoadXexImage(std::string& xex_image) override {
    xex_image = "game:\\default.xex";
  }

  // DIAG(neverdead-xex-probe): verifica tras la carga que el slot que mata al
  // arranque ya contiene la VTABLE real. Solo diagnostica: no parchea memoria
  // (si el SDK ya mapea .rdata/.data este log sale OK y no hace falta tocar
  // OnPreLaunchModule).
  void MapBaseExeSections() {
    auto* rt = runtime();
    if (rt == nullptr || rt->memory() == nullptr) return;
    auto* mem = rt->memory();
    uint8_t* guest_base = mem->TranslateVirtual<uint8_t*>(0x82000000u);
    if (!guest_base) {
      REXLOG_ERROR("neverdead: MapBaseExeSections cannot translate guest 0x82000000");
      return;
    }

#ifdef _WIN32
    // CRITICO: Desbloquear permisos de escritura en cada region de [0x82000000, 0x82C10000)
    // iterando via VirtualQuery (porque VirtualProtect falla si el rango cruza multiples regiones MBI).
    {
      uint8_t* curr = guest_base;
      uint8_t* end = guest_base + 0x00C10000u;
      size_t count = 0;
      while (curr < end) {
        MEMORY_BASIC_INFORMATION mbi{};
        if (!VirtualQuery(curr, &mbi, sizeof(mbi))) break;
        if (mbi.State == MEM_COMMIT && mbi.Protect != PAGE_EXECUTE_READWRITE && mbi.Protect != PAGE_READWRITE) {
          DWORD old_prot = 0;
          DWORD target_prot = (mbi.AllocationProtect & 0xF0) ? PAGE_EXECUTE_READWRITE : PAGE_READWRITE;
          if (VirtualProtect(mbi.BaseAddress, mbi.RegionSize, target_prot, &old_prot)) {
            ++count;
          } else if (VirtualProtect(mbi.BaseAddress, mbi.RegionSize, PAGE_READWRITE, &old_prot)) {
            ++count;
          }
        }
        curr = reinterpret_cast<uint8_t*>(mbi.BaseAddress) + mbi.RegionSize;
      }
      REXLOG_INFO("neverdead: MapBaseExeSections unlocked {} memory regions to writable", count);
    }
#endif

    auto exe_dir = rex::filesystem::GetExecutableFolder();
    auto try_open = [](const std::filesystem::path& p) -> FILE* {
      std::error_code ec;
      if (std::filesystem::is_regular_file(p, ec)) {
        return fopen(p.string().c_str(), "rb");
      }
      return nullptr;
    };
    FILE* f = try_open(exe_dir.parent_path() / "NeverDead_base.bin");
    if (!f) f = try_open(exe_dir / "NeverDead_base.bin");
    if (!f) f = try_open(exe_dir.parent_path() / "assets" / "NeverDead_base.bin");
    if (!f) f = try_open(exe_dir / "assets" / "NeverDead_base.bin");
    if (!f) f = try_open("NeverDead_base.bin");
    if (!f) f = try_open("../NeverDead_base.bin");
    if (!f) {
      REXLOG_WARN("neverdead: MapBaseExeSections: NeverDead_base.bin no encontrado (no critico si default.xex mapea .data)");
      return;
    }

    // PATCH(neverdead-rdata-holes): rellenar HUECOS, no sobrescribir.
    //
    // EVIDENCIA (sonda pre-map, neverdead_012.log): en la imagen que carga el XEX,
    //   0x82144930[+0]  = 0x822CE8F0   (vtable valida)
    //   0x82144930[+16] = 0x824DDF90   (puntero a funcion valido)
    //   0x82144930[+32] = 0x824DDFF0   (puntero a funcion valido)
    // Tras la copia 1:1 del .exe, [+16] pasaba a 0x006C0065 = "le" (texto UTF-16).
    //
    // CORRECCION TRAS AUDITORIA ADVERSARIA (2026-10-23): el desplazamiento entre el
    // .exe y la imagen guest NO es constante. Medido con anclas de contenido
    // (volcados `dump` del log, comparados contra el .exe):
    //
    //   .data   rva 0x7DBFA8 / 0x7EA280 / 0x7EA2B0  -> shift 0x0000  (3/3 anclas)
    //   .rdata  rva 0x04AFC0 / 0x04D350 / 0x04D360  -> shift 0x0000  (3/3 anclas)
    //   .rdata  rva 0x144930                          -> shift 0x1000  (5/5 palabras)
    //   .rdata  rva 0x49EF98 / 0x49EFA8               -> shift 0x2000  (2/2 anclas)
    //
    // Es decir: dentro de la MISMA seccion .rdata el desplazamiento toma tres
    // valores distintos. No existe un offset fijo por seccion que sea correcto,
    // asi que inyectar con offset fijo es inyectar basura en parte de los huecos.
    //
    // Regla nueva (conservadora y verificable):
    //   * .data  -> se rellena con offset = rva (shift 0 verificado).
    //   * .rdata -> NO se inyecta: el loader del XEX la carga correctamente
    //               (lo demuestra la sonda pre-map: los punteros ya eran validos
    //               antes de tocar nada) y su offset correcto no es conocible.
    //   * .pdata -> no se inyecta (rellenaba 0 palabras de todos modos).
    //   * .post  -> no se inyecta: el rango 0xB7BE00..0xB90200 cae mas alla de la
    //               ultima seccion del .exe (0x8EDC00), lee cola de fichero.
    //   * ND_FILL_RDATA=1 / ND_FILL_POST=1 reactivan las dos ultimas para A/B.
    auto env_on = [](const char* name) {
      const char* v = std::getenv(name);
      return v != nullptr && v[0] != '\0' && v[0] != '0';
    };
    auto fill_holes = [&](const char* tag, uint32_t rva, uint32_t size,
                          uint32_t shift, bool enabled) -> uint32_t {
      if (!enabled) {
        REXLOG_INFO("neverdead: MapBaseExeSections {} rva=0x{:X} size=0x{:X} DESACTIVADO",
                    tag, rva, size);
        return 0;
      }
      static uint8_t buf[0x10000];
      uint32_t filled = 0;
      uint32_t done = 0;
      while (done < size) {
        const uint32_t chunk = (size - done) < sizeof(buf) ? (size - done) : (uint32_t)sizeof(buf);
        if (fseek(f, (long)(rva + done - shift), SEEK_SET) != 0) break;
        const size_t got = fread(buf, 1, chunk, f);
        if (got == 0) break;
        uint8_t* dst = guest_base + rva + done;
        for (size_t i = 0; i + 4 <= got; i += 4) {
          uint32_t g, s;
          std::memcpy(&g, dst + i, 4);
          std::memcpy(&s, buf + i, 4);
          if (g == 0 && s != 0) {
            std::memcpy(dst + i, buf + i, 4);
            ++filled;
          }
        }
        done += (uint32_t)got;
        if (got < chunk) break;
      }
      REXLOG_INFO("neverdead: MapBaseExeSections {} rva=0x{:X} size=0x{:X} shift=0x{:X} palabras_rellenadas={}",
                  tag, rva, size, shift, filled);
      return filled;
    };

    const uint32_t h1 = fill_holes(".rdata", 0x00000A40u, 0x0016F800u - 0x00000A40u,
                                   0x0000u, env_on("ND_FILL_RDATA"));
    const uint32_t h2 = fill_holes(".pdata", 0x0016F800u, 0x00195E00u - 0x0016F800u,
                                   0x0000u, false);
    const uint32_t h3 = fill_holes(".data", 0x007D5C00u, 0x000C1200u,
                                   0x0000u, true);
    const uint32_t h4 = fill_holes(".post", 0x00B7BE00u, 0x00B90200u - 0x00B7BE00u,
                                   0x0000u, env_on("ND_FILL_POST"));

    fclose(f);
    REXLOG_INFO("neverdead: MapBaseExeSections holes-only completado "
                "(.rdata={}, .pdata={}, .data={}, .post={}; IAT [0x600,0xA40) preservado)",
                h1, h2, h3, h4);
  }

  void SetupGuestMemoryHooks() {
    auto* rt = runtime();
    if (rt == nullptr || rt->memory() == nullptr) return;
    auto* mem = rt->memory();
    nd_guest_mem_set(static_cast<void*>(mem));
    nd_set_translate(&nd_translate_va);
  }

  void OnPostLoadXexImage() override {
    auto* rt = runtime();
    if (rt == nullptr || rt->memory() == nullptr) {
      return;
    }
    // DIAG(pre-map): que contiene la imagen guest del XEX ANTES de que
    // MapBaseExeSections copie encima el NeverDead_base.exe.  Sirve para
    // separar "el XEX tiene basura aqui" de "el exe compactado la introduce".
    if (auto* pre_mem = rt->memory()) {
      const uint32_t va = 0x82144930u;
      if (const auto* p = pre_mem->TranslateVirtual<const uint8_t*>(va)) {
        uint32_t w0, w1, w2, w3, w4;
        std::memcpy(&w0, p + 0x00, 4);
        std::memcpy(&w1, p + 0x10, 4);
        std::memcpy(&w2, p + 0x20, 4);
        std::memcpy(&w3, p + 0x30, 4);
        std::memcpy(&w4, p + 0x40, 4);
        REXLOG_INFO("neverdead: pre-map obj@82144930 [+0]={:08X} [+16]={:08X} [+32]={:08X} [+48]={:08X} [+64]={:08X}",
                    std::byteswap(w0), std::byteswap(w1), std::byteswap(w2),
                    std::byteswap(w3), std::byteswap(w4));
      }
    }
    MapBaseExeSections();
    auto* mem = rt->memory();
    constexpr uint32_t kObjBase = 0x82144930u;
    constexpr uint32_t kSlot = kObjBase + 16u;  // loc_824D9E94 lee guest[kSlot]
    constexpr uint32_t kExpectVtable = 0x8249EF98u;  // VTABLE fisica en XEX RVA 0x144940
    const uint32_t raw = *mem->TranslateVirtual<const uint32_t*>(kSlot);
    // La arena guest es big-endian vista desde el host little-endian.
    const uint32_t slot = std::byteswap(raw);
    REXLOG_INFO("neverdead: post-load probe obj=0x{:08X} slot=0x{:08X} value=0x{:08X}",
                kObjBase, kSlot, slot);
    if (slot == kExpectVtable) {
      REXLOG_INFO("neverdead: .rdata OK (VTABLE 0x{:08X} presente), loc_824D9E94 debe avanzar a GUIMenu",
                  kExpectVtable);
    } else {
      REXLOG_WARN("neverdead: .rdata ausente en slot (got 0x{:08X}, want 0x{:08X}); revisar game_data_root/XEX",
                  slot, kExpectVtable);
    }
    constexpr uint32_t kVtableAddr = 0x82140958u;
    const uint32_t v0 = std::byteswap(*mem->TranslateVirtual<const uint32_t*>(kVtableAddr));
    const uint32_t v1 = std::byteswap(*mem->TranslateVirtual<const uint32_t*>(kVtableAddr + 4));
    REXLOG_INFO("neverdead: post-load vtable@82140958 = [0x{:08X}, 0x{:08X}]", v0, v1);
    if (v0 == 0x8249EF98u && v1 == 0x825C3370u) {
      REXLOG_INFO("neverdead: .rdata OK! Real VTABLE 0x8249EF98 confirmed active at 0x82140958");
    }

    // DIAG(map-shift): escaneo la imagen guest buscando anchors unicos del
    // archivo (deben estar byteswapeados: arena guest BE vista host LE).
    // Mide el desfase real runtime<->archivo por seccion (.rdata y .text).
    struct Anchor {
      uint32_t word0;
      uint32_t word1;
      const char* tag;
    };
    constexpr uint32_t kImageBase = 0x82000000u;
    constexpr uint32_t kImageSize = 0xA10000u;
    const Anchor anchors[] = {
        {0x8249EF98u, 0x825C3370u, "vtable@82144940"},  // .rdata RVA 0x144940
        {0x7D8802A6u, 0x48588A95u, "ctor@822032B0"},   // .text RVA 0x1FE2B0
        {0x7D8802A6u, 0x482B1F51u, "entry@824D9DF8"},  // .text RVA 0x4D4DF8
        {0x48031BADu, 0x48000014u, "stub@8278BD48"},   // .text RVA 0x786D48
    };
    const uint32_t* base = mem->TranslateVirtual<const uint32_t*>(kImageBase);
    if (base == nullptr) {
      REXLOG_ERROR("neverdead: map-diag no puedeTraslate 0x{:08X}", kImageBase);
      return;
    }
    const size_t words = kImageSize / sizeof(uint32_t);
    for (const auto& a : anchors) {
      const uint32_t w0 = std::byteswap(a.word0);
      const uint32_t w1 = std::byteswap(a.word1);
      uint32_t matches = 0;
      for (size_t i = 0; i + 1 < words && matches < 4; ++i) {
        if (base[i] == w0 && base[i + 1] == w1) {
          const uint32_t guest = kImageBase + static_cast<uint32_t>(i * 4);
          REXLOG_INFO("neverdead: map-diag {} host-idx={:08X} guest=0x{:08X}",
                      a.tag, (uint32_t)i, guest);
          ++matches;
        }
      }
      if (matches == 0) {
        REXLOG_WARN("neverdead: map-diag {} SIN MATCH en imagen guest", a.tag);
      }
    }

    // DIAG(raw-dump): regiones clave del flujo de aborto (2026-10-17).
    auto dump = [&](const char* tag, uint32_t va, uint32_t nbytes) {
      const auto* p = mem->TranslateVirtual<const uint8_t*>(va);
      if (p == nullptr) {
        REXLOG_WARN("neverdead: dump {} 0x{:08X} no traducible", tag, va);
        return;
      }
      for (uint32_t off = 0; off < nbytes; off += 16) {
        uint32_t w0, w1, w2, w3;
        std::memcpy(&w0, p + off, 4);
        std::memcpy(&w1, p + off + 4, 4);
        std::memcpy(&w2, p + off + 8, 4);
        std::memcpy(&w3, p + off + 12, 4);
        REXLOG_INFO("neverdead: dump {} 0x{:08X}: {:08X} {:08X} {:08X} {:08X}",
                    tag, va + off, std::byteswap(w0), std::byteswap(w1),
                    std::byteswap(w2), std::byteswap(w3));
      }
    };
    dump("dtor-array", 0x827DBF90u, 0x70u);
    dump("cs-table", 0x827EA280u, 0x80u);
    dump("vtbl-ptr", 0x8249EF98u, 0x20u);
    dump("name-pool", 0x8204AFC0u, 0x40u);
    dump("cs-pool", 0x8204D340u, 0x40u);

    // FIX(neverdead-rdata-write): Xbox 360 no impone proteccion de solo lectura
    // en .rdata; motores como Asura engine modifican tablas globales en .rdata (0x82080004).
    // Asegurar que cada region comprometida en [0x82000000, 0x82C10000) sea PAGE_EXECUTE_READWRITE.
#ifdef _WIN32
    void* host_img = mem->TranslateVirtual<void*>(0x82000000u);
    if (host_img != nullptr) {
      uint8_t* curr = reinterpret_cast<uint8_t*>(host_img);
      uint8_t* end = curr + 0x00C10000u;
      size_t count = 0;
      while (curr < end) {
        MEMORY_BASIC_INFORMATION mbi{};
        if (!VirtualQuery(curr, &mbi, sizeof(mbi))) break;
        REXLOG_INFO("neverdead: MBI base={:p} alloc_base={:p} alloc_prot=0x{:X} size=0x{:X} state=0x{:X} prot=0x{:X} type=0x{:X}",
                    mbi.BaseAddress, mbi.AllocationBase, mbi.AllocationProtect,
                    (size_t)mbi.RegionSize, mbi.State, mbi.Protect, mbi.Type);
        if (mbi.State == MEM_COMMIT && mbi.Protect != PAGE_EXECUTE_READWRITE && mbi.Protect != PAGE_READWRITE) {
          DWORD old_prot = 0;
          DWORD target_prot = (mbi.AllocationProtect & 0xF0) ? PAGE_EXECUTE_READWRITE : PAGE_READWRITE;
          if (VirtualProtect(mbi.BaseAddress, mbi.RegionSize, target_prot, &old_prot)) {
            REXLOG_INFO("neverdead: region {:p} (size 0x{:X}) changed 0x{:X} -> 0x{:X}",
                        mbi.BaseAddress, (size_t)mbi.RegionSize, old_prot, target_prot);
            ++count;
          } else {
            DWORD err1 = GetLastError();
            if (VirtualProtect(mbi.BaseAddress, mbi.RegionSize, PAGE_READWRITE, &old_prot)) {
              REXLOG_INFO("neverdead: region {:p} (size 0x{:X}) changed 0x{:X} -> PAGE_READWRITE",
                          mbi.BaseAddress, (size_t)mbi.RegionSize, old_prot);
              ++count;
            } else {
              REXLOG_ERROR("neverdead: VirtualProtect {:p} failed: err1={} err2={}", mbi.BaseAddress, err1, GetLastError());
            }
          }
        }
        curr = reinterpret_cast<uint8_t*>(mbi.BaseAddress) + mbi.RegionSize;
      }
      REXLOG_INFO("neverdead: total {} regions updated to writable", count);
    }

    // FIX(neverdead-guest-ram): Pre-commit 1GB guest physical RAM + WC mirror [0x00000000, 0x40000000)
    // Eliminates 86,000+ VEH page faults and disk I/O bottlenecks during Asura engine boot memory initialization.
    void* host_ram = mem->TranslateVirtual<void*>(0x00000000u);
    if (host_ram != nullptr) {
      void* allocated = VirtualAlloc(host_ram, 0x40000000, MEM_COMMIT, PAGE_READWRITE);
      DWORD err = allocated ? 0 : GetLastError();
      REXLOG_INFO("neverdead: guest physical RAM pre-commit [0x00000000, 0x40000000) at {:p} result={:p} (err={})",
                  host_ram, allocated, err);
    }

    void* host_mid = mem->TranslateVirtual<void*>(0x40000000u);
    if (host_mid != nullptr) {
      void* allocated = VirtualAlloc(host_mid, 0x40000000, MEM_COMMIT, PAGE_READWRITE);
      DWORD err = allocated ? 0 : GetLastError();
      REXLOG_INFO("neverdead: guest mid/high memory pre-commit [0x40000000, 0x80000000) at {:p} result={:p} (err={})",
                  host_mid, allocated, err);
    }

    // FIX(neverdead-physical-arena): Sweep and commit physical_membase region-by-region (err=0)
    auto ensure_host_range_committed = [](void* host_start, size_t size, const char* name) {
      if (!host_start) return;
      uint8_t* curr = reinterpret_cast<uint8_t*>(host_start);
      uint8_t* end = curr + size;
      size_t committed_bytes = 0, reserved_fixed = 0;
      while (curr < end) {
        MEMORY_BASIC_INFORMATION mbi{};
        if (!VirtualQuery(curr, &mbi, sizeof(mbi))) break;
        uint8_t* r_start = reinterpret_cast<uint8_t*>(mbi.BaseAddress);
        size_t r_size = mbi.RegionSize;
        if (r_start < curr) {
          r_size -= (curr - r_start);
          r_start = curr;
        }
        if (r_start + r_size > end) {
          r_size = end - r_start;
        }
        if (mbi.State == MEM_RESERVE) {
          void* res = VirtualAlloc(r_start, r_size, MEM_COMMIT, PAGE_READWRITE);
          if (res) reserved_fixed += r_size;
        } else if (mbi.State == MEM_COMMIT) {
          committed_bytes += r_size;
        }
        curr = reinterpret_cast<uint8_t*>(mbi.BaseAddress) + mbi.RegionSize;
      }
      REXLOG_INFO("neverdead: {} at {:p} (0x{:X}) verified committed=0x{:X} newly_fixed=0x{:X}",
                  name, host_start, size, committed_bytes, reserved_fixed);
    };

    void* host_phys = mem->physical_membase();
    if (host_phys != nullptr) {
      ensure_host_range_committed(host_phys, 0x20010000, "physical memory arena");
    }

    // FIX(neverdead-stacks): Pre-commit 240MB of guest stack and user heap memory [0x70000000, 0x7F000000)
    // Eliminates 23,800+ VEH page faults during Asura engine boot memory initialization.
    void* host_stacks = mem->TranslateVirtual<void*>(0x70000000u);
    if (host_stacks != nullptr) {
      void* allocated = VirtualAlloc(host_stacks, 0x0F000000, MEM_COMMIT, PAGE_READWRITE);
      DWORD err = allocated ? 0 : GetLastError();
      REXLOG_INFO("neverdead: guest stacks/user heap commit at {:p} (240MB) result={:p} (err={})", host_stacks, allocated, err);
    }

    // FIX(neverdead-pre-image-heap): Pre-commit 32MB pre-image heap [0x80000000, 0x82000000)
    void* host_pre_img = mem->TranslateVirtual<void*>(0x80000000u);
    if (host_pre_img != nullptr) {
      void* allocated = VirtualAlloc(host_pre_img, 0x02000000, MEM_COMMIT, PAGE_READWRITE);
      DWORD err = allocated ? 0 : GetLastError();
      REXLOG_INFO("neverdead: pre-image heap commit [0x80000000, 0x82000000) at {:p} result={:p} (err={})",
                  host_pre_img, allocated, err);
    }

    // NOTA CRITICA VTABLES: [0x82000000, 0x82C10000) es la imagen XEX (.text y .rdata/vtables).
    // NO invocar VirtualAlloc aqui para no revocar PAGE_EXECUTE. Mantenido como PAGE_EXECUTE_READWRITE.

    // FIX(neverdead-post-image-heap): Pre-commit post-image heap in view v80000000 [0x82C10000, 0x90000000) (~211.9MB)
    void* host_post_img = mem->TranslateVirtual<void*>(0x82C10000u);
    if (host_post_img != nullptr) {
      void* allocated = VirtualAlloc(host_post_img, 0x0D3F0000, MEM_COMMIT, PAGE_READWRITE);
      DWORD err = allocated ? 0 : GetLastError();
      REXLOG_INFO("neverdead: post-image heap commit [0x82C10000, 0x90000000) at {:p} result={:p} (err={})",
                  host_post_img, allocated, err);
    }

    // FIX(neverdead-v90-kernel-heap): Pre-commit extended kernel heap in view v90000000 [0x90000000, 0x92000000) (32MB)
    void* host_v90 = mem->TranslateVirtual<void*>(0x90000000u);
    if (host_v90 != nullptr) {
      void* allocated = VirtualAlloc(host_v90, 0x02000000, MEM_COMMIT, PAGE_READWRITE);
      DWORD err = allocated ? 0 : GetLastError();
      REXLOG_INFO("neverdead: extended kernel heap commit [0x90000000, 0x92000000) at {:p} result={:p} (err={})",
                  host_v90, allocated, err);
    }

    // MBI safety sweep: ensures no uncommitted gaps remain in physical RAM and kernel heap
    auto ensure_range_committed = [&](uint32_t guest_start, uint32_t size, const char* name) {
      void* host_start = mem->TranslateVirtual<void*>(guest_start);
      if (host_start != nullptr) {
        ensure_host_range_committed(host_start, size, name);
      }
    };
    ensure_range_committed(0x00000000u, 0x20000000u, "guest physical RAM");
    ensure_range_committed(0x40000000u, 0x40000000u, "guest mid/high RAM");
    ensure_range_committed(0x70000000u, 0x0F000000u, "guest stacks/user heap");
    ensure_range_committed(0x80000000u, 0x02000000u, "pre-image heap");
    ensure_range_committed(0x82C10000u, 0x0F3F0000u, "post-image kernel heap");
#endif

    // Parche de 60 FPS (Xenia "Unlock FPS"). Debe ir despues del mapeo para
    // que ninguna copia posterior lo sobrescriba.
    SetupGuestMemoryHooks();
  }


  void OnConfigurePaths(rex::PathConfig& paths) override {
    auto exe_dir = rex::filesystem::GetExecutableFolder();
    auto root_dir = exe_dir.parent_path();
    std::error_code ec;

    // 1. Resolver game_data_root si esta vacio o si el path relativo "assets" no existe en el CWD
    if (paths.game_data_root.empty() || !std::filesystem::exists(paths.game_data_root, ec)) {
      auto candidate = exe_dir / "assets";
      if (std::filesystem::is_directory(candidate, ec)) {
        paths.game_data_root = candidate;
        REXLOG_INFO("neverdead: auto-configured game_data_root from exe_dir: {}", candidate.string());
      } else {
        auto parent_candidate = root_dir / "assets";
        if (std::filesystem::is_directory(parent_candidate, ec)) {
          paths.game_data_root = parent_candidate;
          REXLOG_INFO("neverdead: auto-configured game_data_root from parent dir: {}", parent_candidate.string());
        }
      }
    }

    // 2. Unificar neverdead.toml: si existe en la raiz del proyecto, usarlo como fuente unica de verdad
    auto root_toml = root_dir / "neverdead.toml";
    if (std::filesystem::is_regular_file(root_toml, ec)) {
      paths.config_path = root_toml;
      REXLOG_INFO("neverdead: unified config_path to project root: {}", root_toml.string());
    }

    // 3. Auto-configurar cache_root persistente en la raiz del proyecto si no viene fijado por CLI o no existe
    if (paths.cache_root.empty() || !std::filesystem::exists(paths.cache_root, ec)) {
      auto root_cache = root_dir / "cache";
      std::filesystem::create_directories(root_cache, ec);
      paths.cache_root = root_cache;
      REXLOG_INFO("neverdead: auto-configured cache_root to: {}", root_cache.string());
    }
  }

  void OnPreSetup(rex::RuntimeConfig& config) override {
    config.gpu_plugin = "xenos";
    // CRITICO (GATE 1): instalar ANTES de que se cree el Presenter/ventana.
    // El hook de SetPaintModeFromUIThread fuerza paint_mode_=2 en la primera
    // transicion, de modo que el Presenter no nace en kUIThreadOnRequest. El
    // hook de DXGI Present se instala tambien aqui para enganchar el swap
    // chain en cuanto IDXGIFactory2::CreateSwapChainForHwnd lo cree.
    NdEarlyInit();
    REXLOG_INFO("neverdead: configured gpu_plugin = 'xenos' for D3D12 presentation");
  }

  void OnPostSetup() override {
    // Reintento idempotente: rexgpu-xenos.dll puede cargarse durante Setup, y
    // el TOML/cvar de render_target_path se lee en la inicializacion GPU.
    NdEarlyInit();
    auto* rt = runtime();
    if (rt != nullptr && rt->file_system() != nullptr) {
      auto* fs = rt->file_system();
      std::string game_target;
      if (fs->FindSymbolicLink("game:", game_target)) {
        REXLOG_INFO("neverdead: VFS game: mapped to '{}'", game_target);
        fs->RegisterSymbolicLink("dvd:", game_target);
        fs->RegisterSymbolicLink("cdrom0:", game_target);
        fs->RegisterSymbolicLink("assets:", game_target);
        fs->RegisterSymbolicLink("FMV:", game_target + "\\FMV");
        fs->RegisterSymbolicLink("GUIMenu:", game_target + "\\GUIMenu");
        fs->RegisterSymbolicLink("Fonts:", game_target + "\\Fonts");
        fs->RegisterSymbolicLink("Text:", game_target + "\\Text");
        fs->RegisterSymbolicLink("misc:", game_target + "\\misc");
        fs->RegisterSymbolicLink("sounds:", game_target + "\\sounds");
        fs->RegisterSymbolicLink("D:", game_target);
        fs->RegisterSymbolicLink("d:", game_target);
      } else {
        std::string fallback = "\\Device\\Harddisk0\\Partition1";
        fs->RegisterSymbolicLink("dvd:", fallback);
        fs->RegisterSymbolicLink("cdrom0:", fallback);
        fs->RegisterSymbolicLink("assets:", fallback);
        fs->RegisterSymbolicLink("FMV:", fallback + "\\FMV");
        fs->RegisterSymbolicLink("GUIMenu:", fallback + "\\GUIMenu");
        fs->RegisterSymbolicLink("Fonts:", fallback + "\\Fonts");
        fs->RegisterSymbolicLink("Text:", fallback + "\\Text");
        fs->RegisterSymbolicLink("misc:", fallback + "\\misc");
        fs->RegisterSymbolicLink("sounds:", fallback + "\\sounds");
        fs->RegisterSymbolicLink("D:", fallback);
        fs->RegisterSymbolicLink("d:", fallback);
      }
      REXLOG_INFO("neverdead: VFS fallback aliases registered (dvd:, cdrom0:, assets:, FMV:, GUIMenu:, Fonts:, Text:, misc:, sounds:, D:, d:)");
    }
  }
};
