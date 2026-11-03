# Parches del SDK ReXGlue

Copia el contenido de `rex/` sobre el `include/` del SDK **antes de compilar**:

```bat
xcopy /E /Y patches\sdk\rex <SDK>\include\rex\
```

## rex/ppc/context.h — elimina el stall de `ldmxcsr`

`FPSCRRegister::enableFlushMode()` / `disableFlushMode()` (y sus variantes
`Unconditional`) pasan a ser **solo-bandera**: actualizan el espejo `csr` sin escribir
`MXCSR`. `setcsr()` e `InitHost()` fijan `FTZ|DAZ` a ON.

Motivo: el codegen emite 34.754 llamadas FlushMode en `generated/neverdead_default/`
(una por instrucción VMX). Con el header original cada transición ejecuta `ldmxcsr`,
que serializa el pipeline de la CPU. Medido: 283 `ldmxcsr` por `.obj` → 0.

Backup del original: `context.h.rex_nd_orig` (junto al header del SDK).
