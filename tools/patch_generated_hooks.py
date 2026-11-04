#!/usr/bin/env python3
"""Aplica los hooks requeridos sobre el codigo generado post-codegen."""
import os

def main():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    rec87 = os.path.join(root, "generated", "neverdead_default", "neverdead_recomp.87.cpp")
    rec68 = os.path.join(root, "generated", "neverdead_default", "neverdead_recomp.68.cpp")

    # 1. neverdead_recomp.87.cpp: boundary reset en sub_826B6B38
    with open(rec87, "r", encoding="utf-8") as f:
        c87 = f.read().replace("\r\n", "\n")

    t87 = "DEFINE_REX_FUNC(sub_826B6B38) {\n\tREX_FUNC_PROLOGUE();"
    r87 = 'extern "C" void NdResetXdkShadowStateOnBoundary();\nDEFINE_REX_FUNC(sub_826B6B38) {\n\tREX_FUNC_PROLOGUE();\n\tNdResetXdkShadowStateOnBoundary();'

    if t87 in c87 and "NdResetXdkShadowStateOnBoundary" not in c87:
        c87 = c87.replace(t87, r87, 1)
        with open(rec87, "w", encoding="utf-8", newline="\n") as f:
            f.write(c87)
        print("neverdead_recomp.87.cpp: parcheado con éxito")
    else:
        print("neverdead_recomp.87.cpp: ya presente o target no encontrado")

    # 1b. neverdead_recomp.39.cpp: auto-advance Start -> A -> A -> A en sub_826D0820
    rec39 = os.path.join(root, "generated", "neverdead_default", "neverdead_recomp.39.cpp")
    with open(rec39, "r", encoding="utf-8") as f:
        c39 = f.read().replace("\r\n", "\n")

    t39 = "DEFINE_REX_FUNC(sub_826D0820) {\n\tREX_FUNC_PROLOGUE();\n\t// mr r5,r4\n\tctx.r5.u64 = ctx.r4.u64;\n\t// li r4,1\n\tctx.r4.s64 = 1;\n\t// b 0x827bf7c4\n\t__imp__XamInputGetState(ctx, base);\n\treturn;"
    r39 = "void NdAutoAdvanceInput(PPCContext& ctx, uint8_t* base, uint32_t guest_state);\n\nDEFINE_REX_FUNC(sub_826D0820) {\n\tREX_FUNC_PROLOGUE();\n\tconst uint32_t guest_state = ctx.r4.u32;\n\t// mr r5,r4\n\tctx.r5.u64 = ctx.r4.u64;\n\t// li r4,1\n\tctx.r4.s64 = 1;\n\t// b 0x827bf7c4\n\t__imp__XamInputGetState(ctx, base);\n\tNdAutoAdvanceInput(ctx, base, guest_state);\n\treturn;"

    if t39 in c39 and "NdAutoAdvanceInput" not in c39:
        c39 = c39.replace(t39, r39, 1)
        with open(rec39, "w", encoding="utf-8", newline="\n") as f:
            f.write(c39)
        print("neverdead_recomp.39.cpp: parcheado con éxito")
    else:
        print("neverdead_recomp.39.cpp: ya presente o target no encontrado")

    # 1c. neverdead_recomp.98.cpp: NdOnDrawCall en sub_826B1F88 (DrawIndexedVertices)
    rec98 = os.path.join(root, "generated", "neverdead_default", "neverdead_recomp.98.cpp")
    with open(rec98, "r", encoding="utf-8") as f:
        c98 = f.read().replace("\r\n", "\n")

    t98 = "DEFINE_REX_FUNC(sub_826B1F88) {\n\tREX_FUNC_PROLOGUE();"
    r98 = 'extern "C" void NdOnDrawCall();\nDEFINE_REX_FUNC(sub_826B1F88) {\n\tREX_FUNC_PROLOGUE();\n\tNdOnDrawCall();'

    if t98 in c98 and "NdOnDrawCall" not in c98:
        c98 = c98.replace(t98, r98, 1)
        with open(rec98, "w", encoding="utf-8", newline="\n") as f:
            f.write(c98)
        print("neverdead_recomp.98.cpp: parcheado con éxito (NdOnDrawCall)")
    else:
        print("neverdead_recomp.98.cpp: ya presente o target no encontrado")

    # 1d. neverdead_recomp.67.cpp: NdShouldSkipLumaResolve en sub_823ED208 (piramide auto-exposicion)
    rec67 = os.path.join(root, "generated", "neverdead_default", "neverdead_recomp.67.cpp")
    with open(rec67, "r", encoding="utf-8") as f:
        c67 = f.read().replace("\r\n", "\n")

    t67 = "DEFINE_REX_FUNC(sub_823ED208) {\n\tREX_FUNC_PROLOGUE();"
    r67 = 'extern "C" bool NdShouldSkipLumaResolve();\nDEFINE_REX_FUNC(sub_823ED208) {\n\tREX_FUNC_PROLOGUE();\n\tif (NdShouldSkipLumaResolve()) return;'

    if t67 in c67 and "NdShouldSkipLumaResolve" not in c67:
        c67 = c67.replace(t67, r67, 1)
        with open(rec67, "w", encoding="utf-8", newline="\n") as f:
            f.write(c67)
        print("neverdead_recomp.67.cpp: parcheado con éxito (NdShouldSkipLumaResolve)")
    else:
        print("neverdead_recomp.67.cpp: ya presente o target no encontrado")

    # 2. neverdead_recomp.68.cpp: verificar NdVdSwapPostHook
    with open(rec68, "r", encoding="utf-8") as f:
        c68 = f.read().replace("\r\n", "\n")

    print(f"NdVdSwapPostHook en rec68: {'NdVdSwapPostHook' in c68}")
    for line in c68.splitlines():
        if "NdVdSwap" in line:
            print("   ", line.strip())

    return 0

if __name__ == "__main__":
    main()
