// NeverDead — declaraciones de hooks mid-ASM.
//
// Este header se fuerza con -include ANTES de la PCH, asi que debe ser
// autocontenido: incluye rex/ppc.h (que aporta el alias global PPCRegister).

#pragma once

#include <cstdint>
#include <filesystem>

#include <rex/ppc.h>

void NdVdInitHook();
// r31 = puntero al device D3D del invitado (ver sub_826B8620: mr r31,r3).
// Se pasa desde el midasm hook (registers = ["r31"]) para poder leer el ring
// PM4 sin tocar generated/.
void NdVdSwapHook(PPCRegister& r31);
// Instruccion siguiente al bl VdSwap: el frame ya esta encolado.
void NdVdSwapPostHook(PPCRegister& r31);
void NdVdSetDisplayModeHook(PPCRegister& r3);

// Hooks para desbloqueo de 60 FPS
void NdForceIntervalOneHook(PPCRegister& r11);
void NdAdjustRenderTimeoutHook(PPCRegister& r11);
void NdSetFpsLimitHook(PPCRegister& f0);

// Parches en memoria para presentacion real 60 FPS, supresion de error logs y PSO async
void NdSilenceXctdLog();
void NdInstallPresentGate();
void NdPatchAsyncPipelineCache();

// Inicializacion temprana: debe llamarse desde OnPreSetup (antes de que se cree
// el Presenter/ventana) y es idempotente.
void NdEarlyInit();

// Menu de configuracion UI y optimizaciones (F10 / F1)
void NdOpenConfigUI();
void NdLoadSettingsFromIni();
void NdSaveSettingsToIni();
void NdRunFirstLaunchSetupWizard(const std::filesystem::path& target_assets_dir);
void NdEnsureCesaBik(const std::filesystem::path& target_assets_dir);
void NdEnsureAssetJunctionsOrLinks(const std::filesystem::path& base_dir, const std::filesystem::path& assets_dir);

// Deteccion de estado de render y control de framerate
bool NdIsFpsUnlocked();
bool NdIsIn3dGameplay();

// Auto-avance de inicio: Start -> A -> A -> A
void NdAutoAdvanceInput(PPCContext& ctx, uint8_t* base, uint32_t guest_state);

// Notificacion de draw call ligero y optimizacion de resolves luma
extern "C" void NdOnDrawCall();
extern "C" bool NdShouldSkipLumaResolve();
extern "C" void NdResetXdkShadowStateOnBoundary();

// Traductor guest VA -> host. Lo instala la app; permite a los hooks leer y
// escribir memoria arbitraria del invitado (no solo la imagen del XEX).
typedef uint8_t* (*nd_translate_fn)(uint32_t guest_va);
void nd_set_translate(nd_translate_fn fn);
uint8_t* nd_translate_va(uint32_t guest_va);
void nd_guest_mem_set(void* memory);
