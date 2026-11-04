<p align="center">
  <img src="docs/assets/neverdeth_logo_transparent.png" alt="Neverdeth Logo" width="560">
</p>

# Neverdeth

An unofficial static recompilation of the Xbox 360 release of NeverDead (2012, Title ID 4B4E081F) to native x64 Windows and Direct3D 12.

> **Note:** This project does not contain any original game assets, proprietary audio/video streams, or copyrighted game code. You must provide your own legally acquired copy of NeverDead (Xbox 360, Title ID 4B4E081F) to extract and play the game.

---

## Features
- **Native x64 Execution:** PowerPC machine code statically lifted and recompiled to native C++20 and optimized x64 machine code via the ReXGlue SDK.
- **Direct3D 12 Rendering Backend:** Accurate eDRAM emulation using Rasterizer Ordered Views (ROV / Pixel Shader Interlock) for original Xbox 360 lighting, HDR bloom, and post-processing parity.
- **High Framerate Support:** Rock-solid 30.0 FPS 1:1 frame-paced presentation by default, with dynamic frame-by-frame hysteresis for unlocked 60+ FPS in 3D combat while automatically protecting menus, loading screens, and Bink FMVs at 30 FPS.
- **Built-in Native Asset Installer:** Autonomous Win32 setup wizard supporting unencrypted Xbox 360 `.iso` disc images (XISO / XGD2 / XGD3), Games on Demand (`.data`) packages, and extracted game folders without any external dependencies.
- **In-Game Configuration Menu (F10):** Real-time runtime overlay to adjust framerate limits, window modes, and toggle individual Asura Engine render passes (SSAO, Motion Blur, Shadows, Fog, Adaptive Exposure). Settings persist across sessions in `config/settings.ini`.

---

## System Requirements
- **OS:** Windows 10 (version 20H2+) or Windows 11 (64-bit).
- **GPU:** DirectX 12 compatible graphics card supporting Rasterizer Ordered Views (ROV):
  - NVIDIA GeForce GTX 900 series or newer (RTX recommended).
  - AMD Radeon RX 5000 series or newer.
  - Intel Arc / Iris Xe or newer.
- **CPU:** x86-64 processor supporting AVX2 instructions.
- **Game Copy:** Legally acquired Xbox 360 copy of NeverDead (Title ID `4B4E081F`) in `.iso`, `.data`, or extracted folder format.

---

## Installation & Quick Start

1. Download [`Neverdeth-v0.1.0-alpha-win64.zip`](https://github.com/neverdeth/neverdeth/releases) and extract the archive to a clean folder.
2. Launch `Neverdeth.exe`.
3. On first startup, the setup wizard will appear. Select your Xbox 360 `.iso` image, `.data` GOD container, or extracted game folder containing `default.xex`.
4. The game will extract and verify required assets into `assets/` and launch immediately.
5. Press `F10` during gameplay at any time to open the configuration menu.

*Alternatively, you can extract assets manually via CLI:*
```powershell
python tools/extract_neverdead_assets.py --input <path_to_game_dump> --output assets
```

---

## Building from Source

### Prerequisites
- Windows 10/11 x64
- Visual Studio 2022 Build Tools (Desktop development with C++)
- LLVM / Clang 18+ (`clang++`, `clang`, `llvm-rc`, `lld-link`)
- CMake 3.25+
- Ninja 1.11+
- Python 3.10+

### Build Steps

```powershell
# 1. Configure the build directory with CMake and Clang
cmake -B build_clang -G Ninja -DCMAKE_CXX_COMPILER="clang++" -DCMAKE_C_COMPILER="clang"

# 2. Compile the game executable and backend
ninja -C build_clang neverdead

# 3. Package the distribution release archive
python tools/package_release.py
```

The resulting release package will be generated under `dist/Neverdeth-v0.1.0-alpha-win64.zip`.

---

## Disclaimer & Acknowledgments
- **ReXGlue SDK:** Runtime framework, PowerPC execution context, and Direct3D 12 Xenos emulation layer.
- **Xenia Project:** Foundational research on the Xbox 360 Xenos GPU architecture and kernel structures.
- **NeverDead:** Developed by Rebellion Developments and published by Konami Digital Entertainment (2012). This project is an independent educational and preservation effort and is not affiliated with, endorsed by, or sponsored by Konami or Rebellion.
