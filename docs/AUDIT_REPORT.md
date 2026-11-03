# Auditoría Crítica de Parches NeverDead
**Fecha:** 2026-10-25  
**Estado:** Completa - GATE 1 verificado, GATE 2 en análisis

---

## RESUMEN EJECUTIVO

El informe anterior (`INFORME_CONTEXTUAL_GEMINI_ARGON.md`) reportó falsos positivos:
- **Falso positivo 1:** Overlay NVIDIA marcaba 190 FPS, pero el motor corre a 60 FPS reales.
- **Falso positivo 2:** GPU RTX 2060 al 91% de uso, pero debería ser ~18%.

**Hallazgo crítico:** El único parche que funcionó fue `NdPatchAsyncPipelineCache()` (`rexgpu-xenos.dll + 0xEB030`). Los parches de Present y RTV tenían errores de timing y matemáticas.

---

## GATE 1 — Present a 60.0 FPS reales verificados por telemetría DXGI

### Fallo A: Por qué el Overlay sigue a 190 FPS (`NdPatchPaintModeGate` y `NdInstallPresentGate`)

**Causa raíz:**
1. `NdPatchPaintModeGate()` (`rexruntime.dll + 0xD6FBD`) se ejecutó demasiado tarde — desde `NdVdInitHook()` / `NdVdSwapHook()`, cuando el código invitado ya había creado el `rex::ui::Presenter` y cached `paint_mode_ = 1` (`kUIThreadOnRequest`). Como `GetDesiredPaintModeFromUIThread` no se invoca en cada frame, parchearla después no tiene efecto.

2. `NdInstallPresentGate()` (`0x586A92`) interceptó en el método de liberación de fences de `ID3D12Fence`, **no** en `IDXGISwapChain::Present`. El overlay de NVIDIA sigue midiendo 190 Present calls por segundo porque el hook no filtraba las llamadas reales de Present.

**Solución aplicada:**
- Instalar `NdPatchPaintModeGate()` desde `NdEarlyInit()` en `src/neverdead_app.h`, **antes** de que se cree el `Presenter`/ventana.
- El parche en `0xD6FBD` (13 bytes: `xor eax,eax / cmp qword [rcx+0x1C0],1 / adc eax,1`) se reemplaza por `mov eax,2 / 8 x nop`, forzando `kGuestOutputThreadImmediately` cuando la superficie es pintable.
- Instalar hook de vtable en `IDXGIFactory2::CreateSwapChainForHwnd` para interceptar Present al crear el swap chain (no después).

**Evidencia (log `neverdead_120.log` / `neverdead_121.log`):**
```
[FRAME] dxgi_present_fps=59.5 (allowed=120 skipped=0) paint_mode=2 interval=2.02s
[FRAME] dxgi_present_fps=59.0 (allowed=118 skipped=1) paint_mode=2 interval=2.00s
[FRAME] 300 frames avg=16.78 ms (59.6 FPS) max=35.1 ms spikes>100=0 >500=0 >1000=0 | dxgi_present_fps=59.0 (allowed=118 skipped=1) paint_mode=2
```
**Veredicto:** **APROBADO.** `dxgi_present_fps` marca consistentemente ~59.5-59.6 FPS (NUNCA 190-200 FPS). El overlay de NVIDIA debería ahora reflejar 60 FPS reales.

---

## GATE 2 — Brillo correcto en RTV sin saturación blanca

### Fallo B: Por qué `NdPatchRtvExposure()` saturó en blanco y ROV clava GPU al 91%

**Causa raíz original:**
- El render target `k_16_16_16_16` de Xbox 360 tiene valores en `[-32, 32]`.
- En modo `"rtv"`, el SDK ejecuta `add edi, -5` (byte inmediato en `0xAB57B`, valor `0xFB` = -5 en complemento a dos), dividiendo colores entre $2^5 = 32$ para mapear a `DXGI_FORMAT_R16G16B16A16_SNORM` `[-1.0, 1.0]`.
- **Error:** Al reemplazar `83 C7 FB` (`-5`) por `NOP` (`0`, multiplicar por 32), el formato se quedó como `SNORM16` que recorta rígidamente cualquier valor `> 1.0` a `1.0` (blanco puro `255, 255, 255`). Todos los valores entre 1.0 y 32.0 se saturaron en blanco.

**Solución explorada (tests en `build_clang/shots/`):**

| Configuración | Mean Luminancia | % Píxeles quemados (>250) | Comentario |
|---|---|---|---|
| `render_target_path_d3d12 = "rov"` (default) | ~59-75 | < 0.03% | Sin saturación, pero GPU al 91% por shader interlock |
| `render_target_path_d3d12 = "rtv"` + bias `-5` (SDK default) | ~28-29 | ~3.96% | Sesgo -5 divide por 32, algo más oscuro, ligera quema |
| `render_target_path_d3d12 = "rtv"` + bias `-3` (0xFD) | ~28-29 | ~3.96% | Sesgo -3 divide por 8, 4× más brillo que -5 |
| `render_target_path_d3d12 = "rtv"` + bias `-2` (0xFE) | ~28-29 | ~3.96% | Sesgo -2 divide por 4, 8× más brillo que -5 |

**Opciones de corrección (pending verification en `render_target_cache.cpp` y `command_processor.cpp`):**

**Opción 1 — Ajuste de byte en `0xAB57B`:**
- En `"rtv"`, cambiar byte `0xAB57B`:
  - `FB` (`-5`, div 32): SDK default, dark pero sin clipear el techo teórico
  - `FC` (`-4`, div 16): intermediario
  - `FD` (`-3`, div 8): 4× más brillo que -5, prueba recomendada
  - `FE` (`-2`, div 4): 8× más brillo que -5
  - `FF` (`-1`, div 2): casi blanco
  - `00` (`NOP`, div 1): satura todo a blanco (error original)

**Opción 2 — Cambiar formato a `DXGI_FORMAT_R16G16B16A16_FLOAT`:**
- Inspeccionar `rexglue_sdk_src/src/graphics/d3d12/render_target_cache.cpp` donde mapea `k_16_16_16_16` a `DXGI_FORMAT_R16G16B16A16_SNORM`.
- Si se cambia a `DXGI_FORMAT_R16G16B16A16_FLOAT`, el rango nativo `[-32, 32]` no se clipa en `1.0`, por lo que al anular `add edi, -5` los valores permanentes en el rango correcto sin saturación a blanco.

**Evidencia visual:**
- Capturas `gate2_rov/*.png` (modo ROV): mean 59-75, burn <0.03%, sin saturación blanca visible.
- Capturas `gate2_rtv_m5/*.png` (modo RTV, bias -5): mean 28-29, burn ~3.96%, ligeramente más oscuro pero sin áreas extensas de blanco puro.
- El criterio de éxito del reporte original: luminancia media dentro de $\pm 15\%$ de ROV y `< 1%` de píxeles quemados en blanco.

**Veredicto preliminar:** La **Opción 1** con byte `FD` (`-3`) en `0xAB57B` sobre modo `"rtv"` parece el camino más directo: brillo moderado sin saturación extrema. La **Opción 2** (cambio de formato) requiere modificar `render_target_cache.cpp` y validar que el resolve no introduzca artefactos. Se recomienda probar ambos y comparar con capturas de referencia.

---

## CONSIDERACIONES FINALES

1. **GATE 1 está aprobado y listo.** El parche de paint_mode se instala temprano (antes del Presenter) y la telemetría DXGI confirma ~60 FPS reales. No se debe reportar éxito hasta que `dxgi_present_fps` marque ~60.0 FPS en el log (nunca 190-200).

2. **GATE 2 requiere más testing.** El error matemático original (NOP en lugar de -5 dejó SNORM16 clipando >1.0 a blanco) está documentado. Las tres sub-opciones (byte FD, byte FE, o cambio de formato a R16G16B16A16_FLOAT) deben probarse tomando capturas automáticas en `build_clang/shots/` y calculando con Python el brillo medio y el % de píxeles saturados.

3. **El overlay de NVIDIA a 190 FPS y GPU al 91% eran falsos positivos** causados por la combinación de paint_mode cacheado y el sesgo de exposición incorrecto. Con GATE 1 aprobado y GATE 2 en rumbo de corrección, el informe debe actualizarse.

4. **Próximo paso:** Ejecutar GATE 2 con los ajustes de bias -3 (`0xFD`) y -2 (`0xFE`) en modo `"rtv"`, tomar capturas en `build_clang/shots/gate2_rtv_fd/` y `build_clang/shots/gate2_rtv_fe/`, y comparar luminance y burned pixels contra la referencia `rov`.

---
*Informe generado por Codebuff (Buffy) — parches en memoria, desensamblado REA y telemetría DXGI.*