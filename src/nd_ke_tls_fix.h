// FIX(neverdead-ke-tls-set-value): override host del import xboxkrnl#0x155.
//
// Ground truth (swarm run 3, doble evidencia):
// - El thunk recompilado `sub_827BD8F4` son los bytes crudos del XEX:
//   `.long 0x1010155; .long 0x2010155; mtctr r11; bctr`. Es el stub de
//   importacion de xboxkrnl.exe ordinal 0x155 = KeTlsSetValue (vecinos
//   0x152 KeTlsAlloc en 0x827BD914, 0x154 KeTlsGetValue en 0x827BD8E4).
// - Ningun caller fija r11 (todos `mflr r12; bl` al stub via sub_8278BD48):
//   el salto va a basura (0xFFFFFBF7 / 0x827F0000, no-determinista) y muere
//   en `Call to invalid or unregistered function` (exit 0xC0000409).
// - El runtime conoce KeTls* (strings en rexruntime) pero el cuerpo
//   recompilado del stub es inalcanzable por esa via: la llamada es directa.
//
// Mecanismo (verificado en neverdead_pch.h:74-77 + rex/hook.h:49):
// DEFINE_REX_FUNC en no-Apple es alias DEBIL; REX_HOOK_RAW define el simbolo
// FUERTE con la misma firma (ctx, base) y lo sobrescribe en el link. Cubre
// las 3 vias: llamada directa `bl`, `SetFunction(0x827BD8F4, ...)` del
// registrar e indirectas por tabla (todas toman la direccion del simbolo).
// Cero toques a generated/, cero cambios al dispatcher.
//
// Semantica (funcional, no no-op: es primitiva TLS del path de arranque):
// KeTlsSetValue(index=r3, value=r4) -> r3=1 exito / 0 fallo.
// 2026-10-17 Stage B: implementado via __imp__KeTlsSetValue (rexruntime.lib).

#pragma once

#include <cstdint>

#include <rex/hook.h>
#include <rex/logging.h>
#include <rex/system/xthread.h>

namespace neverdead::fix {

inline bool g_ke_tls_announced = false;

inline uint32_t PeekGuestBe32(const uint8_t* base, uint32_t guest) {
  uint32_t raw = 0;
  std::memcpy(&raw, base + guest, sizeof(raw));
  return (raw >> 24) | ((raw >> 8) & 0xFF00u) | ((raw << 8) & 0xFF0000u) |
         (raw << 24);
}

// DIAG-TEMPORAL: mini stack-walk guest (PPC: [r1]=backchain, [r1+4]=saved lr).
// Solo lectura, acotado (10 frames, backchain creciente, ventana 64KB).
inline void LogGuestStack(const uint8_t* base, uint32_t r1) {
  uint32_t cur = r1;
  for (int i = 0; i < 10 && cur != 0; ++i) {
    if (cur < 0x70000000u || cur > 0x72000000u) {
      break;
    }
    const uint32_t back = PeekGuestBe32(base, cur);
    const uint32_t lr = PeekGuestBe32(base, cur + 4);
    REXLOG_INFO("neverdead: guest-stack[{}] r1=0x{:08X} lr=0x{:08X}", i, cur,
                lr);
    if (back <= cur || back > cur + 0x10000u) {
      break;
    }
    cur = back;
  }
}

}  // namespace neverdead::fix

REX_EXTERN(__imp__KeTlsSetValue);

REX_HOOK_RAW(sub_827BD8F4) {
  const uint32_t index = ctx.r3.u32;
  const uint32_t value = ctx.r4.u32;
  if (!neverdead::fix::g_ke_tls_announced) {
    neverdead::fix::g_ke_tls_announced = true;
    REXLOG_INFO("neverdead: KeTlsSetValue override activo (xboxkrnl#0x155)");
  }
  auto* thread = rex::system::XThread::GetCurrentThread();
  // NOTA: LogGuestStack desactivado (la cima del stack guest 0x7010FF80 esta
  // junto a pagina sin mapear 0x70110000 y el peek hunde el proceso; el stack
  // host lo cubre crash_diag.cpp). Se conserva para uso futuro con
  // QueryProtect.
  (void)neverdead::fix::LogGuestStack;
  REXLOG_INFO(
      "neverdead: KeTlsSetValue ctx r1=0x{:08X} r2=0x{:08X} r13=0x{:08X} "
      "lr=0x{:08X} index=0x{:08X} value=0x{:08X}",
      (uint32_t)ctx.r1.u32, (uint32_t)ctx.r2.u32, (uint32_t)ctx.r13.u32,
      (uint32_t)ctx.lr, index, value);
  // STAGE B (2026-10-17): llamada REAL al wrapper del runtime (simbolo
  // __imp__KeTlsSetValue de rexruntime.lib, verificado con llvm-nm).
  // Semantica esperada: r3 = 1 exito / 0 fallo. Reemplaza EXPERIMENTO-A.
  (void)thread;
  (void)base;
  __imp__KeTlsSetValue(ctx, base);
}
