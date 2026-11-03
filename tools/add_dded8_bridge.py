import os

def update_indirect_trap():
    path = 'src/nd_indirect_trap.h'
    with open(path, 'rb') as f:
        content = f.read().decode('utf-8')

    # Add DECLARE_REX_FUNC(sub_824DDE08) and REX_HOOK_RAW(sub_824DDED8)
    decl_target = 'DECLARE_REX_FUNC(sub_8278EB50);\r\n'
    decl_repl = (
        'DECLARE_REX_FUNC(sub_8278EB50);\r\n'
        'DECLARE_REX_FUNC(sub_824DDE08);\r\n\r\n'
        'REX_HOOK_RAW(sub_824DDED8) {\r\n'
        '  REXLOG_INFO("neverdead: sub_824DDED8 bridged to sub_824DDE08 (lr=0x{:08X})", (uint32_t)ctx.lr);\r\n'
        '  sub_824DDE08(ctx, base);\r\n'
        '}\r\n'
    )
    if decl_target in content and 'sub_824DDED8' not in content:
        content = content.replace(decl_target, decl_repl, 1)
        print("nd_indirect_trap.h: sub_824DDED8 hook declared")

    # Add 0x824DDF90 handler
    f6_target = '      if (rex_trap_target_ == 0x824F6DB8u) {'
    f6_repl = (
        '      if (rex_trap_target_ == 0x824DDF90u) {                                     \\\r\n'
        '        REXLOG_INFO("neverdead: unregistered indirect call 0x824DDF90 lr=0x{:08X} (handled r3=1)", (uint32_t)ctx.lr); \\\r\n'
        '        ctx.r3.u64 = 1;                                                          \\\r\n'
        '        break;                                                                   \\\r\n'
        '      }                                                                          \\\r\n'
        '      if (rex_trap_target_ == 0x824F6DB8u) {'
    )
    if f6_target in content and '0x824DDF90u' not in content:
        content = content.replace(f6_target, f6_repl, 1)
        print("nd_indirect_trap.h: 0x824DDF90 safeguard restored")

    with open(path, 'wb') as f:
        f.write(content.encode('utf-8'))

if __name__ == '__main__':
    update_indirect_trap()
