// neverdead - ReXGlue Recompiled Project

#pragma once

#include <bit>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <memory>
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

namespace neverdead::paths {

inline std::filesystem::path ResolveAssetsDir(const std::filesystem::path& exe_dir) {
  std::error_code ec;
  if (std::filesystem::is_directory(exe_dir / "assets", ec)) {
    return exe_dir / "assets";
  }
  const auto root_dir = exe_dir.parent_path();
  if (std::filesystem::is_directory(root_dir / "assets", ec)) {
    return root_dir / "assets";
  }
  return exe_dir / "assets";
}

inline std::filesystem::path ResolveLinkBaseDir(const std::filesystem::path& exe_dir) {
  std::error_code ec;
  if (std::filesystem::is_directory(exe_dir / "assets", ec)) {
    return exe_dir;
  }
  const auto root_dir = exe_dir.parent_path();
  if (std::filesystem::is_directory(root_dir / "assets", ec)) {
    return root_dir;
  }
  return exe_dir;
}

inline std::filesystem::path ResolveCacheDir(const std::filesystem::path& exe_dir) {
  std::error_code ec;
  const auto root_dir = exe_dir.parent_path();
  if (!std::filesystem::is_directory(exe_dir / "assets", ec) &&
      std::filesystem::is_directory(root_dir / "assets", ec)) {
    return root_dir / "cache";
  }
  return exe_dir / "cache";
}

inline std::filesystem::path ResolveConfigPath(const std::filesystem::path& exe_dir) {
  std::error_code ec;
  if (std::filesystem::is_regular_file(exe_dir / "neverdead.toml", ec)) {
    return exe_dir / "neverdead.toml";
  }
  const auto root_dir = exe_dir.parent_path();
  if (std::filesystem::is_regular_file(root_dir / "neverdead.toml", ec)) {
    return root_dir / "neverdead.toml";
  }
  return {};
}

}  // namespace neverdead::paths

class NeverdeadApp : public rex::ReXApp {
 public:
  using rex::ReXApp::ReXApp;

  static std::unique_ptr<rex::ui::WindowedApp> Create(
      rex::ui::WindowedAppContext& ctx) {
    return std::unique_ptr<NeverdeadApp>(new NeverdeadApp(ctx, "neverdead",
        PPCImageConfig));
  }

  // Ensures the SDK maps sections from assets/default.xex into guest memory.
  void OnLoadXexImage(std::string& xex_image) override {
    xex_image = "game:\\default.xex";
  }

  static void EnsureGuestImageWritable(uint8_t* guest_base) {
#ifdef _WIN32
    // Xbox 360 does not enforce read-only .rdata protection; Asura engine
    // modifies global tables in .rdata. Walk MBI regions across [0x82000000, 0x82C10000).
    uint8_t* curr = guest_base;
    uint8_t* end = guest_base + 0x00C10000u;
    size_t count = 0;
    while (curr < end) {
      MEMORY_BASIC_INFORMATION mbi{};
      if (!VirtualQuery(curr, &mbi, sizeof(mbi))) break;
      if (mbi.State == MEM_COMMIT &&
          mbi.Protect != PAGE_EXECUTE_READWRITE &&
          mbi.Protect != PAGE_READWRITE) {
        DWORD old_prot = 0;
        DWORD target_prot = (mbi.AllocationProtect & 0xF0) ? PAGE_EXECUTE_READWRITE : PAGE_READWRITE;
        if (VirtualProtect(mbi.BaseAddress, mbi.RegionSize, target_prot, &old_prot) ||
            VirtualProtect(mbi.BaseAddress, mbi.RegionSize, PAGE_READWRITE, &old_prot)) {
          ++count;
        }
      }
      curr = reinterpret_cast<uint8_t*>(mbi.BaseAddress) + mbi.RegionSize;
    }
    REXLOG_INFO("neverdead: unlocked {} guest image regions to writable", count);
#else
    (void)guest_base;
#endif
  }

  void MapBaseExeSections() {
    auto* rt = runtime();
    if (rt == nullptr || rt->memory() == nullptr) return;
    auto* mem = rt->memory();
    uint8_t* guest_base = mem->TranslateVirtual<uint8_t*>(0x82000000u);
    if (!guest_base) {
      REXLOG_ERROR("neverdead: MapBaseExeSections cannot translate guest 0x82000000");
      return;
    }

    EnsureGuestImageWritable(guest_base);

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
      return;
    }

    // Fill zeroed holes only; never overwrite non-zero guest words.
    // .data uses shift 0; .rdata and .post remain disabled by default because
    // the XEX loader maps .rdata accurately and .rdata offsets vary per range.
    auto env_on = [](const char* name) {
      const char* v = std::getenv(name);
      return v != nullptr && v[0] != '\0' && v[0] != '0';
    };
    auto fill_holes = [&](const char* tag, uint32_t rva, uint32_t size,
                          uint32_t shift, bool enabled) -> uint32_t {
      if (!enabled) {
        if (neverdead::fix::NdiDiagEnabled()) {
          REXLOG_INFO("neverdead: MapBaseExeSections {} rva=0x{:X} size=0x{:X} disabled",
                      tag, rva, size);
        }
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
      REXLOG_INFO("neverdead: MapBaseExeSections {} rva=0x{:X} size=0x{:X} shift=0x{:X} words_filled={}",
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
    REXLOG_INFO("neverdead: MapBaseExeSections holes-only complete "
                "(.rdata={}, .pdata={}, .data={}, .post={}; IAT [0x600,0xA40) preserved)",
                h1, h2, h3, h4);
  }

  void SetupGuestMemoryHooks() {
    auto* rt = runtime();
    if (rt == nullptr || rt->memory() == nullptr) return;
    auto* mem = rt->memory();
    nd_guest_mem_set(static_cast<void*>(mem));
  }

  void RunPostLoadDiagnostics(rex::memory::Memory* mem) {
    constexpr uint32_t kObjBase = 0x82144930u;
    constexpr uint32_t kSlot = kObjBase + 16u;
    constexpr uint32_t kExpectVtable = 0x8249EF98u;
    const uint32_t raw = *mem->TranslateVirtual<const uint32_t*>(kSlot);
    const uint32_t slot = std::byteswap(raw);
    REXLOG_INFO("neverdead: post-load probe obj=0x{:08X} slot=0x{:08X} value=0x{:08X}",
                kObjBase, kSlot, slot);
    if (slot == kExpectVtable) {
      REXLOG_INFO("neverdead: .rdata OK (VTABLE 0x{:08X} present)", kExpectVtable);
    }

    constexpr uint32_t kVtableAddr = 0x82140958u;
    const uint32_t v0 = std::byteswap(*mem->TranslateVirtual<const uint32_t*>(kVtableAddr));
    const uint32_t v1 = std::byteswap(*mem->TranslateVirtual<const uint32_t*>(kVtableAddr + 4));
    REXLOG_INFO("neverdead: post-load vtable@82140958 = [0x{:08X}, 0x{:08X}]", v0, v1);

    struct Anchor {
      uint32_t word0;
      uint32_t word1;
      const char* tag;
    };
    constexpr uint32_t kImageBase = 0x82000000u;
    constexpr uint32_t kImageSize = 0xA10000u;
    const Anchor anchors[] = {
        {0x8249EF98u, 0x825C3370u, "vtable@82144940"},
        {0x7D8802A6u, 0x48588A95u, "ctor@822032B0"},
        {0x7D8802A6u, 0x482B1F51u, "entry@824D9DF8"},
        {0x48031BADu, 0x48000014u, "stub@8278BD48"},
    };
    const uint32_t* base = mem->TranslateVirtual<const uint32_t*>(kImageBase);
    if (base == nullptr) {
      REXLOG_ERROR("neverdead: map-diag failed to translate 0x{:08X}", kImageBase);
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
        REXLOG_WARN("neverdead: map-diag {} no match in guest image", a.tag);
      }
    }

    auto dump = [&](const char* tag, uint32_t va, uint32_t nbytes) {
      const auto* p = mem->TranslateVirtual<const uint8_t*>(va);
      if (p == nullptr) {
        REXLOG_WARN("neverdead: dump {} 0x{:08X} untranslatable", tag, va);
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
  }

  void OnPostLoadXexImage() override {
    auto* rt = runtime();
    if (rt == nullptr || rt->memory() == nullptr) {
      return;
    }
    auto* mem = rt->memory();
    const bool diag = neverdead::fix::NdiDiagEnabled();

    if (diag) {
      const uint32_t va = 0x82144930u;
      if (const auto* p = mem->TranslateVirtual<const uint8_t*>(va)) {
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

    constexpr uint32_t kSlot = 0x82144930u + 16u;
    constexpr uint32_t kExpectVtable = 0x8249EF98u;
    if (const auto* slot_ptr = mem->TranslateVirtual<const uint32_t*>(kSlot)) {
      const uint32_t slot = std::byteswap(*slot_ptr);
      if (slot != kExpectVtable) {
        REXLOG_WARN("neverdead: .rdata missing at slot (got 0x{:08X}, expected 0x{:08X}); check game_data_root/XEX",
                    slot, kExpectVtable);
      }
    }

    if (diag) {
      RunPostLoadDiagnostics(mem);
    }

#ifdef _WIN32
    // Pre-commit guest virtual and physical arenas to avoid boot-time VEH page faults.
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

    void* host_stacks = mem->TranslateVirtual<void*>(0x70000000u);
    if (host_stacks != nullptr) {
      void* allocated = VirtualAlloc(host_stacks, 0x0F000000, MEM_COMMIT, PAGE_READWRITE);
      DWORD err = allocated ? 0 : GetLastError();
      REXLOG_INFO("neverdead: guest stacks/user heap commit at {:p} (240MB) result={:p} (err={})",
                  host_stacks, allocated, err);
    }

    void* host_pre_img = mem->TranslateVirtual<void*>(0x80000000u);
    if (host_pre_img != nullptr) {
      void* allocated = VirtualAlloc(host_pre_img, 0x02000000, MEM_COMMIT, PAGE_READWRITE);
      DWORD err = allocated ? 0 : GetLastError();
      REXLOG_INFO("neverdead: pre-image heap commit [0x80000000, 0x82000000) at {:p} result={:p} (err={})",
                  host_pre_img, allocated, err);
    }

    // Never call VirtualAlloc(PAGE_READWRITE) on [0x82000000, 0x82C10000) so
    // PAGE_EXECUTE_READWRITE on XEX image pages is preserved.

    void* host_post_img = mem->TranslateVirtual<void*>(0x82C10000u);
    if (host_post_img != nullptr) {
      void* allocated = VirtualAlloc(host_post_img, 0x0D3F0000, MEM_COMMIT, PAGE_READWRITE);
      DWORD err = allocated ? 0 : GetLastError();
      REXLOG_INFO("neverdead: post-image heap commit [0x82C10000, 0x90000000) at {:p} result={:p} (err={})",
                  host_post_img, allocated, err);
    }

    void* host_v90 = mem->TranslateVirtual<void*>(0x90000000u);
    if (host_v90 != nullptr) {
      void* allocated = VirtualAlloc(host_v90, 0x02000000, MEM_COMMIT, PAGE_READWRITE);
      DWORD err = allocated ? 0 : GetLastError();
      REXLOG_INFO("neverdead: extended kernel heap commit [0x90000000, 0x92000000) at {:p} result={:p} (err={})",
                  host_v90, allocated, err);
    }

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

    SetupGuestMemoryHooks();
  }

  void OnConfigurePaths(rex::PathConfig& paths) override {
    const auto exe_dir = rex::filesystem::GetExecutableFolder();
    std::error_code ec;

    if (paths.game_data_root.empty() ||
        (paths.game_data_root == "assets" && !std::filesystem::exists(paths.game_data_root, ec))) {
      paths.game_data_root = neverdead::paths::ResolveAssetsDir(exe_dir);
      REXLOG_INFO("neverdead: auto-configured game_data_root: {}", paths.game_data_root.string());
    }

    if (!std::filesystem::exists(paths.game_data_root / "default.xex", ec)) {
      REXLOG_INFO("neverdead: default.xex not found in '{}'; launching First-Time Setup Wizard",
                  paths.game_data_root.string());
      NdRunFirstLaunchSetupWizard(paths.game_data_root);
    }

    // Creates asset directory links/junctions and ensures CESA.bik fallback exists.
    NdEnsureAssetJunctionsOrLinks(neverdead::paths::ResolveLinkBaseDir(exe_dir), paths.game_data_root);

    if (paths.config_path.empty() || !std::filesystem::is_regular_file(paths.config_path, ec)) {
      auto cfg = neverdead::paths::ResolveConfigPath(exe_dir);
      if (!cfg.empty()) {
        paths.config_path = cfg;
        REXLOG_INFO("neverdead: config_path set to: {}", cfg.string());
      }
    }

    if (paths.cache_root.empty()) {
      paths.cache_root = neverdead::paths::ResolveCacheDir(exe_dir);
      REXLOG_INFO("neverdead: auto-configured cache_root: {}", paths.cache_root.string());
    }
    std::filesystem::create_directories(paths.cache_root, ec);
  }

  void OnPreSetup(rex::RuntimeConfig& config) override {
    config.gpu_plugin = "xenos";
    // Install early UI/DXGI presentation hooks before the window and presenter are created.
    NdEarlyInit();
    REXLOG_INFO("neverdead: configured gpu_plugin = 'xenos' for D3D12 presentation");
  }

  void OnPostSetup() override {
    // Idempotent retry in case rexgpu-xenos.dll is loaded during Setup.
    NdEarlyInit();
    auto* rt = runtime();
    if (rt != nullptr && rt->file_system() != nullptr) {
      auto* fs = rt->file_system();
      std::string game_target;
      if (!fs->FindSymbolicLink("game:", game_target)) {
        game_target = "\\Device\\Harddisk0\\Partition1";
      } else {
        REXLOG_INFO("neverdead: VFS game: mapped to '{}'", game_target);
      }
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
      REXLOG_INFO("neverdead: VFS aliases registered (dvd:, cdrom0:, assets:, FMV:, GUIMenu:, Fonts:, Text:, misc:, sounds:, D:, d:)");
    }
  }
};
