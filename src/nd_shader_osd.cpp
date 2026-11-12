// Neverdeth - In-window shader compilation overlay (OSD).

#include "nd_internal.h"

#include <cstdio>

std::atomic<uint32_t> g_compiled_shader_count{0};
std::atomic<uint32_t> g_active_pso_compiles{0};
std::atomic<bool> g_show_shader_compilation{true};
std::atomic<bool> g_is_precompiling_shaders{false};
std::atomic<uint32_t> g_precompile_total{0};
std::atomic<uint64_t> g_last_shader_compile_tick_ms{0};

#ifdef _WIN32
namespace {

HWND g_shader_osd_hwnd = nullptr;

LRESULT CALLBACK NdShaderOsdWndProc(HWND hwnd, UINT msg, WPARAM wParam, LPARAM lParam) {
  switch (msg) {
    case WM_PAINT: {
      PAINTSTRUCT ps;
      HDC hdc = BeginPaint(hwnd, &ps);
      RECT rc{};
      GetClientRect(hwnd, &rc);

      HBRUSH bg = CreateSolidBrush(RGB(15, 18, 25));
      HBRUSH old_b = static_cast<HBRUSH>(SelectObject(hdc, bg));
      HPEN pen = CreatePen(PS_SOLID, 1, RGB(70, 85, 120));
      HPEN old_p = static_cast<HPEN>(SelectObject(hdc, pen));
      RoundRect(hdc, rc.left, rc.top, rc.right, rc.bottom, 8, 8);
      SelectObject(hdc, old_p);
      SelectObject(hdc, old_b);
      DeleteObject(pen);
      DeleteObject(bg);

      const uint32_t count = g_compiled_shader_count.load(std::memory_order_relaxed);
      const uint32_t active = g_active_pso_compiles.load(std::memory_order_relaxed);
      wchar_t text[80] = {};
      if (g_is_precompiling_shaders.load(std::memory_order_relaxed)) {
        const uint32_t total = g_precompile_total.load(std::memory_order_relaxed);
        swprintf_s(text, L"Compiling shaders (%u / %u)...", count, total);
      } else if (count == 0 && active > 0) {
        swprintf_s(text, L"Compiling shaders...");
      } else {
        swprintf_s(text, L"Compiling shaders (%u)...", count);
      }

      SetBkMode(hdc, TRANSPARENT);
      SetTextColor(hdc, RGB(245, 245, 250));
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
    case WM_ERASEBKGND:
      return 1;
    default:
      break;
  }
  return DefWindowProcW(hwnd, msg, wParam, lParam);
}

}  // namespace

void NdEnsureShaderOsdWindow(HWND parent_hwnd) {
  if (NdQaDisabled("osd")) return;
  if (g_shader_osd_hwnd && IsWindow(g_shader_osd_hwnd)) return;

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

  HWND owner = (parent_hwnd && IsWindow(parent_hwnd)) ? parent_hwnd : NdGetGameWindow();
  g_shader_osd_hwnd = CreateWindowExW(
      WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE,
      L"NeverDeadShaderOsdClass", L"", WS_POPUP, 0, 0, 250, 34, owner, nullptr, hInst, nullptr);

  if (g_shader_osd_hwnd) {
    SetLayeredWindowAttributes(g_shader_osd_hwnd, 0, 230, LWA_ALPHA);
  }
}

void NdUpdateShaderOsd(HWND game_hwnd) {
  // Defer creating the layered OSD window until the presenter has output real frames
  // so DWM does not synchronize with early D3D12 initialization on the UI thread.
  if (g_frames_presented.load(std::memory_order_relaxed) < 30) {
    return;
  }

  const uint32_t active_before = g_active_pso_compiles.load(std::memory_order_acquire);
  const uint32_t count = g_compiled_shader_count.load(std::memory_order_acquire);
  const uint64_t last_tick = g_last_shader_compile_tick_ms.load(std::memory_order_acquire);
  const uint32_t active_after = g_active_pso_compiles.load(std::memory_order_acquire);
  const uint64_t now = GetTickCount64();
  const HWND cfg_hwnd = g_config_hwnd.load(std::memory_order_acquire);
  const bool menu_open =
      (cfg_hwnd != nullptr && IsWindow(cfg_hwnd) && IsWindowVisible(cfg_hwnd));
  const bool show_pref = g_show_shader_compilation.load(std::memory_order_relaxed);
  const bool is_compiling = g_is_precompiling_shaders.load(std::memory_order_acquire) ||
                            active_before > 0 || active_after > 0 ||
                            (now >= last_tick && (now - last_tick) < 3000 && count > 0);

  if (show_pref && (is_compiling || menu_open)) {
    NdEnsureShaderOsdWindow(game_hwnd);
    if (g_shader_osd_hwnd && IsWindow(g_shader_osd_hwnd)) {
      POINT pt{16, 16};
      ClientToScreen(game_hwnd, &pt);
      SetWindowPos(g_shader_osd_hwnd, HWND_TOPMOST, pt.x, pt.y, 250, 34,
                   SWP_NOACTIVATE | SWP_SHOWWINDOW);
      // Use async invalidation to avoid re-entering the UI thread via UpdateWindow().
      InvalidateRect(g_shader_osd_hwnd, nullptr, TRUE);
    }
  } else {
    NdHideShaderOsd();
  }
}

void NdHideShaderOsd() {
  if (g_shader_osd_hwnd && IsWindow(g_shader_osd_hwnd) && IsWindowVisible(g_shader_osd_hwnd)) {
    ShowWindow(g_shader_osd_hwnd, SW_HIDE);
  }
}
#endif  // _WIN32
