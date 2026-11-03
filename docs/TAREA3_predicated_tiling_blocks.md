# Tarea 3 — Bloques extraídos de `generated/neverdead_default/`

> **`generated/` NO fue modificado.** Esto es solo lectura, tal como pediste.

## Hallazgo antes de los bloques

Los tres símbolos **no son Predicated Tiling**. La evidencia:

| Símbolo | Qué es realmente | Prueba |
|---|---|---|
| `sub_826B8620` | **Rutina de present/flip del frame** (1130 líneas) | llama `__imp__VdSwap` (línea 30949), `__imp__VdPersistDisplay` (31401) y `__imp__VdGetSystemCommandBuffer` (30772) |
| `sub_826B75C0` | **Contador de anillo módulo 10** (33 líneas) | `sub_826B75A8` = `(x+1) % 10`; no toca geometría ni render targets |
| `sub_826B7F28` | **Constructor de paquete de comandos Xenos** + `_snprintf` de depuración | escribe words con tags `0x30000000`/`0x0C000000` en `r1+96` y copia structs |

El hook de `nd_hooks.cpp` (`0x826B89A0 -> bl VdSwap`) cae **dentro** de `sub_826B8620`
(`0x826B89A0 - 0x826B8620 = 0x380`), lo que confirma que es el camino de presentación.

**Consecuencia:** el Predicated Tiling no vive en el código del invitado, sino en el
plugin de GPU del host (`rexgpu-xenos.dll`) y se controla desde `neverdead.toml`:
`d3d12_tiled_shared_memory = true` + `render_target_path_d3d12 = "rov"`.
Bypassear `sub_826B8620` no reduciría el coste de GPU; rompería la presentación.

---

## Bloque 1 — `sub_826B8620` (neverdead_recomp.68.cpp, líneas 30437-30520)

```cpp
DEFINE_REX_FUNC(sub_826B8620) {
	REX_FUNC_PROLOGUE();
	PPCRegister temp{};
	uint32_t ea{};
	// mflr r12
	ctx.r12.u64 = ctx.lr;
	// bl 0x8278cd24
	ctx.lr = 0x826B8628;
	__savegprlr_19(ctx, base);
	// stwu r1,-480(r1)
	ea = -480 + ctx.r1.u32;
	REX_STORE_U32(ea, ctx.r1.u32);
	ctx.r1.u32 = ea;
	// mr r31,r3
	ctx.r31.u64 = ctx.r3.u64;
	// mr r28,r4
	ctx.r28.u64 = ctx.r4.u64;
	// bl 0x827bfa64
	ctx.lr = 0x826B8638;
	__imp__KeGetCurrentProcessType(ctx, base);
	// addi r10,r3,-2
	ctx.r10.s64 = ctx.r3.s64 + -2;
	// lwz r11,13992(r31)
	ctx.r11.u64 = REX_LOAD_U32(ctx.r31.u32 + 13992);
	// addic r9,r10,-1
	ctx.xer.ca = ctx.r10.u32 > 0;
	ctx.r9.s64 = ctx.r10.s64 + -1;
	// rlwinm. r11,r11,0,27,27
	ctx.r11.u64 = __builtin_rotateleft64(ctx.r11.u32 | (ctx.r11.u64 << 32), 0) & 0x10;
	ctx.cr0.compare<int32_t>(ctx.r11.s32, 0, ctx.xer);
	// subfe r20,r9,r10
	temp.u8 = (~ctx.r9.u32 + ctx.r10.u32 < ~ctx.r9.u32) | (~ctx.r9.u32 + ctx.r10.u32 + ctx.xer.ca < ctx.xer.ca);
	ctx.r20.u64 = ~ctx.r9.u64 + ctx.r10.u64 + ctx.xer.ca;
	ctx.xer.ca = temp.u8;
	// beq 0x826b8668
	if (ctx.cr0.eq) goto loc_826B8668;
	// bl 0x826b75c0
	ctx.lr = 0x826B8654;
	sub_826B75C0(ctx, base);
	// cmpwi r3,0
	ctx.cr0.compare<int32_t>(ctx.r3.s32, 0, ctx.xer);
	// beq 0x826b8668
	if (ctx.cr0.eq) goto loc_826B8668;
	// mr r4,r28
	ctx.r4.u64 = ctx.r28.u64;
	// mr r3,r31
	ctx.r3.u64 = ctx.r31.u64;
	// bl 0x826b7f28
	ctx.lr = 0x826B8668;
	sub_826B7F28(ctx, base);
loc_826B8668:
	// lwz r11,16968(r31)
	ctx.r11.u64 = REX_LOAD_U32(ctx.r31.u32 + 16968);
	// addi r4,r28,28
	ctx.r4.s64 = ctx.r28.s64 + 28;
	// addi r3,r1,128
	ctx.r3.s64 = ctx.r1.s64 + 128;
	// addi r11,r11,1
	ctx.r11.s64 = ctx.r11.s64 + 1;
	// li r5,24
	ctx.r5.s64 = 24;
	// stw r11,16968(r31)
	REX_STORE_U32(ctx.r31.u32 + 16968, ctx.r11.u32);
	// bl 0x8278ce60
	ctx.lr = 0x826B8684;
	sub_8278CE60(ctx, base);
	// lwz r10,14012(r31)
	ctx.r10.u64 = REX_LOAD_U32(ctx.r31.u32 + 14012);
	// li r19,3
	ctx.r19.s64 = 3;
	// lwz r30,132(r1)
	ctx.r30.u64 = REX_LOAD_U32(ctx.r1.u32 + 132);
	// clrlwi r11,r30,26
	ctx.r11.u64 = ctx.r30.u32 & 0x3F;
	// cmplwi cr6,r11,50
	ctx.cr6.compare<uint32_t>(ctx.r11.u32, 50, ctx.xer);
	// bne cr6,0x826b86a4
	if (!ctx.cr6.eq) goto loc_826B86A4;
	// rlwimi r30,r19,1,26,31
	ctx.r30.u64 = (__builtin_rotateleft64(ctx.r19.u32 | (ctx.r19.u64 << 32), 1) & 0x3F) | (ctx.r30.u64 & 0xFFFFFFFFFFFFFFC0);
	// b 0x826b86b4
	goto loc_826B86B4;
loc_826B86A4:
	// cmplwi cr6,r11,7
```

### Punto exacto del `VdSwap` dentro de la función (línea 30940-30955)

```cpp
	// addi r4,r1,128
	ctx.r4.s64 = ctx.r1.s64 + 128;
	// addi r5,r11,8
	ctx.r5.s64 = ctx.r11.s64 + 8;
	// addi r3,r3,4
	ctx.r3.s64 = ctx.r3.s64 + 4;
	// bl 0x827bffd4
	NdVdSwapHook();
	ctx.lr = 0x826B89A4;
	__imp__VdSwap(ctx, base);
	// addi r11,r30,256
	ctx.r11.s64 = ctx.r30.s64 + 256;
	// stw r11,48(r31)
	REX_STORE_U32(ctx.r31.u32 + 48, ctx.r11.u32);
loc_826B89AC:
	// lwz r11,212(r1)
```

---

## Bloque 2 — `sub_826B75C0` completo (neverdead_recomp.16.cpp, líneas 25349-25381)

```cpp
DEFINE_REX_FUNC(sub_826B75C0) {
	REX_FUNC_PROLOGUE();
	uint32_t ea{};
	// mflr r12
	ctx.r12.u64 = ctx.lr;
	// stw r12,-8(r1)
	REX_STORE_U32(ctx.r1.u32 + -8, ctx.r12.u32);
	// stwu r1,-96(r1)
	ea = -96 + ctx.r1.u32;
	REX_STORE_U32(ea, ctx.r1.u32);
	ctx.r1.u32 = ea;
	// lis r9,-32077
	ctx.r9.s64 = -2102198272;
	// lwz r3,19500(r9)
	ctx.r3.u64 = REX_LOAD_U32(ctx.r9.u32 + 19500);
	// bl 0x826b75a8
	ctx.lr = 0x826B75D8;
	sub_826B75A8(ctx, base);
	// lis r11,-32072
	ctx.r11.s64 = -2101870592;
	// stw r3,19500(r9)
	REX_STORE_U32(ctx.r9.u32 + 19500, ctx.r3.u32);
	// lwz r3,-29248(r11)
	ctx.r3.u64 = REX_LOAD_U32(ctx.r11.u32 + -29248);
	// addi r1,r1,96
	ctx.r1.s64 = ctx.r1.s64 + 96;
	// lwz r12,-8(r1)
	ctx.r12.u64 = REX_LOAD_U32(ctx.r1.u32 + -8);
	// mtlr r12
	ctx.lr = ctx.r12.u64;
	// blr 
	return;
}
```

### Su única dependencia: `sub_826B75A8` = `(r3 + 1) % 10` (neverdead_recomp.31.cpp, 25388-25402)

```cpp
DEFINE_REX_FUNC(sub_826B75A8) {
	REX_FUNC_PROLOGUE();
	// addi r11,r3,1
	ctx.r11.s64 = ctx.r3.s64 + 1;
	// li r10,10
	ctx.r10.s64 = 10;
	// divwu r10,r11,r10
	ctx.r10.u64 = uint32_t(ctx.r10.u32 ? ctx.r11.u32 / ctx.r10.u32 : 0);
	// mulli r10,r10,10
	ctx.r10.s64 = static_cast<int64_t>(ctx.r10.u64 * static_cast<uint64_t>(10));
	// subf r3,r10,r11
	ctx.r3.u64 = ctx.r11.u64 - ctx.r10.u64;
	// blr 
	return;
}
```

---

## Bloque 3 — `sub_826B7F28`, primeras 80 líneas (neverdead_recomp.53.cpp, 29599-29678)

```cpp
DEFINE_REX_FUNC(sub_826B7F28) {
	REX_FUNC_PROLOGUE();
	PPCRegister temp{};
	uint32_t ea{};
	// mflr r12
	ctx.r12.u64 = ctx.lr;
	// bl 0x8278cd1c
	ctx.lr = 0x826B7F30;
	__savegprlr_17(ctx, base);
	// stfd f30,-144(r1)
	ctx.fpscr.disableFlushMode();
	REX_STORE_U64(ctx.r1.u32 + -144, ctx.f30.u64);
	// stfd f31,-136(r1)
	REX_STORE_U64(ctx.r1.u32 + -136, ctx.f31.u64);
	// stwu r1,-656(r1)
	ea = -656 + ctx.r1.u32;
	REX_STORE_U32(ea, ctx.r1.u32);
	ctx.r1.u32 = ea;
	// lwz r9,36(r4)
	ctx.r9.u64 = REX_LOAD_U32(ctx.r4.u32 + 36);
	// lis r10,-32237
	ctx.r10.s64 = -2112684032;
	// mr r31,r3
	ctx.r31.u64 = ctx.r3.u64;
	// rlwinm r11,r9,19,19,31
	ctx.r11.u64 = __builtin_rotateleft64(ctx.r9.u32 | (ctx.r9.u64 << 32), 19) & 0x1FFF;
	// addi r30,r10,-30240
	ctx.r30.s64 = ctx.r10.s64 + -30240;
	// addi r11,r11,1
	ctx.r11.s64 = ctx.r11.s64 + 1;
	// clrlwi r10,r9,19
	ctx.r10.u64 = ctx.r9.u32 & 0x1FFF;
	// clrldi r9,r11,32
	ctx.r9.u64 = ctx.r11.u64 & 0xFFFFFFFF;
	// addi r23,r10,1
	ctx.r23.s64 = ctx.r10.s64 + 1;
	// std r9,80(r1)
	REX_STORE_U64(ctx.r1.u32 + 80, ctx.r9.u64);
	// lfd f0,80(r1)
	ctx.f0.u64 = REX_LOAD_U64(ctx.r1.u32 + 80);
	// fcfid f13,f0
	ctx.f13.f64 = double(ctx.f0.s64);
	// lfs f0,1768(r30)
	temp.u32 = REX_LOAD_U32(ctx.r30.u32 + 1768);
	ctx.f0.f64 = double(temp.f32);
	// frsp f12,f13
	ctx.f12.f64 = double(float(ctx.f13.f64));
	// lfs f13,32(r30)
	temp.u32 = REX_LOAD_U32(ctx.r30.u32 + 32);
	ctx.f13.f64 = double(temp.f32);
	// cmplwi cr6,r11,1470
	ctx.cr6.compare<uint32_t>(ctx.r11.u32, 1470, ctx.xer);
	// mr r29,r4
	ctx.r29.u64 = ctx.r4.u64;
	// fmadds f0,f12,f0,f13
	ctx.f0.f64 = double(float(std::fma(ctx.f12.f64, ctx.f0.f64, ctx.f13.f64)));
	// fctidz f0,f0
	ctx.f0.s64 = std::isnan(ctx.f0.f64) ? int64_t(0x8000000000000000ULL) : (ctx.f0.f64 > double(LLONG_MAX)) ? LLONG_MAX : simde_mm_cvttsd_si64(simde_mm_load_sd(&ctx.f0.f64));
	// stfd f0,80(r1)
	REX_STORE_U64(ctx.r1.u32 + 80, ctx.f0.u64);
	// lwz r10,84(r1)
	ctx.r10.u64 = REX_LOAD_U32(ctx.r1.u32 + 84);
	// rlwinm r28,r10,1,0,30
	ctx.r28.u64 = __builtin_rotateleft64(ctx.r10.u32 | (ctx.r10.u64 << 32), 1) & 0xFFFFFFFE;
	// subf r11,r28,r11
	ctx.r11.u64 = ctx.r11.u64 - ctx.r28.u64;
	// rlwinm r24,r11,31,1,31
	ctx.r24.u64 = __builtin_rotateleft64(ctx.r11.u32 | (ctx.r11.u64 << 32), 31) & 0x7FFFFFFF;
	// bge cr6,0x826b7fa8
	if (!ctx.cr6.lt) goto loc_826B7FA8;
	// addi r24,r24,-3
	ctx.r24.s64 = ctx.r24.s64 + -3;
	// addi r28,r28,6
	ctx.r28.s64 = ctx.r28.s64 + 6;
loc_826B7FA8:
	// lwz r11,11024(r31)
	ctx.r11.u64 = REX_LOAD_U32(ctx.r31.u32 + 11024);
	// lwz r11,96(r11)
	ctx.r11.u64 = REX_LOAD_U32(ctx.r11.u32 + 96);
	// cmplwi cr6,r11,0
```

### Contexto inmediato: el `_snprintf` de depuración y el empaquetado del comando

```cpp
	ctx.cr6.compare<uint32_t>(ctx.r11.u32, 0, ctx.xer);
	// beq cr6,0x826b7fe4
	if (ctx.cr6.eq) goto loc_826B7FE4;
	// lis r11,-32233
	ctx.r11.s64 = -2112421888;
	// add r9,r24,r28
	ctx.r9.u64 = ctx.r24.u64 + ctx.r28.u64;
	// addi r5,r11,-14688
	ctx.r5.s64 = ctx.r11.s64 + -14688;
	// mr r8,r23
	ctx.r8.u64 = ctx.r23.u64;
	// mr r7,r24
	ctx.r7.u64 = ctx.r24.u64;
	// li r6,0
	ctx.r6.s64 = 0;
	// li r4,256
	ctx.r4.s64 = 256;
	// addi r3,r1,256
	ctx.r3.s64 = ctx.r1.s64 + 256;
	// bl 0x827bfb34
	ctx.lr = 0x826B7FDC;
	__imp___snprintf(ctx, base);
	// addi r3,r1,256
	ctx.r3.s64 = ctx.r1.s64 + 256;
	// bl 0x826d1468
	ctx.lr = 0x826B7FE4;
	sub_826D1468(ctx, base);
loc_826B7FE4:
	// lwz r11,11024(r31)
	ctx.r11.u64 = REX_LOAD_U32(ctx.r31.u32 + 11024);
	// lis r12,-9
	ctx.r12.s64 = -589824;
```
