// Host override for xboxkrnl#0x155 (KeTlsSetValue) at inline thunk sub_827BD8F4.
//
// The recompiled body of sub_827BD8F4 contains raw XEX import thunk bytes
// (`mtctr r11; bctr`) without r11 initialized by callers, causing an indirect
// jump to an invalid address. REX_HOOK_RAW defines a strong symbol overriding
// the weak recompiled alias and delegates directly to __imp__KeTlsSetValue.

#pragma once

#include <cstdint>
#include <cstring>

#include <rex/hook.h>
#include <rex/logging.h>

#include "nd_import_diag.h"

namespace neverdead::fix {

inline bool g_ke_tls_announced = false;

}  // namespace neverdead::fix

REX_EXTERN(__imp__KeTlsSetValue);

REX_HOOK_RAW(sub_827BD8F4) {
  if (!neverdead::fix::g_ke_tls_announced) {
    neverdead::fix::g_ke_tls_announced = true;
    REXLOG_INFO("neverdead: KeTlsSetValue override active (xboxkrnl#0x155)");
  }
  if (neverdead::fix::NdiDiagEnabled()) {
    const uint32_t index = ctx.r3.u32;
    const uint32_t value = ctx.r4.u32;
    REXLOG_INFO(
        "neverdead: KeTlsSetValue ctx r1=0x{:08X} r2=0x{:08X} r13=0x{:08X} "
        "lr=0x{:08X} index=0x{:08X} value=0x{:08X}",
        (uint32_t)ctx.r1.u32, (uint32_t)ctx.r2.u32, (uint32_t)ctx.r13.u32,
        (uint32_t)ctx.lr, index, value);
  }
  __imp__KeTlsSetValue(ctx, base);
}
