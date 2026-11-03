// DIAG-TEMPORAL(neverdead-indirect-trap): override del dispatch indirecto.
//
// Solo para cazar el caller de saltos OOB. Loguea el lr y target cuando el
// target cae fuera de [0x821A0000, 0x827C05E4) y evita el abort fatal ignorando
// llamadas a memoria no mapeada/no codigo.

#pragma once

#include <cstdint>
#include <cstring>

#include <rex/logging.h>

//=============================================================================
// M2/W3 -- conmutador del memcpy NATIVO de ReXGlue ([rexcrt])
//=============================================================================
// NEVERDEAD_NATIVE_CRT_MEMCPY = 1 retira la implementacion casera de la cola
// de memcpy (neverdead_memcpy_8278AEEC).  DEFECTO: 0 (= comportamiento actual).
//
// POR QUE HAY QUE DEJARLA (evidencia con Capstone, ver handoff_w3.md seccion 1):
//   0x8278AE60  = entrada de la funcion memcpy del CRT (dst=r3, src=r4, n=r5).
//   0x8278AEEC  = ETIQUETA INTERIOR de esa misma funcion: es la rama
//                 "n <= 0x80 y destino desalineado" (dcbtst / addi r4,-8 /
//                 addi r3,-8 y luego copia r7=(n>>3)&0xF qwords + r6=n&7 bytes).
//                 El llamante ya consumio la cabecera, por eso r5 va reducido.
//   Los 3 call sites que usan neverdead_memcpy_8278AEEC (0x8278B0A4,
//   0x8278B08C, 0x8278AFB4, parcheados por apply_bink_branch_fix.py) apuntan a
//   ESA ETIQUETA INTERIOR, no a la entrada 0x8278AE60.  La implementacion
//   nativa rexcrt_memcpy del runtime es una memcpy COMPLETA: sustituir la cola
//   por ella copiaria la cabecera dos veces.  Por tanto la cola casera NO es
//   redundante con [rexcrt] memcpy: NO se retira todavia.
//   Se puede poner a 1 SOLO cuando el arbol v2 haga que memcpy sea una sola
//   funcion (seed correcto) y apply_bink_branch_fix.py pase a ser no-op; entonce
//   los 3 call sites desaparecen y el simbolo deja de necesitarse (si queda
//   alguno, el enlazado falla con "símbolo no definido": es deliberado, para
//   que no se pueda activar el flag con el parche bink todavia activo).
#ifndef NEVERDEAD_NATIVE_CRT_MEMCPY
#define NEVERDEAD_NATIVE_CRT_MEMCPY 0
#endif

DECLARE_REX_FUNC(sub_8278EB50);
DECLARE_REX_FUNC(sub_82252E20);
DECLARE_REX_FUNC(sub_8278BD9C);
void sub_821E0FA0(PPCContext& ctx, uint8_t* base);

inline void neverdead_handler_824F6DB8(PPCContext& ctx, uint8_t* base) {
  ctx.r3.s64 = ctx.r1.s64 + 0x90;
  ctx.lr = 0x824F6DC0;
  sub_82252E20(ctx, base);
  ctx.r1.s64 += 0x100;
  sub_8278BD9C(ctx, base);
}

inline void neverdead_epilogue_8269987C(PPCContext& ctx, uint8_t* base) {
  uint32_t r11_val = (ctx.r16.u32 & 0xFF);
  bool flag = false;
  if (r11_val != 0 && (ctx.r18.u32 & 0xFF) != 0) {
    flag = true;
  } else {
    uint32_t w11 = REX_LOAD_U32(ctx.r17.u32 - 17012);
    flag = (w11 != 254);
  }
  REX_STORE_U8((uint32_t)(-2102198272 + 18906), flag ? 1 : 0);
  uint32_t b1 = REX_LOAD_U8(ctx.r22.u32 + 1);
  if (b1 != 0 && REX_LOAD_U8(ctx.r22.u32 + 0) == 0) {
    ctx.r3.s64 = 0;
  } else {
    ctx.r3.u64 = ctx.r18.u64;
  }
  ctx.r1.s64 += 224;
  ctx.r16.u64 = REX_LOAD_U64(ctx.r1.u32 - 136);
  ctx.r17.u64 = REX_LOAD_U64(ctx.r1.u32 - 128);
  ctx.r18.u64 = REX_LOAD_U64(ctx.r1.u32 - 120);
  ctx.r19.u64 = REX_LOAD_U64(ctx.r1.u32 - 112);
  ctx.r20.u64 = REX_LOAD_U64(ctx.r1.u32 - 104);
  ctx.r21.u64 = REX_LOAD_U64(ctx.r1.u32 - 96);
  ctx.r22.u64 = REX_LOAD_U64(ctx.r1.u32 - 88);
  ctx.r23.u64 = REX_LOAD_U64(ctx.r1.u32 - 80);
  ctx.r24.u64 = REX_LOAD_U64(ctx.r1.u32 - 72);
  ctx.r25.u64 = REX_LOAD_U64(ctx.r1.u32 - 64);
  ctx.r26.u64 = REX_LOAD_U64(ctx.r1.u32 - 56);
  ctx.r27.u64 = REX_LOAD_U64(ctx.r1.u32 - 48);
  ctx.r28.u64 = REX_LOAD_U64(ctx.r1.u32 - 40);
  ctx.r29.u64 = REX_LOAD_U64(ctx.r1.u32 - 32);
  ctx.r30.u64 = REX_LOAD_U64(ctx.r1.u32 - 24);
  ctx.r31.u64 = REX_LOAD_U64(ctx.r1.u32 - 16);
  ctx.lr = REX_LOAD_U32(ctx.r1.u32 - 8);
}

#if !NEVERDEAD_NATIVE_CRT_MEMCPY
void neverdead_memcpy_8278AEEC(PPCContext& ctx, uint8_t* base);
#endif
void neverdead_epilogue_822CACC0(PPCContext& ctx, uint8_t* base);

#undef REX_CALL_INDIRECT_FUNC
#define REX_CALL_INDIRECT_FUNC(x)                                                \
  do {                                                                           \
    REX_PROFILE_INDIRECT_DISPATCH();                                             \
    uint32_t rex_trap_target_ = (uint32_t)(x);                                   \
    const uint32_t rex_r3_pre_ = ctx.r3.u32;                                     \
    if (rex_trap_target_ < 0x821A0000u ||                                         \
        rex_trap_target_ >= 0x827C05E4u) {                                        \
      ctx.r3.u64 = 0;                                                          \
      if (ctx.lr == 0x8242D02Cu) {                                               \
        ctx.r30.u64 = ctx.r29.u64;                                             \
      }                                                                         \
      /* DIAG(chunk-iterator): 0x821E0FBC es el bctrl del bucle del iterador de \
         chunks (sub_821E0FA0). Con target 0 significa que [r3] no es una vtable \
         valida o que [vtable+4] es 0. Se vuelca el estado UNA vez por ventana. */\
      if ((uint32_t)ctx.lr == 0x821E0FBCu) {                                     \
        static uint32_t s_ci_probe_ = 0;                                         \
        if (++s_ci_probe_ <= 3) {                                                \
          const uint32_t r3_ = rex_r3_pre_;                                      \
          const uint32_t r27_ = ctx.r27.u32;                                     \
          const uint32_t r28_ = ctx.r28.u32;                                     \
          const bool r27_ok_ = (r27_ >= 0x10000u && r27_ < 0x90000000u);          \
          const uint32_t off_ = r27_ok_ ? REX_LOAD_U32(r27_ + 20) : 0u;           \
          const uint32_t vt_ = (r3_ >= 0x10000u && r3_ < 0x80000000u)            \
                                   ? REX_LOAD_U32(r3_) : 0u;                     \
          const uint32_t fn_ = (vt_ >= 0x82000000u && vt_ < 0x827C05E4u)         \
                                   ? REX_LOAD_U32(vt_ + 4) : 0u;                \
          REXLOG_ERROR("neverdead: CI-PROBE #{} r3=0x{:08X} vt=0x{:08X} fn=0x{:08X} " \
                       "r24=0x{:08X} r25=0x{:08X} r26=0x{:08X} r27=0x{:08X} "     \
                       "r28=0x{:08X} r29=0x{:08X} [r27+20]=0x{:08X} target=0x{:08X}", \
                       s_ci_probe_, r3_, vt_, fn_, ctx.r24.u32, ctx.r25.u32,        \
                       ctx.r26.u32, r27_, r28_, ctx.r29.u32, off_, rex_trap_target_); \
        }                                                                        \
      }                                                                          \
      static uint32_t rex_oob_count_ = 0;                                        \
      if (++rex_oob_count_ <= 25 || (rex_oob_count_ % 100000 == 0)) {               \
        REXLOG_ERROR("neverdead: TRAP-OOB caller lr=0x{:08X} target=0x{:08X} (skipped, #{})", \
                     (uint32_t)ctx.lr, rex_trap_target_, rex_oob_count_);        \
      }                                                                           \
      break;                                                                     \
    }                                                                            \
    PPCFunc* rex_indirect_func_;                                                  \
    if ((uint32_t)(rex_trap_target_ - REX_CODE_BASE) <                            \
        REX_CODE_SIZE + REX_THUNK_RESERVE_SIZE) [[likely]] {                     \
      rex_indirect_func_ = REX_LOOKUP_FUNC(base, rex_trap_target_);              \
    } else {                                                                     \
      rex_indirect_func_ = nullptr;                                               \
    }                                                                            \
    if (!rex_indirect_func_) [[unlikely]] {                                      \
      ctx.last_indirect_target = rex_trap_target_;                               \
      if (rex_trap_target_ == 0x82378300u) {                                     \
        float f_ = (float)ctx.f0.f64;                                            \
        uint32_t u_;                                                             \
        std::memcpy(&u_, &f_, 4);                                                \
        REX_STORE_U32(ctx.r5.u32, u_);                                           \
        break;                                                                   \
      }                                                                          \
      if (rex_trap_target_ == 0x8269987Cu) {                                     \
        neverdead_epilogue_8269987C(ctx, base);                                  \
        break;                                                                   \
      }                                                                          \
      if (rex_trap_target_ == 0x822CACC0u) {                                     \
        neverdead_epilogue_822CACC0(ctx, base);                                  \
        break;                                                                   \
      }                                                                          \
      if (rex_trap_target_ == 0x824DDF90u) {                                     \
        REXLOG_INFO("neverdead: unregistered indirect call 0x824DDF90 lr=0x{:08X} (handled r3=1)", (uint32_t)ctx.lr); \
        ctx.r3.u64 = 1;                                                          \
        break;                                                                   \
      }                                                                          \
      if (rex_trap_target_ == 0x824F6DB8u) {                                     \
        REXLOG_INFO("neverdead: unregistered indirect call 0x824F6DB8 lr=0x{:08X} (stack-safe epilogue)", (uint32_t)ctx.lr); \
        neverdead_handler_824F6DB8(ctx, base);                                   \
        break;                                                                   \
      }                                                                          \
      REXLOG_WARN("neverdead: unregistered indirect call target=0x{:08X} lr=0x{:08X} (safe stub r3=0)", \
                  rex_trap_target_, (uint32_t)ctx.lr);                           \
      ctx.r3.u64 = 0;                                                            \
      break;                                                                     \
    }                                                                            \
    if (rex_indirect_func_) {                                                    \
      rex_indirect_func_(ctx, base);                                             \
    }                                                                            \
  } while (0)

inline void neverdead_epilogue_822CACC0(PPCContext& ctx, uint8_t* base) {
  const uint32_t r30_val = (ctx.r30.u32 >= 0x82000000u) ? ctx.r30.u32 : 0x828E0000u;
  const uint32_t obj = (r30_val >= 0x10000u) ? REX_LOAD_U32(r30_val + 4876) : 0;
  static bool s_epilogue_logged = false;
  if (!s_epilogue_logged) {
    s_epilogue_logged = true;
    REXLOG_INFO("neverdead: epilogue_822CACC0 enter r1=0x{:08X} r30=0x{:08X} obj=0x{:08X} lr=0x{:08X}",
                ctx.r1.u32, r30_val, obj, (uint32_t)ctx.lr);
  }
  if (obj >= 0x10000u && obj < 0x80000000u) {
    const uint32_t vtbl = REX_LOAD_U32(obj);
    if (vtbl >= 0x82000000u && vtbl < 0x827C05E4u) {
      const uint32_t target_fn = REX_LOAD_U32(vtbl + 8);
      if (target_fn >= 0x821A0000u && target_fn < 0x827C05E4u) {
        ctx.r3.u64 = obj;
        ctx.lr = 0x822CACD4;
        REX_CALL_INDIRECT_FUNC(target_fn);
      }
    }
  }

  const uint32_t backchain = REX_LOAD_U32(ctx.r1.u32);
  if (backchain == ctx.r1.u32 + 112) {
    ctx.r1.s64 += 112;
    ctx.r12.u64 = REX_LOAD_U32(ctx.r1.u32 - 8);
    ctx.lr = ctx.r12.u64;
    ctx.r30.u64 = REX_LOAD_U64(ctx.r1.u32 - 24);
    ctx.r31.u64 = REX_LOAD_U64(ctx.r1.u32 - 16);
  }
}

#if !NEVERDEAD_NATIVE_CRT_MEMCPY
inline void neverdead_memcpy_8278AEEC(PPCContext& ctx, uint8_t* base) {
  uint32_t r7 = (ctx.r5.u32 >> 3) & 0xF;
  uint32_t r6 = ctx.r5.u32 & 0x7;

  // Qword copy loop (loc_8278AF04)
  for (uint32_t i = 0; i < r7; ++i) {
    ctx.r4.u32 += 8;
    ctx.r3.u32 += 8;
    uint64_t val = REX_LOAD_U64(ctx.r4.u32);
    REX_STORE_U64(ctx.r3.u32, val);
  }

  // Trailing bytes (loc_8278AF10)
  if (r6 != 0) {
    if (r6 == 4) {
      uint32_t align = ctx.r3.u32 & 0x3;
      if (align == 0) {
        uint32_t val = REX_LOAD_U32(ctx.r4.u32 + 8);
        REX_STORE_U32(ctx.r3.u32 + 8, val);
      } else {
        for (uint32_t i = 0; i < 4; ++i) {
          uint8_t b_val = REX_LOAD_U8(ctx.r4.u32 + 8 + i);
          REX_STORE_U8(ctx.r3.u32 + 8 + i, b_val);
        }
      }
    } else {
      ctx.r3.s64 += 7;
      ctx.r4.s64 += 7;
      for (uint32_t i = 0; i < r6; ++i) {
        ctx.r4.u32 += 1;
        ctx.r3.u32 += 1;
        uint8_t b_val = REX_LOAD_U8(ctx.r4.u32);
        REX_STORE_U8(ctx.r3.u32, b_val);
      }
    }
  }

  // Restore destination pointer saved at -8(r1)
  ctx.r3.u64 = REX_LOAD_U64(ctx.r1.u32 - 8);
}
#endif  // !NEVERDEAD_NATIVE_CRT_MEMCPY
