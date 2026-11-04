// neverdead - ReXGlue Recompiled Project

#include "generated/neverdead_default/neverdead_init.h"
#include "neverdead_app.h"
#include <rex/cvar.h>
#include <filesystem>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <shellapi.h>
#endif

static std::unique_ptr<rex::ui::WindowedApp> NeverdeadAppCreator(rex::ui::WindowedAppContext& ctx) {
#ifdef _WIN32
    int wargc = 0;
    wchar_t** wargv = CommandLineToArgvW(GetCommandLineW(), &wargc);
    if (wargv) {
        for (int i = 1; i < wargc; ++i) {
            if (wcscmp(wargv[i], L"--cache_path") == 0 && i + 1 < wargc) {
                char utf8[MAX_PATH] = {};
                WideCharToMultiByte(CP_UTF8, 0, wargv[i + 1], -1, utf8, sizeof(utf8) - 1, nullptr, nullptr);
                rex::cvar::SetFlagByName("cache_root", utf8);
            }
        }
        LocalFree(wargv);
    }
#endif

    auto exe_dir = rex::filesystem::GetExecutableFolder();
    auto root_dir = exe_dir.parent_path();
    std::error_code ec;

    std::string cur_data = rex::cvar::GetFlagByName("game_data_root");
    if (cur_data.empty()) {
        if (std::filesystem::is_directory(exe_dir / "assets", ec)) {
            rex::cvar::SetFlagByName("game_data_root", (exe_dir / "assets").string());
        } else if (std::filesystem::is_directory(root_dir / "assets", ec)) {
            rex::cvar::SetFlagByName("game_data_root", (root_dir / "assets").string());
        } else {
            rex::cvar::SetFlagByName("game_data_root", "assets");
        }
    }

    std::string cur_cache = rex::cvar::GetFlagByName("cache_root");
    if (cur_cache.empty()) {
        auto target_cache = std::filesystem::is_directory(root_dir / "assets", ec) ? (root_dir / "cache") : (exe_dir / "cache");
        std::filesystem::create_directories(target_cache, ec);
        rex::cvar::SetFlagByName("cache_root", target_cache.string());
    }

    std::string cur_config = rex::cvar::GetFlagByName("config_path");
    if (cur_config.empty()) {
        if (std::filesystem::is_regular_file(exe_dir / "neverdead.toml", ec)) {
            rex::cvar::SetFlagByName("config_path", (exe_dir / "neverdead.toml").string());
        } else if (std::filesystem::is_regular_file(root_dir / "neverdead.toml", ec)) {
            rex::cvar::SetFlagByName("config_path", (root_dir / "neverdead.toml").string());
        }
    }

    return NeverdeadApp::Create(ctx);
}

REX_DEFINE_APP(neverdead, NeverdeadAppCreator)
