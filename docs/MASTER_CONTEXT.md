# MASTER CONTEXT & HANDOVER DOSSIER: NEVERDEAD STATIC RECOMPILATION
**Target Game:** *NeverDead* (`4B4E081F`, Konami / Rebellion, Asura Engine 2012, Xbox 360)  
**Host Platform:** Windows 11 x64  
**Toolchain:** ReXGlue SDK v0.10.0.24-dev (XenonRecomp derivative), LLVM Clang 23.1 + Ninja  
**Workspace Path:** `c:\Users\katze\.gemini\antigravity-ide\scratch\vibe-recomp\neverdead_project`  
**Current Milestone:** Line 348,875 in `build_clang/logs/neverdead_110.log` (Resource Chunk Iterator Unlocked)  
**Primary Objective:** Native PC execution reaching introductory FMV video playback (`rebellion.bik`, `ND_logo_Cred.bik`, `Front_End.bik`) and front-end GUI initialization (`GUIMenu\loadingInitial.guiBE`).

---

## 1. Executive Summary & Evolutionary Timeline

*NeverDead* runs on Rebellion's proprietary **Asura Engine (2012)**. The static recompilation pipeline translates PowerPC Xenon machine code into C++ translation units (`generated/neverdead_default/neverdead_recomp.*.cpp`) compiled with Clang 23.1 into a native x64 Windows binary `neverdead.exe`.

Over 72 iterative cycles, execution advanced from **117 lines** to **348,875 lines** of live engine telemetry:
- **Phase 1 (Boot & Core Systems):** Memory precommit, thread manager initialization, TLS allocation, CRT heap allocation.
- **Phase 2 (VFS & Asset Mounting):** Asura Engine file system initialization, mounting of game archives (`common.asrBE`, `fonts.asrBE`, `text.asrBE`).
- **Iteration #69 (CRT Formatting Unlocked):**
  - Bridged `0x8278F4D8 -> 0x8278EB5C` in `_output_l` / `vsnprintf` with register pre-compensation (`r11 = r23 + 25296`, `r10 = r22 + 23960`, `r9 = r17 + 11112`).
  - Allowed path formatting to complete without memory corruption (`0xC0000005`), reaching 58,099 lines in `neverdead_107.log`.
- **Iteration #70 & #71 (CRT Lock Table Deadlock Solved):**
  - Fixed an infinite recursion loop in `sub_827953E0` (`_mtinitlocknum`) and `sub_82795520` (`_lock`).
  - Root cause: Index `10` is `_LOCKTAB_LOCK` in Microsoft CRT (`mlock.c`), which protects the master lock table. In ReX-generated C++, `r30` was overwritten with `0x827EA290` (`_locktable` base address), causing the condition `if (ctx.r30.u32 != 10)` to always be true.
  - Solution: Rewrote `apply_locktab_fix.py` (v2) to capture `const uint32_t s_locknum = ctx.r3.u32;` in the function prologue and guard recursive calls with `if (s_locknum != 10)`. Stream descriptors were cleanly allocated and handed to Asura.
- **Iteration #72 (Resource Chunk Iterator Loop Bridged - Historic Leap):**
  - ReX partitioner severed `sub_821E0F70` (the main `for (int i = 0; i < count; i++)` archive chunk unpacker) between `recomp.5.cpp` (loop head) and `recomp.56.cpp` (loop tail at `0x821E1098`).
  - Created `apply_821e0fa0_fix.py`: Extracted loop body in `recomp.5.cpp` as `void sub_821E0FA0(PPCContext& ctx, uint8_t* base)`, added declaration in `src/nd_indirect_trap.h`, and patched `recomp.56.cpp:1010` to bridge `blt cr6, 0x821e0fa0`.
  - **Result:** Execution exploded from **2,770 lines to 348,875 lines** in `neverdead_110.log`! Asura processed thousands of asset chunks across all archive containers.
- **Iteration #73 (Current State - Cross-Block Epilogue Bridge):**
  - Hit `[FATAL] Unresolved branch from 0x822CAC34 to 0x822CACC0` at line 348,875.
  - Forensic analysis of `sub_822CABA8` revealed this is a conditional early-exit branch (`r4 == 0`) jumping forward to the shared epilogue `loc_822CACC0`.
  - In `NeverDead_base.exe`, `sub_822CABA8` allocated 112 bytes on the stack (`stwu r1, -112(r1)`). The epilogue at `0x822CACC0` invokes a post-cleanup callback at `vtable[8]`, deallocates 112 bytes from stack (`r1 += 112`), and restores non-volatile registers `r30/r31` and `lr`.
  - Defined `neverdead_epilogue_822CACC0(PPCContext& ctx, uint8_t* base)` in `src/nd_indirect_trap.h`.
  - Added patches `("0x822CAC34", "0x822CACC0")` and `("0x822CAC4C", "0x822CACC0")` to `apply_bink_branch_fix.py`.

---

## 2. Ground Truth Oracles & Reference Assets

1. **Oracle 1: Xenia Canary Trace (`xenia_oracle.log`)**
   - Path: `c:\Users\katze\.gemini\antigravity-ide\scratch\vibe-recomp\neverdead_project\xenia_oracle.log`
   - Verified engine milestone sequence:
     1. Asura mounts `GAME:\` virtual drive.
     2. Engine loads `GAME:\misc\common.asrBE`.
     3. Engine loads `GAME:\Text\English.asrBE` and `GAME:\Fonts\buttons.asrBE`.
     4. Engine initializes `GAME:\GUIMenu\loadingInitial.guiBE`.
     5. Asura spawns `Thread 10: FMV` and streams `GAME:\FMV\rebellion.bik`.
2. **Oracle 2: Native PC Asura Engine (`Sniper Elite V2`)**
   - Binary: `..\rosetta_sev2\SniperEliteV2.exe` (PDB: `SniperV2_D3D11_CEG_Submission.pdb`).
   - Sibling game developed concurrently on the same Asura codebase (2012).
   - Confirms vtable layouts, `AsuraZlb` chunk formats, and Bink video export signatures (`_BinkOpen@8`, `_BinkDoFrame@4`, `_BinkCopyToBuffer@28`, `_BinkWait@4`, `_BinkClose@4`).
3. **Oracle 3: Capstone Static Disassembler**
   - Binary: `..\NeverDead_base.exe`.
   - Guest VA to file offset formula: `file_offset = guest_va - 0x82000000`.

---

## 3. Virtual File System (VFS) Infrastructure

Rebellion's Asura Engine expects Windows file access via relative directories or `game:\` drive mappings. To ensure zero `FILE_NOT_FOUND` errors, NTFS Directory Junctions are active in the project root:
- `FMV` $\rightarrow$ `assets\FMV`
- `GUIMenu` $\rightarrow$ `assets\GUIMenu`
- `Fonts` $\rightarrow$ `assets\Fonts`
- `sounds` $\rightarrow$ `assets\sounds`
- `misc` $\rightarrow$ `assets\misc`
- `Text` $\rightarrow$ `assets\Text`

---

## 4. Current Working Tree & Tooling State

All modifications are strictly encapsulated in idempotent scripts:

| File | Status | Description |
| :--- | :--- | :--- |
| `src/nd_indirect_trap.h` | **Active** | Custom indirect dispatch hook: bounds check, safe stubs for missing `.pdata` vtable targets, loop declarations (`sub_821E0FA0`), and epilogue implementations (`neverdead_epilogue_822CACC0`, `neverdead_epilogue_8269987C`). |
| `apply_bink_branch_fix.py` | **Active** | Master patcher for severed cross-TU branches. Automatically calls `apply_locktab_fix.py` and `apply_821e0fa0_fix.py`. |
| `apply_locktab_fix.py` | **Active** | Solves CRT `_LOCKTAB_LOCK` deadlock with `s_locknum != 10`. |
| `apply_821e0fa0_fix.py` | **Active** | Bridges resource chunk iterator loop `0x821E1104 -> 0x821E0FA0` across `recomp.5.cpp` and `recomp.56.cpp`. |
| `build_neverdead.ps1` | **Active** | Clang 23.1 + Ninja compilation script producing `build_clang/neverdead.exe`. |
| `run_game_watchdog.py` | **Active** | Executes game with 20s timeout, capturing output to `build_clang/logs/neverdead_*.log`. |
| `crash_oracle.py` | **Active** | Forensic analyzer parsing latest log for crashes, `LAST_LR`, and memory exceptions. |

---

## 5. Immediate Action Plan for the New Agent (Iteration #73)

### The Exact Task:
The patch for `0x822CAC34 -> 0x822CACC0` and `0x822CAC4C -> 0x822CACC0` is already formulated and defined in `src/nd_indirect_trap.h` and `apply_bink_branch_fix.py`.
- **Pre-verified Epilogue Integrity:** `neverdead_epilogue_822CACC0` was already verified byte-for-byte against the native disassembly in `NeverDead_base.exe` and `recomp.43.cpp:6840-6865`:
  - Indirect virtual cleanup at `vtable[8]` (`ctx.lr = 0x822CACD4`)
  - Stack frame release (`r1 += 112`)
  - Restoration of `lr` (`r1 - 8`), `r30` (`r1 - 24`), and `r31` (`r1 - 16`)
- **Telemetry I/O Watch:** In Iteration #72, writing 348,875 lines consumed significant CPU in disk I/O. If Iteration #73 hits the 20s timeout without a `FATAL`, check if high-frequency logs (e.g. repeated OOB skips or alloc traces) should be throttled/sampled, or increase the watchdog timeout (`python run_game_watchdog.py 30`).

1. **Add Target Files to `apply_bink_branch_fix.py`:**
   In `apply_bink_branch_fix.py` around line 115, add to `targets`:
   - `gen_dir / "neverdead_recomp.112.cpp"` (contains line 7035: `0x822CAC34 -> 0x822CACC0`)
   - `gen_dir / "neverdead_recomp.104.cpp"` (contains line 6388: `0x822CAC4C -> 0x822CACC0`)
   - `gen_dir / "neverdead_recomp.108.cpp"` (contains line 6914: `0x822CAC98 -> 0x822CAC68` if needed)
2. **Execute Patch and Build:**
   ```powershell
   python apply_bink_branch_fix.py
   powershell -ExecutionPolicy Bypass -File .\build_neverdead.ps1
   python run_game_watchdog.py 20
   python crash_oracle.py
   ```
3. **Verify Subsystem Transition:**
   Check the new log (`neverdead_111.log`). Once `sub_822CABA8` finishes cleanly, observe the engine advancing into UI menu initialization (`GUIMenu\loadingInitial.guiBE`) or spawning `Thread 10: FMV` to play `FMV/rebellion.bik`.

---

## 6. Pairing & Subagent Governance Rules

1. **Single Serialized Implementer:** Never execute concurrent builds or parallel file modifications. Always serialize CMake/Ninja invocations to prevent `.ninja_lock` collisions.
2. **Memory Graph Synchronization:** Query and update the `memory` MCP server per global rule `00_memory_always.md`.
3. **Zero Hallucination / Ground Truth First:** Verify all branch destinations against `..\NeverDead_base.exe` (`file_offset = guest_va - 0x82000000`) before patching.
4. **Preserve Documentation Integrity:** Maintain all existing comments, docstrings, and diagnostic telemetry intact.
