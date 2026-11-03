import os

def update_indirect_trap():
    path = 'src/nd_indirect_trap.h'
    with open(path, 'rb') as f:
        content = f.read().decode('utf-8')

    decl_target = 'DECLARE_REX_FUNC(sub_8278EB50);\r\nvoid sub_821E0FA0(PPCContext& ctx, uint8_t* base);'
    decl_repl = (
        'DECLARE_REX_FUNC(sub_8278EB50);\r\n'
        'DECLARE_REX_FUNC(sub_82252E20);\r\n'
        'DECLARE_REX_FUNC(sub_8278BD9C);\r\n'
        'void sub_821E0FA0(PPCContext& ctx, uint8_t* base);\r\n\r\n'
        'inline void neverdead_handler_824F6DB8(PPCContext& ctx, uint8_t* base) {\r\n'
        '  ctx.r3.s64 = ctx.r1.s64 + 0x90;\r\n'
        '  ctx.lr = 0x824F6DC0;\r\n'
        '  sub_82252E20(ctx, base);\r\n'
        '  ctx.r1.s64 += 0x100;\r\n'
        '  sub_8278BD9C(ctx, base);\r\n'
        '}'
    )
    if decl_target in content:
        content = content.replace(decl_target, decl_repl, 1)
        print("nd_indirect_trap.h: handler declared")

    stubs_target = (
        '      if (rex_trap_target_ == 0x824DDF90u) {                                     \\\r\n'
        '        REXLOG_INFO("neverdead: unregistered indirect call 0x824DDF90 lr=0x{:08X} (handled r3=1)", (uint32_t)ctx.lr); \\\r\n'
        '        ctx.r3.u64 = 1;                                                          \\\r\n'
        '        break;                                                                   \\\r\n'
        '      }                                                                          \\\r\n'
        '      if (rex_trap_target_ == 0x824DDEC8u) {                                     \\\r\n'
        '        REXLOG_INFO("neverdead: unregistered indirect call 0x824DDEC8 lr=0x{:08X} (handled r3=1)", (uint32_t)ctx.lr); \\\r\n'
        '        ctx.r3.u64 = 1;                                                          \\\r\n'
        '        break;                                                                   \\\r\n'
        '      }                                                                          \\\r\n'
        '      if (rex_trap_target_ == 0x824DDEB8u) {                                     \\\r\n'
        '        REXLOG_INFO("neverdead: unregistered indirect call 0x824DDEB8 lr=0x{:08X} (handled r3=1)", (uint32_t)ctx.lr); \\\r\n'
        '        ctx.r3.u64 = 1;                                                          \\\r\n'
        '        break;                                                                   \\\r\n'
        '      }                                                                          \\\r\n'
    )
    if stubs_target in content:
        content = content.replace(stubs_target, '', 1)
        print("nd_indirect_trap.h: fake stubs removed")

    f6_target = (
        '      if (rex_trap_target_ == 0x824F6DB8u) {                                     \\\r\n'
        '        REXLOG_INFO("neverdead: unregistered indirect call 0x824F6DB8 lr=0x{:08X} (handled r3=1)", (uint32_t)ctx.lr); \\\r\n'
        '        ctx.r3.u64 = 1;                                                          \\\r\n'
        '        break;                                                                   \\\r\n'
        '      }'
    )
    f6_repl = (
        '      if (rex_trap_target_ == 0x824F6DB8u) {                                     \\\r\n'
        '        REXLOG_INFO("neverdead: unregistered indirect call 0x824F6DB8 lr=0x{:08X} (stack-safe epilogue)", (uint32_t)ctx.lr); \\\r\n'
        '        neverdead_handler_824F6DB8(ctx, base);                                   \\\r\n'
        '        break;                                                                   \\\r\n'
        '      }'
    )
    if f6_target in content:
        content = content.replace(f6_target, f6_repl, 1)
        print("nd_indirect_trap.h: 0x824F6DB8 epilogue routed")

    with open(path, 'wb') as f:
        f.write(content.encode('utf-8'))

def update_import_diag():
    path = 'src/nd_import_diag.h'
    with open(path, 'rb') as f:
        content = f.read().decode('utf-8')

    # Replace ND_IMPORT_REAL for sub_827BE064 (VdSetGraphicsInterruptCallback)
    old_cb = 'ND_IMPORT_REAL(sub_827BE064, 0x827BE064u, 0x1D5, VdSetGraphicsInterruptCallback)'
    new_cb = (
        'REX_EXTERN(__imp__VdSetGraphicsInterruptCallback);\r\n'
        'REX_HOOK_RAW(sub_827BE064) {\r\n'
        '  REXLOG_INFO("neverdead: IMPORT-REAL va=0x827BE064 ord=0x1D5 VdSetGraphicsInterruptCallback lr=0x{:08X} pfnCallback=0x{:08X} userData=0x{:08X}",\r\n'
        '              (uint32_t)ctx.lr, ctx.r3.u32, ctx.r4.u32);\r\n'
        '  __imp__VdSetGraphicsInterruptCallback(ctx, base);\r\n'
        '}'
    )
    if old_cb in content:
        content = content.replace(old_cb, new_cb, 1)
        print("nd_import_diag.h: VdSetGraphicsInterruptCallback hooked with telemetry")

    # Replace ND_IMPORT_REAL for sub_827BE084 (VdInitializeEngines)
    old_init = 'ND_IMPORT_REAL(sub_827BE084, 0x827BE084u, 0x1C2, VdInitializeEngines)'
    new_init = (
        'REX_EXTERN(__imp__VdInitializeEngines);\r\n'
        'REX_HOOK_RAW(sub_827BE084) {\r\n'
        '  REXLOG_INFO("neverdead: IMPORT-REAL va=0x827BE084 ord=0x1C2 VdInitializeEngines CALLED! lr=0x{:08X}", (uint32_t)ctx.lr);\r\n'
        '  __imp__VdInitializeEngines(ctx, base);\r\n'
        '}'
    )
    if old_init in content:
        content = content.replace(old_init, new_init, 1)
        print("nd_import_diag.h: VdInitializeEngines hooked with telemetry")

    # Replace ND_IMPORT_REAL for sub_827BDFD4 (VdSwap)
    old_swap = 'ND_IMPORT_REAL(sub_827BDFD4, 0x827BDFD4u, 0x25B, VdSwap)'
    new_swap = (
        'REX_EXTERN(__imp__VdSwap);\r\n'
        'REX_HOOK_RAW(sub_827BDFD4) {\r\n'
        '  static uint32_t s_vdswap_count = 0;\r\n'
        '  if (++s_vdswap_count <= 25 || (s_vdswap_count % 60 == 0)) {\r\n'
        '    REXLOG_INFO("neverdead: VdSwap #{} frontBuffer=0x{:08X} (lr=0x{:08X})", s_vdswap_count, ctx.r3.u32, (uint32_t)ctx.lr);\r\n'
        '  }\r\n'
        '  __imp__VdSwap(ctx, base);\r\n'
        '}'
    )
    if old_swap in content:
        content = content.replace(old_swap, new_swap, 1)
        print("nd_import_diag.h: VdSwap hooked with telemetry")

    with open(path, 'wb') as f:
        f.write(content.encode('utf-8'))

if __name__ == '__main__':
    update_indirect_trap()
    update_import_diag()
