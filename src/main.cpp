// neverdead - ReXGlue Recompiled Project

#include "generated/neverdead_default/neverdead_init.h"
#include "neverdead_app.h"
#include <rex/cvar.h>

#include <filesystem>
#include <string>

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
  const auto exe_dir = rex::filesystem::GetExecutableFolder();

#ifdef _WIN32
  int wargc = 0;
  wchar_t** wargv = CommandLineToArgvW(GetCommandLineW(), &wargc);
  if (wargv) {
    for (int i = 1; i < wargc; ++i) {
      std::wstring raw_cache;
      if (wcscmp(wargv[i], L"--cache_path") == 0 && i + 1 < wargc) {
        raw_cache = wargv[++i];
      } else if (wcsncmp(wargv[i], L"--cache_path=", 13) == 0 && wargv[i][13] != L'\0') {
        raw_cache = wargv[i] + 13;
      }
      if (!raw_cache.empty()) {
        std::filesystem::path resolved(raw_cache);
        if (resolved.is_relative()) {
          // Resolve relative --cache_path against the executable directory for CWD independence.
          resolved = (exe_dir / resolved).lexically_normal();
        }
        const std::wstring& wide = resolved.native();
        const int needed =
            WideCharToMultiByte(CP_UTF8, 0, wide.c_str(), -1, nullptr, 0, nullptr, nullptr);
        if (needed > 1) {
          std::string utf8(static_cast<size_t>(needed - 1), '\0');
          WideCharToMultiByte(CP_UTF8, 0, wide.c_str(), -1, utf8.data(), needed, nullptr, nullptr);
          rex::cvar::SetFlagByName("cache_root", utf8);
        }
      }
    }
    LocalFree(wargv);
  }
#endif

  if (rex::cvar::GetFlagByName("game_data_root").empty()) {
    rex::cvar::SetFlagByName("game_data_root", neverdead::paths::ResolveAssetsDir(exe_dir).string());
  }

  if (rex::cvar::GetFlagByName("config_path").empty()) {
    const auto cfg = neverdead::paths::ResolveConfigPath(exe_dir);
    if (!cfg.empty()) {
      rex::cvar::SetFlagByName("config_path", cfg.string());
    }
  }

  return NeverdeadApp::Create(ctx);
}

REX_DEFINE_APP(neverdead, NeverdeadAppCreator)
