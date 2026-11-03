// NeverDead — declaraciones de hooks mid-ASM.
//
// Este header se fuerza con -include ANTES de la PCH, asi que debe ser
// autocontenido: incluye rex/ppc.h (que aporta el alias global PPCRegister).

#pragma once

#include <cstdint>

#include <rex/ppc.h>

void NdVdInitHook();
void NdVdSwapHook();
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

// Menu de configuracion UI y optimizaciones (F1)
void NdOpenConfigUI();

// Traductor guest VA -> host. Lo instala la app; permite a los hooks leer y
// escribir memoria arbitraria del invitado (no solo la imagen del XEX).
typedef uint8_t* (*nd_translate_fn)(uint32_t guest_va);
void nd_set_translate(nd_translate_fn fn);
uint8_t* nd_translate_va(uint32_t guest_va);
void nd_guest_mem_set(void* memory);
