#pragma once

#include <cstdint>
#include <rex/logging.h>
#include "nd_indirect_trap.h"

// Forward declare callees invoked by NeverDead Asura WinMain / Main Loop
DECLARE_REX_FUNC(sub_82220888);
DECLARE_REX_FUNC(sub_824DBE58);
DECLARE_REX_FUNC(sub_823C0DD0);
DECLARE_REX_FUNC(sub_823C8048);
DECLARE_REX_FUNC(sub_82788B30);
DECLARE_REX_FUNC(sub_823C9948);
DECLARE_REX_FUNC(sub_823C96E0);
DECLARE_REX_FUNC(sub_823C9E40);
DECLARE_REX_FUNC(sub_826C6C10);
DECLARE_REX_FUNC(sub_824DA078);
DECLARE_REX_FUNC(sub_8220B188);
DECLARE_REX_FUNC(sub_8278BD7C);
DECLARE_REX_FUNC(sub_8278BD80);
DECLARE_REX_FUNC(sub_8278BD84);
DECLARE_REX_FUNC(sub_8278BD88);

inline void neverdead_main_loop(PPCContext& ctx, uint8_t* base) {
  REXLOG_INFO("neverdead: Entering Asura main loop (0x823C5DAC)...");

  // FIX(neverdead-timer-init): Initialize Asura timebase frequency & ticks.
  // sub_82220888 was skipped by severed entrypoint bridge.
  // 0x828DF84C = timer frequency (50,000,000 Hz)
  // 0x82925968 = start-of-frame timebase ticks
  float* p_freq = (float*)REX_RAW_ADDR(0x828DF84C);
  if (*p_freq <= 0.0f) {
    REXLOG_INFO("neverdead: Priming Asura timebase & timer frequency via sub_82220888...");
    sub_82220888(ctx, base);
    if (*p_freq <= 0.0f) {
      *p_freq = 50000000.0f;
    }
    uint64_t* p_tb = (uint64_t*)REX_RAW_ADDR(0x82925968);
    if (*p_tb == 0) {
      *p_tb = REX_QUERY_TIMEBASE();
    }
    REXLOG_INFO("neverdead: Timer primed: freq={:.1f} Hz, start_tb=0x{:016X}", *p_freq, *p_tb);
  }

  uint8_t* p_quit = (uint8_t*)REX_RAW_ADDR(0x82B312DE);
  uint32_t* p_state = (uint32_t*)REX_RAW_ADDR(0x82B312F8);
  REXLOG_INFO("neverdead: Engine state before first frame: quit={}, state={}", *p_quit, *p_state);
  *p_quit = 0; // ensure quit flag is 0 at boot

  // 0x823C5DAC: bl 0x824dbe58
  ctx.lr = 0x823C5DB0;
  sub_824DBE58(ctx, base);

  REXLOG_INFO("neverdead: sub_824DBE58 boot check returned r3=0x{:016X} (quit={}, state={})",
              ctx.r3.u64, *p_quit, *p_state);
  if ((ctx.r3.u32 & 0xFF) == 0) {
    REXLOG_INFO("neverdead: Overriding r3=1 on boot frame to enter Asura main loop!");
    ctx.r3.u64 = 1;
  }

  // 0x823C5DB0: clrlwi r10, r3, 24
  ctx.r10.u64 = ctx.r3.u32 & 0xFF;

  // 0x823C5DB4: cmplwi cr6, r10, 0
  ctx.cr6.compare<uint32_t>(ctx.r10.u32, 0, ctx.xer);

  // 0x823C5DB8: beq cr6, 0x823c5f74
  if (ctx.cr6.eq) goto loc_823C5F74;

  // 0x823C5DBC: lis r10, -32078 (0x82B20000)
  ctx.r10.s64 = -2102263808;
  // 0x823C5DC0: lis r9, -32078
  ctx.r9.s64 = -2102263808;
  // 0x823C5DC4: lis r11, -32078
  ctx.r11.s64 = -2102263808;
  // 0x823C5DC8: lis r22, -32077 (0x82B30000)
  ctx.r22.s64 = -2102198272;
  // 0x823C5DCC: lis r28, -32078
  ctx.r28.s64 = -2102263808;
  // 0x823C5DD0: lis r27, -32078
  ctx.r27.s64 = -2102263808;
  // 0x823C5DD4: lis r26, -32078
  ctx.r26.s64 = -2102263808;

  // 0x823C5DD8: addi r25, r10, 28516 (0x6F64)
  ctx.r25.s64 = ctx.r10.s64 + 28516;
  // 0x823C5DDC: addi r24, r9, 28480 (0x6F40)
  ctx.r24.s64 = ctx.r9.s64 + 28480;
  // 0x823C5DE0: addi r23, r11, 28468 (0x6F34)
  ctx.r23.s64 = ctx.r11.s64 + 28468;

loc_823C5DE4:
  // F0 (PATCH neverdead-crt-gpr-helpers): guard de invariante de pila.
  // El marco de sub_823C5DAC es fijo (0xB0) y se reserva UNA vez en su
  // prologo; r1 no puede cambiar entre iteraciones. Si cambia, el desbordamiento
  // es aqui y no 200 lineas despues: convierte el `lr = 0xBEBEBEBE` en un
  // diagnostico localizado con el frame exacto y el delta.
  {
    static const uint32_t s_r1_base = ctx.r1.u32;
    static uint32_t s_frames = 0;
    ++s_frames;
    if (ctx.r1.u32 != s_r1_base) {
      const int32_t d = static_cast<int32_t>(ctx.r1.u32 - s_r1_base);
      if (s_frames <= 8 || (s_frames % 60) == 0) {
        REXLOG_ERROR("neverdead: STACK-DRIFT frame={} r1=0x{:08X} base=0x{:08X} delta={:+d}",
                     s_frames, ctx.r1.u32, s_r1_base, d);
      }
    } else if (s_frames <= 5 || (s_frames % 120) == 0) {
      REXLOG_INFO("neverdead: STACK-OK frame={} r1=0x{:08X}", s_frames, ctx.r1.u32);
    }
  }
  // 0x823C5DE4: bl 0x823c0dd0
  ctx.lr = 0x823C5DE8;
  sub_823C0DD0(ctx, base);

  // 0x823C5DE8: lwz r11, 28464(r26)
  ctx.r11.u64 = REX_LOAD_U32(ctx.r26.u32 + 28464);
  // 0x823C5DEC: li r29, 0
  ctx.r29.s64 = 0;
  // 0x823C5DF0: cmplwi cr6, r11, 0
  ctx.cr6.compare<uint32_t>(ctx.r11.u32, 0, ctx.xer);
  // 0x823C5DF4: beq cr6, 0x823c5e48
  if (ctx.cr6.eq) goto loc_823C5E48;

  // 0x823C5DF8: mr r30, r23
  ctx.r30.u64 = ctx.r23.u64;

loc_823C5DFC:
  // 0x823C5DFC: lwz r31, 0(r30)
  ctx.r31.u64 = REX_LOAD_U32(ctx.r30.u32 + 0);
  // 0x823C5E00: lwz r10, 152(r31) (0x98)
  ctx.r10.u64 = REX_LOAD_U32(ctx.r31.u32 + 152);
  // 0x823C5E04: cmplwi cr6, r10, 997 (0x3E5)
  ctx.cr6.compare<uint32_t>(ctx.r10.u32, 997, ctx.xer);
  // 0x823C5E08: beq cr6, 0x823c5e38
  if (ctx.cr6.eq) goto loc_823C5E38;

  // 0x823C5E0C: mr r3, r31
  ctx.r3.u64 = ctx.r31.u64;
  // 0x823C5E10: bl 0x823c8048
  ctx.lr = 0x823C5E14;
  sub_823C8048(ctx, base);

  // 0x823C5E14: mr r3, r31
  ctx.r3.u64 = ctx.r31.u64;
  // 0x823C5E18: bl 0x82788b30
  ctx.lr = 0x823C5E1C;
  sub_82788B30(ctx, base);

  // 0x823C5E1C: lwz r11, 28464(r26)
  ctx.r11.u64 = REX_LOAD_U32(ctx.r26.u32 + 28464);
  // 0x823C5E20: addi r11, r11, -1
  ctx.r11.s64 = ctx.r11.s64 - 1;
  // 0x823C5E24: rlwinm r10, r11, 2, 0, 29 (slwi r10, r11, 2)
  ctx.r10.u64 = __builtin_rotateleft64(ctx.r11.u32 | (ctx.r11.u64 << 32), 2) & 0xFFFFFFFC;
  // 0x823C5E28: stw r11, 28464(r26)
  REX_STORE_U32(ctx.r26.u32 + 28464, ctx.r11.u32);
  // 0x823C5E2C: lwzx r9, r10, r23
  ctx.r9.u64 = REX_LOAD_U32(ctx.r10.u32 + ctx.r23.u32);
  // 0x823C5E30: stw r9, 0(r30)
  REX_STORE_U32(ctx.r30.u32 + 0, ctx.r9.u32);
  // 0x823C5E34: b 0x823c5e40
  goto loc_823C5E40;

loc_823C5E38:
  // 0x823C5E38: addi r29, r29, 1
  ctx.r29.s64 = ctx.r29.s64 + 1;
  // 0x823C5E3C: addi r30, r30, 4
  ctx.r30.s64 = ctx.r30.s64 + 4;

loc_823C5E40:
  // 0x823C5E40: cmplw cr6, r29, r11
  ctx.cr6.compare<uint32_t>(ctx.r29.u32, ctx.r11.u32, ctx.xer);
  // 0x823C5E44: blt cr6, 0x823c5dfc
  if (ctx.cr6.lt) goto loc_823C5DFC;

loc_823C5E48:
  // 0x823C5E48: lwz r11, 28476(r27)
  ctx.r11.u64 = REX_LOAD_U32(ctx.r27.u32 + 28476);
  // 0x823C5E4C: li r29, 0
  ctx.r29.s64 = 0;
  // 0x823C5E50: cmplwi cr6, r11, 0
  ctx.cr6.compare<uint32_t>(ctx.r11.u32, 0, ctx.xer);
  // 0x823C5E54: beq cr6, 0x823c5eac
  if (ctx.cr6.eq) goto loc_823C5EAC;

  // 0x823C5E58: mr r31, r24
  ctx.r31.u64 = ctx.r24.u64;

loc_823C5E5C:
  // 0x823C5E5C: lwz r30, 0(r31)
  ctx.r30.u64 = REX_LOAD_U32(ctx.r31.u32 + 0);
  // 0x823C5E60: mr r3, r30
  ctx.r3.u64 = ctx.r30.u64;
  // 0x823C5E64: bl 0x823c9948
  ctx.lr = 0x823C5E68;
  sub_823C9948(ctx, base);

  // 0x823C5E68: lbz r11, 42(r30) (0x2A)
  ctx.r11.u64 = REX_LOAD_U8(ctx.r30.u32 + 42);
  // 0x823C5E6C: cmplwi cr6, r11, 0
  ctx.cr6.compare<uint32_t>(ctx.r11.u32, 0, ctx.xer);
  // 0x823C5E70: bne cr6, 0x823c5e98
  if (!ctx.cr6.eq) goto loc_823C5E98;

  // 0x823C5E74: mr r3, r30
  ctx.r3.u64 = ctx.r30.u64;
  // 0x823C5E78: bl 0x823c96e0
  ctx.lr = 0x823C5E7C;
  sub_823C96E0(ctx, base);

  // 0x823C5E7C: lwz r11, 28476(r27)
  ctx.r11.u64 = REX_LOAD_U32(ctx.r27.u32 + 28476);
  // 0x823C5E80: addi r11, r11, -1
  ctx.r11.s64 = ctx.r11.s64 - 1;
  // 0x823C5E84: rlwinm r10, r11, 2, 0, 29
  ctx.r10.u64 = __builtin_rotateleft64(ctx.r11.u32 | (ctx.r11.u64 << 32), 2) & 0xFFFFFFFC;
  // 0x823C5E88: stw r11, 28476(r27)
  REX_STORE_U32(ctx.r27.u32 + 28476, ctx.r11.u32);
  // 0x823C5E8C: lwzx r9, r10, r24
  ctx.r9.u64 = REX_LOAD_U32(ctx.r10.u32 + ctx.r24.u32);
  // 0x823C5E90: stw r9, 0(r31)
  REX_STORE_U32(ctx.r31.u32 + 0, ctx.r9.u32);
  // 0x823C5E94: b 0x823c5ea4
  goto loc_823C5EA4;

loc_823C5E98:
  // 0x823C5E98: lwz r11, 28476(r27)
  ctx.r11.u64 = REX_LOAD_U32(ctx.r27.u32 + 28476);
  // 0x823C5E9C: addi r29, r29, 1
  ctx.r29.s64 = ctx.r29.s64 + 1;
  // 0x823C5EA0: addi r31, r31, 4
  ctx.r31.s64 = ctx.r31.s64 + 4;

loc_823C5EA4:
  // 0x823C5EA4: cmplw cr6, r29, r11
  ctx.cr6.compare<uint32_t>(ctx.r29.u32, ctx.r11.u32, ctx.xer);
  // 0x823C5EA8: blt cr6, 0x823c5e5c
  if (ctx.cr6.lt) goto loc_823C5E5C;

loc_823C5EAC:
  // 0x823C5EAC: lwz r11, 28512(r28)
  ctx.r11.u64 = REX_LOAD_U32(ctx.r28.u32 + 28512);
  // 0x823C5EB0: li r29, 0
  ctx.r29.s64 = 0;
  // 0x823C5EB4: cmplwi cr6, r11, 0
  ctx.cr6.compare<uint32_t>(ctx.r11.u32, 0, ctx.xer);
  // 0x823C5EB8: beq cr6, 0x823c5f64
  if (ctx.cr6.eq) goto loc_823C5F64;

  // 0x823C5EBC: mr r30, r25
  ctx.r30.u64 = ctx.r25.u64;

loc_823C5EC0:
  // 0x823C5EC0: lwz r31, 0(r30)
  ctx.r31.u64 = REX_LOAD_U32(ctx.r30.u32 + 0);
  // 0x823C5EC4: lwz r10, 8(r31)
  ctx.r10.u64 = REX_LOAD_U32(ctx.r31.u32 + 8);
  // 0x823C5EC8: cmplwi cr6, r10, 0
  ctx.cr6.compare<uint32_t>(ctx.r10.u32, 0, ctx.xer);
  // 0x823C5ECC: beq cr6, 0x823c5f0c
  if (ctx.cr6.eq) goto loc_823C5F0C;

  // 0x823C5ED0: rotlwi r3, r10, 0
  ctx.r3.u64 = __builtin_rotateleft32(ctx.r10.u32, 0);
  // 0x823C5ED4: lbz r21, 40(r3) (0x28)
  ctx.r21.u64 = REX_LOAD_U8(ctx.r3.u32 + 40);
  // 0x823C5ED8: bl 0x823c9948
  ctx.lr = 0x823C5EDC;
  sub_823C9948(ctx, base);

  // 0x823C5EDC: lwz r10, 8(r31)
  ctx.r10.u64 = REX_LOAD_U32(ctx.r31.u32 + 8);
  // 0x823C5EE0: mr r11, r21
  ctx.r11.u64 = ctx.r21.u64;
  // 0x823C5EE4: lbz r9, 40(r10) (0x28)
  ctx.r9.u64 = REX_LOAD_U8(ctx.r10.u32 + 40);
  // 0x823C5EE8: cmplw cr6, r21, r9
  ctx.cr6.compare<uint32_t>(ctx.r21.u32, ctx.r9.u32, ctx.xer);
  // 0x823C5EEC: beq cr6, 0x823c5f08
  if (ctx.cr6.eq) goto loc_823C5F08;

  // 0x823C5EF0: lwz r11, 13200(r22) (0x3390)
  ctx.r11.u64 = REX_LOAD_U32(ctx.r22.u32 + 13200);
  // 0x823C5EF4: cmplwi cr6, r11, 0
  ctx.cr6.compare<uint32_t>(ctx.r11.u32, 0, ctx.xer);
  // 0x823C5EF8: beq cr6, 0x823c5f08
  if (ctx.cr6.eq) goto loc_823C5F08;

  // 0x823C5EFC: rotlwi r11, r11, 0
  ctx.r11.u64 = __builtin_rotateleft32(ctx.r11.u32, 0);
  // 0x823C5F00: mtctr r11
  ctx.ctr.u64 = ctx.r11.u64;
  // 0x823C5F04: bctrl
  ctx.lr = 0x823C5F08;
  REX_CALL_INDIRECT_FUNC(ctx.ctr.u32);

loc_823C5F08:
  // 0x823C5F08: lwz r11, 28512(r28)
  ctx.r11.u64 = REX_LOAD_U32(ctx.r28.u32 + 28512);

loc_823C5F0C:
  // 0x823C5F0C: lwz r10, 8(r31)
  ctx.r10.u64 = REX_LOAD_U32(ctx.r31.u32 + 8);
  // 0x823C5F10: cmplwi cr6, r10, 0
  ctx.cr6.compare<uint32_t>(ctx.r10.u32, 0, ctx.xer);
  // 0x823C5F14: beq cr6, 0x823c5f20
  if (ctx.cr6.eq) goto loc_823C5F20;

  // 0x823C5F18: lbz r10, 42(r10) (0x2A)
  ctx.r10.u64 = REX_LOAD_U8(ctx.r10.u32 + 42);
  // 0x823C5F1C: b 0x823c5f24
  goto loc_823C5F24;

loc_823C5F20:
  // 0x823C5F20: li r10, 0
  ctx.r10.s64 = 0;

loc_823C5F24:
  // 0x823C5F24: clrlwi r10, r10, 24
  ctx.r10.u64 = ctx.r10.u32 & 0xFF;
  // 0x823C5F28: cmplwi cr6, r10, 0
  ctx.cr6.compare<uint32_t>(ctx.r10.u32, 0, ctx.xer);
  // 0x823C5F2C: bne cr6, 0x823c5f54
  if (!ctx.cr6.eq) goto loc_823C5F54;

  // 0x823C5F30: mr r3, r31
  ctx.r3.u64 = ctx.r31.u64;
  // 0x823C5F34: bl 0x823c9e40
  ctx.lr = 0x823C5F38;
  sub_823C9E40(ctx, base);

  // 0x823C5F38: lwz r11, 28512(r28)
  ctx.r11.u64 = REX_LOAD_U32(ctx.r28.u32 + 28512);
  // 0x823C5F3C: addi r11, r11, -1
  ctx.r11.s64 = ctx.r11.s64 - 1;
  // 0x823C5F40: rlwinm r10, r11, 2, 0, 29
  ctx.r10.u64 = __builtin_rotateleft64(ctx.r11.u32 | (ctx.r11.u64 << 32), 2) & 0xFFFFFFFC;
  // 0x823C5F44: stw r11, 28512(r28)
  REX_STORE_U32(ctx.r28.u32 + 28512, ctx.r11.u32);
  // 0x823C5F48: lwzx r9, r10, r25
  ctx.r9.u64 = REX_LOAD_U32(ctx.r10.u32 + ctx.r25.u32);
  // 0x823C5F4C: stw r9, 0(r30)
  REX_STORE_U32(ctx.r30.u32 + 0, ctx.r9.u32);
  // 0x823C5F50: b 0x823c5f5c
  goto loc_823C5F5C;

loc_823C5F54:
  // 0x823C5F54: addi r29, r29, 1
  ctx.r29.s64 = ctx.r29.s64 + 1;
  // 0x823C5F58: addi r30, r30, 4
  ctx.r30.s64 = ctx.r30.s64 + 4;

loc_823C5F5C:
  // 0x823C5F5C: cmplw cr6, r29, r11
  ctx.cr6.compare<uint32_t>(ctx.r29.u32, ctx.r11.u32, ctx.xer);
  // 0x823C5F60: blt cr6, 0x823c5ec0
  if (ctx.cr6.lt) goto loc_823C5EC0;

loc_823C5F64:
  // 0x823C5F64: bl 0x824dbe58
  ctx.lr = 0x823C5F68;
  sub_824DBE58(ctx, base);
  {
    static uint32_t s_frame_count = 0;
    if (++s_frame_count <= 20 || s_frame_count % 60 == 0) {
      REXLOG_INFO("neverdead: Frame #{} sub_824DBE58 returned r3=0x{:016X} (quit={}, state={})",
                  s_frame_count, ctx.r3.u64, *p_quit, *p_state);
    }
  }

  // 0x823C5F68: clrlwi r11, r3, 24
  ctx.r11.u64 = ctx.r3.u32 & 0xFF;
  // 0x823C5F6C: cmplwi cr6, r11, 0
  ctx.cr6.compare<uint32_t>(ctx.r11.u32, 0, ctx.xer);
  // 0x823C5F70: bne cr6, 0x823c5de4
  if (!ctx.cr6.eq) goto loc_823C5DE4;

loc_823C5F74:
  REXLOG_INFO("neverdead: Asura engine shutting down (0x823C5F74)...");
  // 0x823C5F74: lis r11, -32077
  ctx.r11.s64 = -2102198272;
  // 0x823C5F78: lwz r3, 15944(r11) (0x3E48)
  ctx.r3.u64 = REX_LOAD_U32(ctx.r11.u32 + 15944);
  // 0x823C5F7C: bl 0x826c6c10
  ctx.lr = 0x823C5F80;
  sub_826C6C10(ctx, base);
  // 0x823C5F80: bl 0x824da078
  ctx.lr = 0x823C5F84;
  sub_824DA078(ctx, base);
  // 0x823C5F84: bl 0x8220b188
  ctx.lr = 0x823C5F88;
  sub_8220B188(ctx, base);
  // 0x823C5F88: li r3, 0
  ctx.r3.s64 = 0;
  // 0x823C5F8C: addi r1, r1, 176 (0xB0)
  ctx.r1.s64 = ctx.r1.s64 + 176;
  // 0x823C5F90: b 0x8278bd7c
  //
  // CORRECCION (PATCH neverdead-crt-gpr-helpers): 0x8278BD7C NO es un fragmento
  // de cola de epilogo compartida; es `__restgprlr_21`
  // (ld r21..r31 desde -96..-16(r1) ; lwz r12,-8(r1) ; mtlr r12 ; blr), y es
  // exactamente la cola que el invitado necesita aqui: sub_823C5DAC tiene marco
  // 0xB0 y salva r21..r31, asi que `addi r1,+0xB0` seguido de `b 0x8278BD7C`
  // restaura el marco y devuelve por el LR del llamante.
  //
  // La version anterior encadenaba sub_8278BD7C/80/84/88 a mano porque creia
  // que eran fragmentos de una cola; con el cuerpo canonico eso restauraria
  // cuatro juegos de registros distintos y volveria por un LR equivocado.
  sub_8278BD7C(ctx, base);
  return;
}
