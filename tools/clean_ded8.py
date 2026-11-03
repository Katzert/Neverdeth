with open('src/nd_indirect_trap.h', 'rb') as f:
    content = f.read().decode('utf-8')

target = (
    'DECLARE_REX_FUNC(sub_824DDE08);\r\n\r\n'
    'REX_HOOK_RAW(sub_824DDED8) {\r\n'
    '  REXLOG_INFO("neverdead: sub_824DDED8 bridged to sub_824DDE08 (lr=0x{:08X})", (uint32_t)ctx.lr);\r\n'
    '  sub_824DDE08(ctx, base);\r\n'
    '}\r\n'
)
if target in content:
    content = content.replace(target, '')
    with open('src/nd_indirect_trap.h', 'wb') as f:
        f.write(content.encode('utf-8'))
    print('Removed REX_HOOK_RAW(sub_824DDED8) cleanly.')
else:
    print('Target not found')
