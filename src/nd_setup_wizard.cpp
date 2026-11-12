// Neverdeth - First-launch asset setup wizard and asset link helpers.

#include "nd_internal.h"
#include "nd_native_extractor.h"

#ifdef _WIN32
#include <commdlg.h>
#include <shlobj.h>
#endif

#include <mutex>
#include <thread>

void NdEnsureCesaBik(const std::filesystem::path& target_assets_dir) {
  nd::extractor::EnsureCesaBik(target_assets_dir);
}

void NdEnsureAssetJunctionsOrLinks(const std::filesystem::path& base_dir,
                                   const std::filesystem::path& assets_dir) {
  nd::extractor::NdEnsureAssetJunctionsOrLinks(base_dir, assets_dir);
}

void NdRunFirstLaunchSetupWizard(const std::filesystem::path& target_assets_dir) {
#ifdef _WIN32
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

        const std::wstring header_text =
            L"Neverdeth (" + NdVersionWString() + L") \u2014 Game Data Setup";
        HWND lblTitle = CreateWindowW(L"STATIC", header_text.c_str(), WS_CHILD | WS_VISIBLE, 20, 15,
                                      480, 26, hwnd, nullptr, nullptr, nullptr);
        SendMessageW(lblTitle, WM_SETFONT, reinterpret_cast<WPARAM>(hTitleFont), TRUE);

        HWND lblInfo = CreateWindowW(
            L"STATIC",
            L"No game assets were found in 'assets/'.\n"
            L"To play Neverdeth, please provide your legal copy of the Xbox 360 release "
            L"(Title ID 4B4E081F).\n"
            L"Select one of the installation sources below:",
            WS_CHILD | WS_VISIBLE, 20, 48, 480, 56, hwnd, nullptr, nullptr, nullptr);
        SendMessageW(lblInfo, WM_SETFONT, reinterpret_cast<WPARAM>(hFont), TRUE);

        HWND btnIso = CreateWindowW(
            L"BUTTON", L"Select ISO Image (.iso) or GOD Container (.data)",
            WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON | BS_OWNERDRAW, 20, 115, 480, 36, hwnd,
            reinterpret_cast<HMENU>(2001), nullptr, nullptr);
        SendMessageW(btnIso, WM_SETFONT, reinterpret_cast<WPARAM>(hBtnFont), TRUE);
        NdDisableTheme(btnIso);

        HWND btnFolder = CreateWindowW(
            L"BUTTON", L"Select Extracted Game Folder",
            WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON | BS_OWNERDRAW, 20, 160, 480, 36, hwnd,
            reinterpret_cast<HMENU>(2002), nullptr, nullptr);
        SendMessageW(btnFolder, WM_SETFONT, reinterpret_cast<WPARAM>(hBtnFont), TRUE);
        NdDisableTheme(btnFolder);

        HWND btnExit = CreateWindowW(
            L"BUTTON", L"Exit", WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON | BS_OWNERDRAW, 20, 205, 480,
            32, hwnd, reinterpret_cast<HMENU>(2003), nullptr, nullptr);
        SendMessageW(btnExit, WM_SETFONT, reinterpret_cast<WPARAM>(hBtnFont), TRUE);
        NdDisableTheme(btnExit);

        HWND lblStatus = CreateWindowW(L"STATIC", L"", WS_CHILD, 20, 248, 480, 24, hwnd,
                                       reinterpret_cast<HMENU>(2004), nullptr, nullptr);
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
        Rectangle(dis->hDC, dis->rcItem.left, dis->rcItem.top, dis->rcItem.right,
                  dis->rcItem.bottom);
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
        if (id == 2003) {
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
            const bool ok = nd::extractor::ExtractAll(
                input_path, s_target_assets,
                [&](const std::string& rel_file, uint64_t done_bytes, uint64_t total_bytes,
                    float frac) {
                  (void)done_bytes;
                  (void)total_bytes;
                  int pct = static_cast<int>(frac * 100.0f);
                  if (pct > 100) pct = 100;
                  std::wstring status_msg;
                  if (rel_file == "Building shader cache...") {
                    status_msg = L"Building shader cache...";
                  } else if (rel_file.rfind("Insufficient disk space", 0) == 0) {
                    status_msg = L"Error: Insufficient disk space (< 7 GB available)";
                  } else {
                    const std::string fname = std::filesystem::path(rel_file).filename().string();
                    status_msg = L"Extracting (" + std::to_wstring(pct) + L"%): ";
                    status_msg.append(fname.begin(), fname.end());
                  }
                  {
                    std::lock_guard<std::mutex> lk(status_mtx);
                    status_str = status_msg;
                  }
                  return true;
                });
            nd::extractor::EnsureCesaBik(s_target_assets);
            worker_ok.store(ok);
            worker_done.store(true);
          });

          // Pump Win32 messages while extraction runs so the setup window stays responsive.
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
          if (worker_ok.load() && std::filesystem::exists(s_target_assets / "default.xex", ec)) {
            MessageBoxW(hwnd,
                        L"Game data extracted and verified successfully!\nStarting Neverdeth...",
                        L"Neverdeth Setup Complete", MB_OK | MB_ICONINFORMATION);
            DestroyWindow(hwnd);
          } else {
            if (btn1) EnableWindow(btn1, TRUE);
            if (btn2) EnableWindow(btn2, TRUE);
            if (btn3) EnableWindow(btn3, TRUE);
            if (lblStat) SetWindowTextW(lblStat, L"Extraction failed. Please verify the source.");
            MessageBoxW(
                hwnd,
                L"Extraction failed or default.xex was not found in assets/.\n"
                L"Please ensure the file is an unencrypted Xbox 360 copy of NeverDead / "
                L"Neverdeth (4B4E081F).",
                L"Setup Incomplete", MB_OK | MB_ICONWARNING);
          }
        };

        if (id == 2001) {
          HMODULE hComDlg = LoadLibraryW(L"comdlg32.dll");
          if (hComDlg) {
            using Fn = BOOL(WINAPI*)(LPOPENFILENAMEW);
            auto fn = reinterpret_cast<Fn>(GetProcAddress(hComDlg, "GetOpenFileNameW"));
            if (fn) {
              wchar_t file_buf[MAX_PATH] = {};
              OPENFILENAMEW ofn{};
              ofn.lStructSize = sizeof(ofn);
              ofn.hwndOwner = hwnd;
              ofn.lpstrFilter =
                  L"Xbox 360 Disc Image or GOD Container (*.iso;*.data;Data0000;*)\0"
                  L"*.iso;*.data;Data0000;*\0All Files (*.*)\0*.*\0";
              ofn.lpstrFile = file_buf;
              ofn.nMaxFile = MAX_PATH;
              ofn.lpstrTitle =
                  L"Select Xbox 360 ISO Image or GOD Container (NeverDead 4B4E081F)";
              ofn.Flags = OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST;
              if (fn(&ofn)) {
                run_extraction_async(std::filesystem::path(file_buf));
              }
            }
          }
          return 0;
        }
        if (id == 2002) {
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

  const std::wstring window_title =
      L"Neverdeth (" + NdVersionWString() + L") \u2014 Game Data Setup";
  std::filesystem::path target_copy = target_assets_dir;
  HWND hwnd = CreateWindowExW(0, L"NeverDeadSetupClass", window_title.c_str(),
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
#else
  (void)target_assets_dir;
#endif
}
