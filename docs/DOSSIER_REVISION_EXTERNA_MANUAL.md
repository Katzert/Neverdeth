# DOSSIER DE REVISIÓN EXTERNA MANUAL (CROSS-MODEL)
**Proyecto:** NeverDead PC (Xbox 360 Title ID `4B4E081F` recompilado a x64 nativo vía ReXGlue SDK v0.10.0 + D3D12)  
**Propósito:** Evaluación técnica adversaria independiente de fallos de renderizado, barreras de sincronización y emulación de eDRAM.

---

## 1. CONTRATO DE REVISIÓN (CRITERIOS DE ACEPTACIÓN)

Un cambio o parche en la capa de traducción de GPU (`rexgpu-xenos.dll` / `rexruntime.dll`) solo es válido si satisface:
1. **Fidelidad Visual Empírica:** La escena 3D (pasillo del hospital psiquiátrico) debe mostrar texturas difusas, iluminación, sombras y HUD sin sobreexposición (white-out) ni corrupción cromática (magenta).
2. **Integridad de Draw Calls:** Ningún draw call debe ser descartado silenciosamente (`IssueDraw` no debe abortar llamadas por falta de PSO).
3. **Consistencia de Sincronización:** Las barreras entre hilos de compilación de PSOs y el hilo de sumisión de comandos D3D12 (`ExecuteCommandLists`) no deben introducir condiciones de carrera ni accesos a memoria liberada/no inicializada.
4. **Cumplimiento de DXGI Flip Model:** Las llamadas a `Present()` en swapchains con `DXGI_SWAP_EFFECT_FLIP_DISCARD` deben mantener la cadencia y permitir el avance del índice del backbuffer.

---

## 2. EL PROBLEMA Y LA EVIDENCIA EMPÍRICA REAL

Al ejecutar el juego en su estado actual, se obtienen tres capturas de pantalla reales que demuestran un fallo visual catastrófico:

1. **Logo Rebellion (`rebellion_magenta_failure.png`)**:
   - Píxeles colapsados a magenta puro `RGB(252, 7, 249)` en un **$94.97\%$** del área de pantalla. El canal verde se pierde por completo.
2. **Pantalla de Carga del Psiquiátrico (`asylum_loading_burnt_white.png`)**:
   - Sobreexposición extrema en los márgenes y elementos de interfaz (**$69.05\%$** de píxeles quemados).
3. **Gameplay 3D en el Pasillo del Hospital (`gameplay_3d_hospital_whiteout.png`)**:
   - White-out cegador total (**$96.35\%$** de la pantalla en blanco absoluto `RGB(250, 250, 249)`). El modelo de Bryce, las paredes y el entorno 3D quedan invisibles tras un velo blanco.

---

## 3. LOS ARTEFACTOS Y PARCHES BAJO EXAMEN

### Artefacto A: Parche de "Pipeline Cache Asíncrono" (`rexgpu-xenos.dll + 0xEB030`)
En el archivo `src/nd_hooks.cpp`:
```cpp
// Offset en rexgpu-xenos.dll (PipelineCache::EndSubmission)
constexpr size_t kRva = 0xEB030;
// Original: 0F 84 81 00 00 00 (je 0xEB0B7)
// Parche:   E9 82 00 00 00 90 (jmp 0xEB0B7; nop)
```

**Contexto del Desensamblado en `0xEB030`:**
```x86asm
0x0EB022: mov rax, qword ptr [rsi + 0x4a0]  ; inicio cola compilacion
0x0EB029: cmp rax, qword ptr [rsi + 0x4a8]  ; fin cola compilacion
0x0EB030: 0F 84 81 00 00 00                 ; je 0xEB0B7 (salta si cola vacia)
0x0EB036: mov rcx, rsi
0x0EB039: call 0xead90                      ; drena cola de descriptores
0x0EB03E: lea rdi, [rsi + 0x3c8]            ; mutex
0x0EB045: mov rcx, rdi
0x0EB048: call MSVCP140.dll!_Mtx_lock
0x0EB06F: mov rcx, qword ptr [rsi + 0x488]  ; WaitHandle
0x0EB079: call ResetEvent
0x0EB07C: mov byte ptr [rsi + 0x490], 1     ; compilation_active = 1
0x0EB087: call MSVCP140.dll!_Mtx_unlock
0x0EB08C: lea rcx, [rsi + 0x418]            ; condition_variable
0x0EB093: call MSVCP140.dll!_Cnd_signal     ; despierta hilos workers
0x0EB098: mov rcx, qword ptr [rsi + 0x488]
0x0EB09F: movabs r8, 0x7fffffffffffffff     ; INFINITE
0x0EB0B2: jmp rexruntime.dll!Wait           ; BARRERA BLOQUEANTE
0x0EB0B7: add rsp, 0x30; ret
```

**Hallazgo del Auditor Interno:**
- Forzar el salto incondicional a `0xEB0B7` evita la espera bloqueante, pero omite `0x0EAD90` y `_Cnd_signal`.
- En `PipelineCache::ConfigurePipeline` (`0xEB4C0`), si el PSO aún no existe, retorna `nullptr`.
- En `D3D12CommandProcessor::IssueDraw`, al recibir `nullptr`, la llamada de dibujado se aborta silenciosamente.
- **Pregunta para el Revisor:** ¿Cómo debe desacoplarse la compilación de PSOs en D3D12 sin descartar draw calls ni causar tirones de 2.8 segundos? ¿Es viable usar un shader dummy de pase simple o fallback mientras el pipeline compila en segundo plano?

---

### Artefacto B: Parche de Formatos RTV a `FLOAT16` (`rexgpu-xenos.dll + 0xC0B2B`)
En `src/nd_hooks.cpp`:
```cpp
// GetColorDrawDXGIFormat (0xC0B2B y 0xC0EBD)
// k_16_16       : 0x25 (SNORM16) -> 0x22 (FLOAT16)
// k_16_16_16_16 : 0x0D (SNORM16) -> 0x0A (FLOAT16)
```

**Mecanismo del Fallo:**
1. Los shaders del juego NeverDead asumen el comportamiento de eDRAM de Xenos: esperan que los valores HDR estén polarizados con `color_exp_bias -= 5` ($\div 32$) para entrar en `[-1.0, 1.0]`.
2. Para recuperar el color real, los shaders aplican una multiplicación por 32 ($\times 32$) al muestrear las texturas intermedias resueltas.
3. Al parchear el Render Target a `FLOAT16`, el buffer almacena valores float directos sin dividir. Cuando los shaders aplican su $\times 32$, el resultado es $1.0 \times 32 = 32.0$ (blanco saturado al 100%).
4. En el logo de Rebellion, el Compute Shader de `DumpRenderTargets` (`0xBFDF0`) utiliza instrucciones `OpBFI` asumiendo empaquetado de bits en enteros `SNORM16`. Al procesar bits de float IEEE 754, colapsa el canal verde a 0 (magenta).
5. **Pregunta para el Revisor:** ¿Cuál es la estrategia correcta para emular eDRAM en RTV en este motor? ¿Compensar el sesgo de exponente en la tabla de constantes del shader, o revertir a la ruta nativa ROV?

---

### Artefacto C: La Ruta ROV (Rasterizer-Ordered Views) y su Cuello de Botella
En `neverdead.toml`:
```toml
render_target_path_d3d12 = "rov"
```
- **Ventaja:** Emula la memoria física eDRAM de 10 MB con un `ByteAddressBuffer` y `PixelShaderInterlock`. No requiere conversiones de formato ni `DumpRenderTargets`, eliminando por diseño la pantalla blanca y el logo magenta.
- **Problema empírico reportado:**
  - En la escena 3D real de NeverDead, ROV cae a **25.7 – 30.1 FPS** con la GPU al **91% de uso** constante (en una RTX 2060 a 720p).
  - La causa es la serialización extrema de primitivas concurrentes que escriben sobre los mismos píxeles en el render diferido de Asura.
- **Pregunta para el Revisor:** ¿Qué técnicas existen en D3D12/HLSL para mitigar la congestión de `PixelShaderInterlock` en emulación de eDRAM (e.g., depth-stencil pre-pass nativo, early-z desvinculado, o batching de accesos atómicos)?

---

### Artefacto D: Puerta de Presentación a 60 Hz (`rexruntime.dll + 0xFFC10`)
En `src/nd_hooks.cpp`:
```cpp
extern "C" bool NdCheckShouldPaintAndPresent() {
  const uint64_t swaps = g_vd_swap_calls.load(std::memory_order_acquire);
  if (swaps == 0) return true;
  const uint64_t last = g_last_presented_swap.load(std::memory_order_relaxed);
  if (swaps <= last) return false;
  return true;
}
```
**Hallazgo del Auditor:**
- Si Windows solicita un repintado (`WM_PAINT`, restaurar ventana, cambiar foco), esta función retorna `false` si no ha habido un nuevo swap del juego, dejando la ventana congelada o negra.
- En `DXGI_SWAP_EFFECT_FLIP_DISCARD`, omitir `Present()` tras despachar listas de comandos impide que el índice del swapchain avance, provocando tearing o corrupción de buffers.
- **Pregunta para el Revisor:** ¿Cómo debe estructurarse el gate de presentación para desacoplar el bucle de presentación del invitado de la tasa de refresco del compositor de Windows sin violar las invariantes de DXGI?

---

## 4. PLANTILLA DE RESPUESTA ESPERADA DEL REVISOR EXTERNO

Al enviar este expediente a un revisor externo, solicita que responda con la siguiente estructura:

```markdown
### 1. Veredicto sobre el Parche de PSO (0xEB030)
- ¿Es seguro mantener el salto o debe revertirse inmediatamente?
- Solución recomendada para el stutter sin dropear draws:

### 2. Veredicto sobre la Arquitectura de eDRAM (RTV vs ROV)
- ¿Es viable reparar RTV ajustando constantes/shaders, o ROV es la única vía anatómicamente correcta?
- En caso de ROV, ¿cómo mitigar el 91% de carga por PixelShaderInterlock?

### 3. Veredicto sobre el Present Gate (0xFFC10)
- Corrección para soportar WM_PAINT y preservar el ciclo del swapchain DXGI:

### 4. Próximos Pasos Prioritarios
- Orden secuencial de intervención técnica:
```
