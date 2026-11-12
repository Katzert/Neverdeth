// Neverdeth - In-game settings dialog (F10) and INI configuration persistence.

#include "nd_internal.h"

#include <cstdlib>
#include <filesystem>
#include <thread>

#include <rex/logging.h>

#ifdef _WIN32
#pragma comment(lib, "user32.lib")
#pragma comment(lib, "gdi32.lib")

std::atomic<HWND> g_config_hwnd{nullptr};
int g_window_mode = 0;

namespace {

enum {
  IDC_CFG_COMBO_WND = 1001,
  IDC_CFG_COMBO_FPS = 1002,
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
  uint32_t va;    // Primary guest .data byte flag
  uint32_t va2;   // Companion flag (e.g., cascaded shadows), 0 if unused
  bool inverted;  // true when 0 = enabled in Asura engine (Dynamic Shadows)
};

// Ordered by 2-column layout: 0..4 left column, 5..9 right column.
const NdRenderToggle kToggles[] = {
    {L"Dynamic Shadows", L"DynamicShadows", IDC_CFG_CHK_SHADOW, 0x828E1FB7, 0x828E1FC5, true},
    {L"Ambient Occlusion (SSAO)", L"AmbientOcclusionSSAO", IDC_CFG_CHK_SSAO, 0x828E1FC7, 0, false},
    {L"Motion Blur", L"MotionBlur", IDC_CFG_CHK_MBLUR, 0x82801CEF, 0, false},
    {L"HDR Bloom", L"BloomHDR", IDC_CFG_CHK_BLOOM, 0x828E1FC6, 0, false},
    {L"God Rays (Light Shafts)", L"LightShafts", IDC_CFG_CHK_LIGHTSHAFTS, 0x828E1FBF, 0, false},
    {L"Volumetric Fog", L"VolumetricFog", IDC_CFG_CHK_FOG, 0x828E1FC0, 0, false},
    {L"Post-Processing (FSFX)", L"PostProcessingFSFX", IDC_CFG_CHK_FSFX, 0x828E1FC1, 0, false},
    {L"Particles (PFX)", L"ParticlesPFX", IDC_CFG_CHK_PFX, 0x828E1FC8, 0, false},
    {L"Anti-Aliasing (RMLAA)", L"AntialiasingRMLAA", IDC_CFG_CHK_RMLAA, 0x828E1FC9, 0, false},
    {L"Show HUD", L"ShowHUD", IDC_CFG_CHK_HUD, 0x828E1FC3, 0, false},
};

const uint32_t kPresetFlags[] = {
    0x82801CEF,  // MotionBlur
    0x828E1FC7,  // SSAO
    0x828E1FC6,  // HDR Bloom
    0x828E1FBF,  // LightShafts
    0x828E1FC0,  // Fog
    0x828E1FC1,  // FSFX
    0x828E1FC9,  // RMLAA
    0x828E1FB9,  // Decals
    0x828E1FBA,  // ObjectDecals
    0x828E1FBB,  // Photons
    0x828E1FBC,  // Projectors
    0x828E1FBE,  // Coronas
    0x828E1FC8,  // Weather / Particles
};
constexpr uint32_t kHudVa = 0x828E1FC3;

std::atomic<bool> g_config_ui_open{false};

// Prevent duplicate F10/ESC transitions when both WM_KEYDOWN and WM_TIMER poll.
bool s_f10_wm_handled = false;
bool s_esc_wm_handled = false;
bool s_f10_prev = false;
bool s_esc_prev = false;

void NdApplyDarkComboTheme(HWND ctrl) {
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

bool NdToggleValue(const NdRenderToggle& t) {
  if (nd_translate_va(t.va) == nullptr) {
    return true;
  }
  bool raw = NdReadGuestU8(t.va) != 0;
  if (t.va2 != 0) {
    raw = raw && (NdReadGuestU8(t.va2) != 0);
  }
  return t.inverted ? !raw : raw;
}

std::wstring NdGetSettingsIniPath() {
  wchar_t exe_path[MAX_PATH] = {};
  GetModuleFileNameW(nullptr, exe_path, MAX_PATH);
  const std::filesystem::path exe_dir = std::filesystem::path(exe_path).parent_path();
  const std::filesystem::path ini = exe_dir / "config" / "settings.ini";
  if (std::filesystem::exists(ini)) {
    return ini.wstring();
  }
  const std::filesystem::path parent_ini = exe_dir.parent_path() / "config" / "settings.ini";
  if (std::filesystem::exists(parent_ini)) {
    return parent_ini.wstring();
  }
  std::error_code ec;
  std::filesystem::create_directories(exe_dir / "config", ec);
  return ini.wstring();
}

void NdSyncCheckboxesFromMemory(HWND hwnd) {
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
    SendMessageW(chkUnlock, BM_SETCHECK, g_unlock_framerate.load() ? BST_CHECKED : BST_UNCHECKED,
                 0);
  }
  HWND chkShaders = GetDlgItem(hwnd, IDC_CFG_CHK_SHOW_SHADERS);
  if (chkShaders != nullptr) {
    SendMessageW(chkShaders, BM_SETCHECK,
                 g_show_shader_compilation.load() ? BST_CHECKED : BST_UNCHECKED, 0);
  }
}

void NdApplyPreset(HWND hwnd, bool high_quality) {
  int written = 0;
  // Dynamic shadows use inverted polarity (0 = enabled, 1 = disabled).
  const uint8_t shadow_val = high_quality ? 0 : 1;
  if (NdWriteGuestU8(0x828E1FB7, shadow_val)) ++written;
  if (NdWriteGuestU8(0x828E1FC5, shadow_val)) ++written;

  // Preserve render pass stage 2 guard.
  NdWriteGuestU8(0x828E1FBD, 1);

  const uint8_t val = high_quality ? 1 : 0;
  for (uint32_t va : kPresetFlags) {
    if (NdWriteGuestU8(va, val)) ++written;
  }
  NdWriteGuestU8(kHudVa, 1);
  NdSyncCheckboxesFromMemory(hwnd);
  NdSaveSettingsToIni();
  if (NdIsDiagEnabled()) {
    REXLOG_INFO("neverdead: [CONFIG-UI] Preset '{}': {} flags updated (shadows={}, others={})",
                high_quality ? "Original Quality" : "Performance", written, shadow_val, val);
  }
}

void NdApplyWindowMode(int idx) {
  g_window_mode = idx;
  HWND ghwnd = NdGetGameWindow();
  if (ghwnd == nullptr) return;
  if (idx == 0) {
    SetWindowLongPtrW(ghwnd, GWL_STYLE, WS_OVERLAPPEDWINDOW | WS_VISIBLE);
    RECT rc = {0, 0, 1280, 720};
    AdjustWindowRect(&rc, WS_OVERLAPPEDWINDOW, FALSE);
    const int w = rc.right - rc.left;
    const int h = rc.bottom - rc.top;
    const int x = (GetSystemMetrics(SM_CXSCREEN) - w) / 2;
    const int y = (GetSystemMetrics(SM_CYSCREEN) - h) / 2;
    SetWindowPos(ghwnd, HWND_NOTOPMOST, x > 0 ? x : 0, y > 0 ? y : 0, w, h,
                 SWP_FRAMECHANGED | SWP_SHOWWINDOW);
  } else if (idx == 1) {
    SetWindowLongPtrW(ghwnd, GWL_STYLE, WS_OVERLAPPEDWINDOW | WS_VISIBLE);
    RECT rc = {0, 0, 1920, 1080};
    AdjustWindowRect(&rc, WS_OVERLAPPEDWINDOW, FALSE);
    const int w = rc.right - rc.left;
    const int h = rc.bottom - rc.top;
    const int x = (GetSystemMetrics(SM_CXSCREEN) - w) / 2;
    const int y = (GetSystemMetrics(SM_CYSCREEN) - h) / 2;
    SetWindowPos(ghwnd, HWND_NOTOPMOST, x > 0 ? x : 0, y > 0 ? y : 0, w, h,
                 SWP_FRAMECHANGED | SWP_SHOWWINDOW);
  } else {
    SetWindowLongPtrW(ghwnd, GWL_STYLE, WS_POPUP | WS_VISIBLE);
    SetWindowPos(ghwnd, HWND_TOPMOST, 0, 0, GetSystemMetrics(SM_CXSCREEN),
                 GetSystemMetrics(SM_CYSCREEN), SWP_FRAMECHANGED | SWP_SHOWWINDOW);
  }
  NdSaveSettingsToIni();
}

void NdHideConfigAndFocusGame(HWND hwnd) {
  ShowWindow(hwnd, SW_HIDE);
  if (HWND g = NdGetGameWindow()) {
    SetForegroundWindow(g);
    SetFocus(g);
  }
}

void NdDrawBanner(HDC hdc) {
  static HBITMAP s_banner = nullptr;
  static bool s_loaded = false;
  if (!s_loaded) {
    s_loaded = true;
    s_banner = reinterpret_cast<HBITMAP>(LoadImageW(
        GetModuleHandleW(nullptr), MAKEINTRESOURCEW(2), IMAGE_BITMAP, 0, 0, LR_CREATEDIBSECTION));
    if (s_banner == nullptr) {
      wchar_t exe_buf[MAX_PATH] = {};
      GetModuleFileNameW(nullptr, exe_buf, MAX_PATH);
      const std::filesystem::path exe_dir = std::filesystem::path(exe_buf).parent_path();
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

void NdCreateConfigControls(HWND hwnd) {
  HFONT hFont = CreateFontW(16, 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE, DEFAULT_CHARSET,
                            OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
                            DEFAULT_PITCH | FF_DONTCARE, L"Segoe UI");
  HFONT hFontBold = CreateFontW(16, 0, 0, 0, FW_SEMIBOLD, FALSE, FALSE, FALSE, DEFAULT_CHARSET,
                                OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
                                DEFAULT_PITCH | FF_DONTCARE, L"Segoe UI");
  auto set_font = [](HWND ctrl, HFONT f) {
    SendMessageW(ctrl, WM_SETFONT, reinterpret_cast<WPARAM>(f), TRUE);
  };

  // Row 1: Display mode and framerate target
  HWND lblScreen = CreateWindowW(L"STATIC", L"Display:", WS_CHILD | WS_VISIBLE, 20, 65, 60, 20,
                                 hwnd, nullptr, nullptr, nullptr);
  set_font(lblScreen, hFont);
  HWND cbWnd = CreateWindowExW(0, L"COMBOBOX", nullptr,
                               WS_CHILD | WS_VISIBLE | CBS_DROPDOWNLIST | WS_VSCROLL, 85, 61,
                               165, 200, hwnd, reinterpret_cast<HMENU>(IDC_CFG_COMBO_WND), nullptr,
                               nullptr);
  set_font(cbWnd, hFont);
  SendMessageW(cbWnd, CB_ADDSTRING, 0, reinterpret_cast<LPARAM>(L"Windowed 720p"));
  SendMessageW(cbWnd, CB_ADDSTRING, 0, reinterpret_cast<LPARAM>(L"Windowed 1080p"));
  SendMessageW(cbWnd, CB_ADDSTRING, 0, reinterpret_cast<LPARAM>(L"Borderless Fullscreen"));
  SendMessageW(cbWnd, CB_SETCURSEL, g_window_mode, 0);
  NdApplyDarkComboTheme(cbWnd);

  HWND lblFps = CreateWindowW(L"STATIC", L"FPS:", WS_CHILD | WS_VISIBLE, 275, 65, 35, 20, hwnd,
                              nullptr, nullptr, nullptr);
  set_font(lblFps, hFont);
  HWND cbFps = CreateWindowExW(0, L"COMBOBOX", nullptr,
                               WS_CHILD | WS_VISIBLE | CBS_DROPDOWNLIST | WS_VSCROLL, 315, 61,
                               180, 200, hwnd, reinterpret_cast<HMENU>(IDC_CFG_COMBO_FPS), nullptr,
                               nullptr);
  set_font(cbFps, hFont);
  SendMessageW(cbFps, CB_ADDSTRING, 0, reinterpret_cast<LPARAM>(L"Unlimited (Unlocked)"));
  SendMessageW(cbFps, CB_ADDSTRING, 0, reinterpret_cast<LPARAM>(L"60 FPS"));
  SendMessageW(cbFps, CB_ADDSTRING, 0, reinterpret_cast<LPARAM>(L"30 FPS"));
  const int fps_sel =
      (g_fps_target.load() <= 0.0f) ? 0 : (g_fps_target.load() > 45.0f ? 1 : 2);
  SendMessageW(cbFps, CB_SETCURSEL, fps_sel, 0);
  NdApplyDarkComboTheme(cbFps);

  // Row 2: One-click quality presets
  HWND btnHigh = CreateWindowW(L"BUTTON", L"Preset: Original Quality",
                               WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON | BS_OWNERDRAW, 20, 99, 230,
                               28, hwnd, reinterpret_cast<HMENU>(IDC_CFG_PRESET_HIGH), nullptr,
                               nullptr);
  set_font(btnHigh, hFontBold);
  NdDisableTheme(btnHigh);
  HWND btnLow = CreateWindowW(L"BUTTON", L"Preset: Performance (Max FPS)",
                              WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON | BS_OWNERDRAW, 275, 99, 220,
                              28, hwnd, reinterpret_cast<HMENU>(IDC_CFG_PRESET_LOW), nullptr,
                              nullptr);
  set_font(btnLow, hFontBold);
  NdDisableTheme(btnLow);

  // Graphics pass checkboxes (2 columns)
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

  // Extra runtime options
  HWND chkLuma = CreateWindowW(L"BUTTON", L"Adaptive Exposure Opt.",
                               WS_CHILD | WS_VISIBLE | BS_AUTOCHECKBOX, 25, 268, 220, 22, hwnd,
                               reinterpret_cast<HMENU>(IDC_CFG_CHK_LUMA_OPT), nullptr, nullptr);
  set_font(chkLuma, hFont);
  NdDisableTheme(chkLuma);
  SendMessageW(chkLuma, BM_SETCHECK, g_opt_luma_resolve.load() ? BST_CHECKED : BST_UNCHECKED, 0);

  HWND chkUnlock = CreateWindowW(L"BUTTON", L"Unlock FPS in Gameplay",
                                 WS_CHILD | WS_VISIBLE | BS_AUTOCHECKBOX, 275, 268, 220, 22, hwnd,
                                 reinterpret_cast<HMENU>(IDC_CFG_CHK_UNLOCK_FPS), nullptr, nullptr);
  set_font(chkUnlock, hFont);
  NdDisableTheme(chkUnlock);
  SendMessageW(chkUnlock, BM_SETCHECK, g_unlock_framerate.load() ? BST_CHECKED : BST_UNCHECKED, 0);

  HWND chkShaders = CreateWindowW(
      L"BUTTON", L"Show Shader Compilation", WS_CHILD | WS_VISIBLE | BS_AUTOCHECKBOX, 25, 295, 240,
      22, hwnd, reinterpret_cast<HMENU>(IDC_CFG_CHK_SHOW_SHADERS), nullptr, nullptr);
  set_font(chkShaders, hFont);
  NdDisableTheme(chkShaders);
  SendMessageW(chkShaders, BM_SETCHECK,
               g_show_shader_compilation.load() ? BST_CHECKED : BST_UNCHECKED, 0);

  // Bottom action buttons
  HWND btnClose = CreateWindowW(L"BUTTON", L"Resume Game (F10 / ESC)",
                                WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON | BS_OWNERDRAW, 20, 328, 230,
                                32, hwnd, reinterpret_cast<HMENU>(IDC_CFG_BTN_CLOSE), nullptr,
                                nullptr);
  set_font(btnClose, hFontBold);
  NdDisableTheme(btnClose);
  HWND btnExit = CreateWindowW(L"BUTTON", L"Exit to Desktop",
                               WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON | BS_OWNERDRAW, 275, 328, 220,
                               32, hwnd, reinterpret_cast<HMENU>(IDC_CFG_BTN_EXIT_DESKTOP), nullptr,
                               nullptr);
  set_font(btnExit, hFontBold);
  NdDisableTheme(btnExit);
}

LRESULT CALLBACK NdConfigWndProc(HWND hwnd, UINT msg, WPARAM wParam, LPARAM lParam) {
  static HBRUSH s_bg_brush = CreateSolidBrush(RGB(22, 22, 28));
  static HBRUSH s_ctrl_brush = CreateSolidBrush(RGB(34, 34, 44));

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
          if (g_window_mode == 2 && parent_hwnd != nullptr && IsWindow(parent_hwnd)) {
            const int screen_w = GetSystemMetrics(SM_CXSCREEN);
            const int screen_h = GetSystemMetrics(SM_CYSCREEN);
            SetWindowPos(parent_hwnd, HWND_TOPMOST, 0, 0, screen_w, screen_h,
                         SWP_SHOWWINDOW | SWP_FRAMECHANGED);
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
          REXLOG_INFO("neverdead: [CLEAN-EXIT] Exit to Desktop selected from settings menu");
        }
        NdFlushPsoBundle();
        NdSavePipelineLibraryCache();
        fflush(nullptr);
        _flushall();
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
        const bool on =
            SendMessageW(reinterpret_cast<HWND>(lParam), BM_GETCHECK, 0, 0) == BST_CHECKED;
        g_opt_luma_resolve.store(on, std::memory_order_relaxed);
        NdSaveSettingsToIni();
        return 0;
      }
      if (id == IDC_CFG_CHK_UNLOCK_FPS && code == BN_CLICKED) {
        const bool on =
            SendMessageW(reinterpret_cast<HWND>(lParam), BM_GETCHECK, 0, 0) == BST_CHECKED;
        g_unlock_framerate.store(on, std::memory_order_relaxed);
        if (on) {
          if (g_fps_target.load(std::memory_order_relaxed) == 30.0f) {
            g_fps_target.store(60.0f, std::memory_order_relaxed);
          }
        } else {
          g_fps_target.store(30.0f, std::memory_order_relaxed);
        }
        HWND cb = GetDlgItem(hwnd, IDC_CFG_COMBO_FPS);
        if (cb) {
          const float t = g_fps_target.load(std::memory_order_relaxed);
          const int sel = (t == 60.0f) ? 1 : (t <= 0.0f && on ? 0 : 2);
          SendMessageW(cb, CB_SETCURSEL, sel, 0);
        }
        NdWriteGuestBe32(0x82B31FBC, NdFloatToBeBits(0.0f));
        NdNotifyAsuraFpsIntervalChanged();
        NdSaveSettingsToIni();
        return 0;
      }
      if (id == IDC_CFG_COMBO_FPS && code == CBN_SELCHANGE) {
        HWND cb = reinterpret_cast<HWND>(lParam);
        const int idx = static_cast<int>(SendMessageW(cb, CB_GETCURSEL, 0, 0));
        const float target = (idx == 1) ? 60.0f : (idx == 2) ? 30.0f : 0.0f;
        const bool unlock = (idx != 2);
        g_fps_target.store(target, std::memory_order_relaxed);
        g_unlock_framerate.store(unlock, std::memory_order_relaxed);
        HWND chkUnlock = GetDlgItem(hwnd, IDC_CFG_CHK_UNLOCK_FPS);
        if (chkUnlock) {
          SendMessageW(chkUnlock, BM_SETCHECK, unlock ? BST_CHECKED : BST_UNCHECKED, 0);
        }
        NdWriteGuestBe32(0x82B31FBC, NdFloatToBeBits(0.0f));
        NdNotifyAsuraFpsIntervalChanged();
        NdSaveSettingsToIni();
        if (NdIsDiagEnabled()) {
          REXLOG_INFO("neverdead: [CONFIG-UI] Framerate target: {:.1f} FPS (idx={}, unlock={})",
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
        if (!on) {
          NdHideShaderOsd();
        }
        NdSaveSettingsToIni();
        return 0;
      }
      for (const auto& t : kToggles) {
        if (id == t.id && code == BN_CLICKED) {
          const bool on =
              SendMessageW(reinterpret_cast<HWND>(lParam), BM_GETCHECK, 0, 0) == BST_CHECKED;
          const uint8_t byte_val = (t.inverted ? !on : on) ? 1 : 0;
          NdWriteGuestU8(t.va, byte_val);
          if (t.va2 != 0) NdWriteGuestU8(t.va2, byte_val);
          NdWriteGuestU8(0x828E1FBD, 1);
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
      g_config_hwnd.store(nullptr, std::memory_order_release);
      g_config_ui_open.store(false, std::memory_order_release);
      PostQuitMessage(0);
      return 0;
    }
    default:
      break;
  }
  return DefWindowProcW(hwnd, msg, wParam, lParam);
}

void NdRunConfigUIDialog() {
  HINSTANCE hInst = GetModuleHandle(nullptr);
  WNDCLASSEXW wc{};
  wc.cbSize = sizeof(wc);
  wc.style = CS_HREDRAW | CS_VREDRAW;
  wc.lpfnWndProc = NdConfigWndProc;
  wc.hInstance = hInst;
  wc.hCursor = LoadCursor(nullptr, IDC_ARROW);
  wc.hbrBackground = nullptr;
  wc.lpszClassName = L"NeverDeadConfigClass";
  RegisterClassExW(&wc);

  RECT rc = {0, 0, 520, 375};
  const DWORD dwExStyle = WS_EX_TOPMOST | WS_EX_TOOLWINDOW;
  AdjustWindowRectEx(&rc, WS_POPUP | WS_CAPTION | WS_SYSMENU, FALSE, dwExStyle);
  const int w = rc.right - rc.left;
  const int h = rc.bottom - rc.top;
  const int x = (GetSystemMetrics(SM_CXSCREEN) - w) / 2;
  const int y = (GetSystemMetrics(SM_CYSCREEN) - h) / 2;

  const std::wstring title = L"Neverdeth (" + NdVersionWString() + L") \u2014 Settings (F10)";
  HWND parent_hwnd = NdGetGameWindow();
  HWND hwnd = CreateWindowExW(dwExStyle, L"NeverDeadConfigClass", title.c_str(),
                              WS_POPUP | WS_CAPTION | WS_SYSMENU, x, y, w, h, parent_hwnd, nullptr,
                              hInst, nullptr);
  if (hwnd == nullptr) {
    g_config_ui_open.store(false, std::memory_order_release);
    return;
  }
  g_config_hwnd.store(hwnd, std::memory_order_release);
  NdSetDarkTitleBar(hwnd);

  HICON ico = LoadIconW(GetModuleHandleW(nullptr), MAKEINTRESOURCEW(1));
  if (ico == nullptr) {
    wchar_t exe_buf[MAX_PATH] = {};
    GetModuleFileNameW(nullptr, exe_buf, MAX_PATH);
    const std::filesystem::path exe_dir = std::filesystem::path(exe_buf).parent_path();
    std::filesystem::path ico_path = exe_dir / "config" / "neverdeth.ico";
    if (!std::filesystem::exists(ico_path)) {
      ico_path = exe_dir.parent_path() / "config" / "neverdeth.ico";
    }
    ico = reinterpret_cast<HICON>(LoadImageW(nullptr, ico_path.wstring().c_str(), IMAGE_ICON, 32,
                                             32, LR_LOADFROMFILE));
  }
  if (ico != nullptr) {
    SendMessageW(hwnd, WM_SETICON, ICON_SMALL, reinterpret_cast<LPARAM>(ico));
    SendMessageW(hwnd, WM_SETICON, ICON_BIG, reinterpret_cast<LPARAM>(ico));
  }

  NdCreateConfigControls(hwnd);
  SetTimer(hwnd, IDC_CFG_TIMER_ID, 16, nullptr);

  if (g_window_mode == 2 && parent_hwnd != nullptr && IsWindow(parent_hwnd)) {
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

}  // namespace

void NdSetDarkTitleBar(HWND hwnd) {
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

void NdDisableTheme(HWND ctrl) {
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

void NdLoadSettingsFromIni() {
  const std::wstring ini = NdGetSettingsIniPath();
  if (!std::filesystem::exists(ini)) return;

  wchar_t buf[64] = {};

  GetPrivateProfileStringW(L"Graphics", L"UnlockFramerate", L"true", buf, 64, ini.c_str());
  bool unlock = (_wcsicmp(buf, L"true") == 0 || wcstol(buf, nullptr, 10) == 1);

  GetPrivateProfileStringW(L"Graphics", L"ShowShaderCompilation", L"true", buf, 64, ini.c_str());
  const bool show_shaders = (_wcsicmp(buf, L"false") != 0 && _wcsicmp(buf, L"0") != 0);
  g_show_shader_compilation.store(show_shaders, std::memory_order_relaxed);

  GetPrivateProfileStringW(L"Graphics", L"AdaptiveExposureOptimization", L"true", buf, 64,
                           ini.c_str());
  const bool luma_opt = (_wcsicmp(buf, L"false") != 0 && _wcsicmp(buf, L"0") != 0);
  g_opt_luma_resolve.store(luma_opt, std::memory_order_relaxed);

  const int fps = GetPrivateProfileIntW(L"Graphics", L"TargetFPS", 0, ini.c_str());
  float target = (fps <= 0 && unlock) ? 0.0f : static_cast<float>(fps);

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

  g_unlock_framerate.store(unlock, std::memory_order_relaxed);
  g_fps_target.store(target, std::memory_order_relaxed);

  g_window_mode = GetPrivateProfileIntW(L"Graphics", L"WindowMode", 0, ini.c_str());

  for (const auto& t : kToggles) {
    GetPrivateProfileStringW(L"Graphics", t.ini_key, L"true", buf, 64, ini.c_str());
    const bool val = (_wcsicmp(buf, L"false") != 0 && _wcsicmp(buf, L"0") != 0);
    const uint8_t byte_val = (t.inverted ? !val : val) ? 1 : 0;
    NdWriteGuestU8(t.va, byte_val);
    if (t.va2 != 0) NdWriteGuestU8(t.va2, byte_val);
  }
  NdWriteGuestU8(0x828E1FBD, 1);
}

void NdSaveSettingsToIni() {
  const std::wstring ini = NdGetSettingsIniPath();
  const bool unlock = g_unlock_framerate.load(std::memory_order_relaxed);
  WritePrivateProfileStringW(L"Graphics", L"UnlockFramerate", unlock ? L"true" : L"false",
                             ini.c_str());

  const bool show_shaders = g_show_shader_compilation.load(std::memory_order_relaxed);
  WritePrivateProfileStringW(L"Graphics", L"ShowShaderCompilation",
                             show_shaders ? L"true" : L"false", ini.c_str());

  const bool luma_opt = g_opt_luma_resolve.load(std::memory_order_relaxed);
  WritePrivateProfileStringW(L"Graphics", L"AdaptiveExposureOptimization",
                             luma_opt ? L"true" : L"false", ini.c_str());

  const float target = g_fps_target.load(std::memory_order_relaxed);
  const int fps_val = (target <= 0.0f) ? 0 : static_cast<int>(target);
  WritePrivateProfileStringW(L"Graphics", L"TargetFPS", std::to_wstring(fps_val).c_str(),
                             ini.c_str());

  WritePrivateProfileStringW(L"Graphics", L"WindowMode", std::to_wstring(g_window_mode).c_str(),
                             ini.c_str());

  for (const auto& t : kToggles) {
    const bool val = NdToggleValue(t);
    WritePrivateProfileStringW(L"Graphics", t.ini_key, val ? L"true" : L"false", ini.c_str());
  }
}

void NdOpenConfigUI() {
  HWND existing = g_config_hwnd.load(std::memory_order_acquire);
  if (g_config_ui_open.load(std::memory_order_acquire) && existing != nullptr &&
      IsWindow(existing)) {
    if (!IsWindowVisible(existing)) {
      NdSyncCheckboxesFromMemory(existing);
      HWND parent_hwnd = NdGetGameWindow();
      if (g_window_mode == 2 && parent_hwnd != nullptr && IsWindow(parent_hwnd)) {
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
#else
void NdLoadSettingsFromIni() {}
void NdSaveSettingsToIni() {}
void NdOpenConfigUI() {}
#endif  // _WIN32
