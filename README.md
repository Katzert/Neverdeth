<div align="center">
  <img src="docs/assets/neverdeth_logo_transparent.png" alt="Neverdeth" width="420" />
  <p>
    <b>English</b> | <a href="README.es.md">Español</a>
  </p>
</div>

**Neverdeth** is an unofficial native Windows x64 port of *NeverDead* (Xbox 360, 2012). Conceived as a full AI-assisted **EmuComp** (a hybrid combining static PowerPC-to-C++20 recompilation with a custom Xbox 360 hardware runtime and Direct3D 12 translation layer), it runs the game natively on 64-bit Windows.

This repository does not contain any original game code or proprietary assets. You must provide your own retail Xbox 360 copy of *NeverDead* (Title ID `4B4E081F`) to extract the game files on first launch.

## Features

- **Native x64 & Direct3D 12 EmuComp Architecture:** Combines static executable recompilation with low-level XDK/Xenos runtime bridging to run natively on Windows x64 without full virtual machine emulation overhead.
- **High Framerate Mode:** Runs at the original 30 FPS by default, with an optional unlocked framerate mode for 3D gameplay while preserving native 30 FPS timing in menus and pre-rendered cutscenes.
- **Real-Time Graphics Menu (`F10`):** Toggle shadows, SSAO, volumetric light shafts, fog, HDR bloom, motion blur, RMLAA, and HUD elements in-game at any time.
- **Built-In Game Extractor:** Extracts game data directly from unencrypted `.iso` images (XISO/XGD2), Games on Demand (`.data`) containers, or extracted folders on first startup.

## System Requirements

- **OS:** Windows 10 (64-bit, version 20H2 or newer) or Windows 11
- **GPU:** Direct3D 12 compatible graphics card (NVIDIA GeForce GTX 900 series+, AMD Radeon RX 5000 series+, or Intel Arc)
- **Runtime:** [Microsoft Visual C++ 2015–2022 Redistributable (x64)](https://aka.ms/vs/17/release/vc_redist.x64.exe)
- **Input:** XInput-compatible controller
- **Game Data:** Unmodified Xbox 360 copy of *NeverDead* (Title ID `4B4E081F`) as an unencrypted `.iso` (XISO/XGD2), a Games on Demand (`.data`) package, or an extracted folder containing `default.xex`

## Setup

1. Download `Neverdeth-v0.1.0-alpha-win64.zip` from the [Releases](https://github.com/Katzert/Neverdeth/releases) page and extract it to a folder of your choice.
2. Run `Neverdeth.exe`.
3. On first launch, select your `.iso` image, `.data` container, or extracted game directory when prompted. Once the game files are unpacked into `assets/`, the game will start automatically.

> **Note:** Raw Redump `.iso` images encrypted with physical disc keys must be decrypted or converted to standard XISO format before importing.

## Configuration

Press **F10** at any time while the game is running to open the configuration window, or edit `config/settings.ini` manually:

- **Framerate:** Set `UnlockFramerate=true` (or toggle it in the `F10` menu) to unlock the framerate during 3D gameplay.
- **Graphics Options:** Individual toggles for shadows, ambient occlusion (SSAO), light shafts, fog, bloom, motion blur, anti-aliasing (RMLAA), and the HUD are saved automatically to `config/settings.ini`.

## Building from Source

### Prerequisites

- Visual Studio 2022 Build Tools & Windows 10/11 SDK
- Clang/LLVM 18+
- CMake 3.24+ & Ninja
- Python 3.10+

### Build Steps

```powershell
cmake --preset clang-release
ninja -C build_clang
```

To generate the redistributable archive in `dist/`:

```powershell
python tools/package_release.py
```

## AI-Assisted Development & Credits

**Neverdeth** was developed as a fully AI-assisted **EmuComp** project, orchestrating multiple large language and multimodal models across different development environments, reverse engineering workflows, and code auditing stages. The entire project was built using only a **Google AI Pro** subscription, without spending additional money on external API tokens by distributing tasks across free-tier models in various programs:

- **Primary Architecture, Reverse Engineering & Auditing (via Google AI Pro):** Gemini Argon, Gemini 3.8 Flash
- **Additional Code Analysis & Reasoning:** Claude Sonnet 4.5
- **Free-Tier Models (Distributed across development tools at zero cost):** DeepSeek v4.1 Flash, GLM 5.3, GLM 5.3 Flash, Muse Spark 1.3, Nemotron 3.5 Super VL, MiniMax M3.1 Flash

### Toolchain & Acknowledgments

Built with the [ReXGlue SDK](https://github.com/rexglue) static recompilation toolchain, with foundational hardware documentation and research from the [Xenia Project](https://xenia.jp/).

*NeverDead* was developed by Rebellion Developments and published by Konami Digital Entertainment. This project is non-commercial and is not affiliated with or endorsed by Konami Digital Entertainment or Rebellion Developments.
