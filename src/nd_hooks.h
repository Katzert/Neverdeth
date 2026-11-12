// Neverdeth - Mid-ASM and runtime hook declarations.
//
// Force-included via -include before the Clang PCH, so keep this header
// minimal and self-contained (rex/ppc.h provides PPCRegister and PPCContext).

#pragma once

#include <cstdint>
#include <filesystem>

#include <rex/ppc.h>

// Video initialization and swap instrumentation hooks
void NdVdInitHook();
void NdVdSwapHook(PPCRegister& r31);
void NdVdSwapPostHook(PPCRegister& r31);
void NdVdSetDisplayModeHook(PPCRegister& r3);

// Framerate unlock and timing hooks
void NdForceIntervalOneHook(PPCRegister& r11);
void NdAdjustRenderTimeoutHook(PPCRegister& r11);
void NdSetFpsLimitHook(PPCRegister& f0);

// Runtime memory patches for presentation gating, log filtering, and async PSO compilation
void NdSilenceXctdLog();
void NdInstallPresentGate();
void NdPatchAsyncPipelineCache();

// Idempotent early initialization; must be called from OnPreSetup before window creation
void NdEarlyInit();

// Settings UI and INI persistence (F10)
void NdOpenConfigUI();
void NdLoadSettingsFromIni();
void NdSaveSettingsToIni();

// First-launch asset setup wizard and directory link helpers
void NdRunFirstLaunchSetupWizard(const std::filesystem::path& target_assets_dir);
void NdEnsureCesaBik(const std::filesystem::path& target_assets_dir);
void NdEnsureAssetJunctionsOrLinks(const std::filesystem::path& base_dir,
                                   const std::filesystem::path& assets_dir);

// Render state queries
bool NdIsFpsUnlocked();
bool NdIsIn3dGameplay();

// Startup auto-navigation input injection
void NdAutoAdvanceInput(PPCContext& ctx, uint8_t* base, uint32_t guest_state);

// Draw call tracking, adaptive exposure decimation, and shadow-state boundary reset
extern "C" void NdOnDrawCall();
extern "C" bool NdShouldSkipLumaResolve();
extern "C" void NdResetXdkShadowStateOnBoundary();

// Guest VA -> host address translation registered by the application
uint8_t* nd_translate_va(uint32_t guest_va);
void nd_guest_mem_set(void* memory);
