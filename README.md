<div align="center">
  <img src="docs/assets/neverdeth_logo_transparent.png" alt="Neverdeth" width="420" />
  <p>
    <b>English</b> | <a href="README.es.md">Español</a>
  </p>
</div>

**Neverdeth** is an unofficial Windows x64 EmuComp for *NeverDead* (Xbox 360, 2012). It statically recompiles the PowerPC executable to C++23 using the ReXGlue SDK and bridges the Xbox 360 runtime and Xenos graphics calls to a Direct3D 12 backend.

This repository does not contain any original game code or proprietary assets. You must provide your own retail Xbox 360 copy of the game.

## Features

- **x64 EmuComp Architecture:** Ahead-of-time PowerPC recompilation paired with a low-level XDK and Xenos runtime bridge, running on 64-bit Windows without virtual machine overhead.
- **On-Demand Shader Cache:** A local Direct3D 12 pipeline library plus an in-memory PSO cache. New pipeline states are compiled on demand and their descriptors are persisted by an asynchronous background writer. Bulk startup pre-compilation is available but opt-in (`ND_PSO_PRECOMPILE=1`).
- **Framerate Options:** `30 FPS`, `60 FPS` and unlocked, selectable in the F10 menu or `config/settings.ini`. Menus and Bink cutscenes stay at 30 FPS; 3D uses native VBlank pacing.
- **Real-Time Graphics Menu (F10):** Toggle shadows, SSAO, volumetric light shafts, fog, HDR bloom, motion blur, RMLAA and the HUD on the fly.
- **Built-In Game Extractor:** Reads game data from unencrypted `.iso` images (XISO/XGD2), Games on Demand (`.data`) containers or extracted folders on first launch.

## System Requirements

- **OS:** Windows 10 (64-bit, version 20H2 or newer) or Windows 11
- **GPU:** Direct3D 12 compatible graphics card (NVIDIA GeForce GTX 900 series+, AMD Radeon RX 5000 series+, or Intel Arc)
- **Runtime:** [Microsoft Visual C++ 2015-2022 Redistributable (x64)](https://aka.ms/vs/17/release/vc_redist.x64.exe)
- **Input:** XInput controller required (Xbox gamepad, or PlayStation controller via an XInput wrapper). Keyboard gameplay is not supported.
- **Game Data:** Unmodified Xbox 360 copy of *NeverDead* (Title ID `4B4E081F`) as an unencrypted `.iso` (XISO/XGD2), Games on Demand container, or extracted directory containing `default.xex`

## Setup

1. Download the latest ZIP from the [Releases](https://github.com/Katzert/Neverdeth/releases) page and extract it to a folder with at least 7 GB of free disk space.
2. Run `Neverdeth.exe`.
3. On first launch, select your `.iso` image, `.data` container, or extracted game directory when prompted. Once asset extraction finishes, the game starts automatically; the shader cache is populated on demand as new pipelines are encountered.

> **Note:** Raw Redump `.iso` images encrypted with physical disc keys must be decrypted or converted to standard XISO format before use.

## Configuration

Press **F10** at any time while the game is running to open the configuration window, or edit `config/settings.ini` manually:

- **Framerate:** Unlocked 3D gameplay framerate is enabled by default (`UnlockFramerate = true`), and can be capped to 60 or 30 FPS in the F10 menu or `config/settings.ini`.
- **Graphics Options:** Individual toggles for shadows, ambient occlusion (SSAO), light shafts, fog, bloom, motion blur, anti-aliasing (RMLAA), and HUD visibility are saved automatically to `config/settings.ini`.

## Alpha Status

This is an early alpha: expect missing effects, occasional shader compilation stutter when first entering 3D areas, and GPU/driver-dependent behaviour. Bug reports with `ND_DIAG=1` logs are welcome.

## Building from Source

### Prerequisites

- Visual Studio 2022 Build Tools & Windows 10/11 SDK
- Clang/LLVM 18+
- CMake 3.25+ & Ninja
- Python 3.10+ (plus `Pillow` if running `tools/gameplay_qa.py`)
- [ReXGlue SDK 0.10.0](https://github.com/rexglue/rexglue-sdk) installed (via `CMAKE_PREFIX_PATH` or default install path; or `REXSDK_DIR` for an SDK source checkout)
- Your own retail Xbox 360 copy of `NeverDead` (`assets/default.xex` extracted into `assets/` before running `rexglue codegen`)

### Build Steps

1. Apply the SDK header overlay from [patches/sdk/README.md](patches/sdk/README.md) into your ReXGlue SDK include directory.
2. Place `default.xex` in `assets/` and generate the recompiled C++ sources from `neverdead_manifest.toml`:

```powershell
rexglue codegen neverdead_manifest.toml
```

3. Ensure `INCLUDE`, `LIB`, and `PATH` are configured in your shell environment for MSVC Build Tools, the Windows SDK, and LLVM/Clang, then build:

```powershell
cmake --preset clang-release
ninja -C build_clang neverdead
```

To generate the redistributable archive in `dist/`:

```powershell
python tools/package_release.py
```

## Acknowledgments

Built with the [ReXGlue SDK](https://github.com/rexglue) static recompilation toolchain, with foundational hardware research and documentation from the [Xenia Project](https://xenia.jp/).

*NeverDead* was developed by Rebellion Developments and published by Konami Digital Entertainment. This project is non-commercial and is not affiliated with or endorsed by Rebellion Developments or Konami.

## Support

Neverdeth is a solo hobby project. If you'd like to chip in to help me test more tools and hardware, or just buy me a coffee, you can do so here:

- **Patreon:** [patreon.com/Katzert](https://www.patreon.com/Katzert)
- **USDC / USDT (EVM):** `0x2486534a24CB3a2108DF2F73f3872B89bb7032C9`
  *(Supported networks: Polygon, Arbitrum, BNB Chain)*

## License

This project is released under the [MIT License](LICENSE). The source code, build scripts and documentation are provided as-is, without warranty.

*NeverDead* and all related game assets, trademarks and copyrights are the property of Rebellion Developments and Konami Digital Entertainment. This project includes no game data and requires the user's own legally obtained retail copy. The MIT license applies only to the Neverdeth tooling and recompiled code, not to any third-party or game intellectual property.
