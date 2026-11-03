// DIAG(neverdead-indirect-guard): sonda de diagnostico para saltos indirectos.
//
// Solo LOGUEA cuando el target cae fuera del rango de codigo recompilado
// [0x821A0000, 0x827C05E4). NO cambia la semantica valida: tras loguear, el
// dispatch original (REX_CALL_INDIRECT_FUNC / ResolveIndirectFunction) sigue
// su curso sin alteraciones: sin returns, sin throws, sin mutar ctx/base.
//
// Uso en codigo generado (via apply_indirect_guard.py, nunca a mano):
//   ctx.lr = 0x824D9EAC;
//   ND_DIAG_INDIRECT(ctx, base, nd_r11_pre_9E94, 0x824D9EAC);
//   REX_CALL_INDIRECT_FUNC(ctx.ctr.u32);
//
// El header es dual-use: compila tanto en TUs recomp (pch con logging) como
// en el host (neverdead_app.h). Solo depende de <rex/logging.h>.
// FASE4-HOST: verificado que `base` (uint8_t* arena) esta en scope en todo
// DEFINE_REX_FUNC (firma ctx,base) y que REXLOG_ERROR existe con formato fmt.

#pragma once

#include <cstdint>
#include <cstring>

#include <rex/logging.h>

namespace neverdead::diag {

// Rango de codigo recompilado (tabla de funcs del log 010). Coincide con
// REX_CODE_BASE / REX_CODE_SIZE del pch (0x821A0000 + 0x6205E4 = 0x827C05E4).
inline constexpr uint32_t kCodeBase = 0x821A0000u;
inline constexpr uint32_t kCodeEnd = 0x827C05E4u;  // exclusivo

// Ventana de imagen guest para el peek seguro (REX_IMAGE_BASE/SIZE del pch:
// 0x82000000 + 0xC10000 = 0x82C10000, coincide con el log 010).
inline constexpr uint32_t kImageBase = 0x82000000u;
inline constexpr uint32_t kImageSize = 0x00C10000u;

inline constexpr bool InCodeRange(uint32_t addr) {
  return addr >= kCodeBase && addr < kCodeEnd;
}

// Lectura big-endian segura (memcpy: sin problemas de alineamiento ni
// aliasing). Solo llamar con guest dentro de [kImageBase, kImageBase+size).
inline uint32_t PeekGuestBe32(const uint8_t* base, uint32_t guest) {
  uint32_t raw = 0;
  std::memcpy(&raw, base + guest, sizeof(raw));
  return (raw >> 24) | ((raw >> 8) & 0xFF00u) | ((raw << 8) & 0xFF0000u) |
         (raw << 24);
}

}  // namespace neverdead::diag

// ND_DIAG_INDIRECT(ctx, base, r11_pre, lr_site): loguea r11_pre/lr/target solo
// si el target (ctx.ctr, ya fijado por el mtctr del guest) esta fuera de rango.
// El peek de guest[target] solo se intenta dentro de la ventana de imagen para
// no leer fuera de la arena (p. ej. target 0xFFFFFBFF + offset fisico saldria
// de la arena de 4GB). Sin efectos secundarios: no toca ctx ni base.
#define ND_DIAG_INDIRECT(ctx, base, r11_pre, lr_site)                        \
  do {                                                                       \
    const uint32_t nd_target_ = static_cast<uint32_t>((ctx).ctr.u32);        \
    if (nd_target_ < ::neverdead::diag::kCodeBase ||                         \
        nd_target_ >= ::neverdead::diag::kCodeEnd) {                         \
      uint32_t nd_peek_ = 0;                                                 \
      const char* nd_peek_st_ = "n/a";                                       \
      if (nd_target_ >= ::neverdead::diag::kImageBase &&                     \
          nd_target_ <=                                                      \
              ::neverdead::diag::kImageBase + ::neverdead::diag::kImageSize - \
                  4u) {                                                      \
        nd_peek_ = ::neverdead::diag::PeekGuestBe32(                         \
            reinterpret_cast<const uint8_t*>(base), nd_target_);             \
        nd_peek_st_ = "ok";                                                  \
      }                                                                      \
      REXLOG_ERROR(                                                          \
          "neverdead: indirect fuera de rango r11_pre=0x{:08X} lr=0x{:08X} "  \
          "target=0x{:08X} peek={} (0x{:08X})",                              \
          static_cast<uint32_t>(r11_pre), static_cast<uint32_t>(lr_site),    \
          nd_target_, nd_peek_st_, nd_peek_);                                \
    }                                                                        \
  } while (0)
