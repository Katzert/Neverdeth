# Neverdeth - v0.1.0-alpha

Initial public alpha release of Neverdeth (static recompilation of NeverDead Xbox 360 to native x64 Windows and Direct3D 12).

### Highlights
- **Native x64 and Direct3D 12 Execution:** Complete PowerPC binary recompilation running over Direct3D 12 (ROV / Pixel Shader Interlock path) with native HDR bloom and eDRAM emulation.
- **Dynamic Framerate Hysteresis:** Rock-solid 30.0 FPS 1:1 pacing in menus and Bink videos, with automatic high-framerate unlocked presentation in 3D combat (toggleable in real-time with `F10` or `config/settings.ini`).
- **Autonomous Setup Wizard:** 100% native Win32 extractor for unencrypted Xbox 360 disc images (`.iso`), Games on Demand (`.data`) packages, and extracted folders without Python or external dependencies.
- **Asset Junction Compatibility:** Automatic link creation for game subdirectories (`FMV`, `Fonts`, `GUIMenu`, `misc`, `sounds`, `Text`) ensuring seamless media playback across all Windows installations.
- **In-Game Settings (F10):** Real-time graphics menu to adjust display modes, framerate ceilings, SSAO, Motion Blur, Fog, and Dynamic Exposure.

### Setup Instructions
1. Extract `Neverdeth-v0.1.0-alpha-win64.zip` into any folder.
2. Run `Neverdeth.exe`.
3. Follow the first-time setup prompt to select your legally acquired Xbox 360 copy of NeverDead (Title ID `4B4E081F`).

### Known Issues
- **First-Time PSO Compilation:** Entering new 3D zones compiles DirectX 12 pipeline state objects asynchronously. Cached states are preserved in `cache/` for stutter-free subsequent sessions.
- **Raw Encrypted Redump ISOs:** Disc dumps with raw physical optical encryption must be decrypted to standard XISO format prior to extraction.
