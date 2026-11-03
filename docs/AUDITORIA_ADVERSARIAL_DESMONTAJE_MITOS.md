# AUDITORÍA ADVERSARIAL: DESMONTAJE DE AFIRMACIONES FALSAS Y RUPTURA ARQUITECTÓNICA EN NEVERDEAD PC

**Fecha:** 25 de Octubre de 2026  
**Auditoría:** Adversarial Reality Auditor (Doubt-Driven Development)  
**Estado:** Dictamen Crítico Aceptado — Erradicación de triunfalismo infundado.

---

## 1. Veredicto General

Bajo el contrato estricto de ingeniería de software:
- **Ningún problema está resuelto** hasta que se verifique empíricamente en el juego real (combate 3D, movimiento, iluminación, HUD, frametime estable y fidelidad visual exacta).
- Las afirmaciones previas de "solución definitiva", "fin de stutters PSO" o "60 FPS continuos" fueron **deducciones teóricas no verificadas** y **mediciones inválidas tomadas en menús estáticos**.
- La realidad empírica demostrada por las capturas del usuario es que **NeverDead sigue siendo injugable**, con un colapso visual del 96.35% (pantalla blanca cegadora en el hospital) y 94.97% magenta en el logo de Rebellion.

---

## 2. Desglose Forense de las Afirmaciones Falsas

### A. Parche de Pipeline Cache (`0xEB030` en `rexgpu-xenos.dll`)
* **Afirmación previa:** *"Compilación asíncrona desbloqueada; fin de stutters PSO."*
* **Realidad forense:**
  1. El RVA `0xEB030` está dentro de `PipelineCache::EndSubmission`.
  2. Al reemplazar el salto condicional por un salto incondicional a `ret` (`E9 82 00 00 00 90`):
     - Se salta la llamada a `0x0EAD90` (drenaje de cola de descriptores).
     - Se salta `_Cnd_signal` (`0xEB093`), por lo que los hilos workers **no son notificados** oportunamente al finalizar la sumisión.
     - Se anula la barrera `Wait`.
  3. Al auditar `PipelineCache::ConfigurePipeline` (`0x0EB4C0`), cuando el pipeline aún no ha sido compilado, retorna `nullptr`.
  4. En `IssueDraw`, al recibir `nullptr`, **se aborta el draw call y no se emite nada a la lista de comandos**.
  5. **Conclusión:** No se eliminó el stutter por magia asíncrona; **el juego descarta silenciosamente geometrías, partículas y efectos de post-procesado** (Silent Draw Dropping), además de introducir una condición de carrera (Data Race) entre los hilos workers y `ExecuteCommandLists`.

### B. Parche RTV `FLOAT16` (`0xC0B2B`, `0xC0B37`, `0xC0EBD`, `0xC0ECB`)
* **Afirmación previa:** *"Pipeline FLOAT16 completo activado; elimina subexposición y saturación."*
* **Realidad forense:**
  1. En Xenos, los formatos HDR `k_16_16_16_16` utilizan un sesgo de exponente (`color_exp_bias -= 5`, división $\div 32$) para entrar en el rango `[-1, 1]` de `SNORM16`.
  2. Los shaders transpilados del juego aplican deliberadamente la multiplicación inversa ($\times 32$) al muestrear texturas intermedias y resolver la eDRAM.
  3. Al forzar el buffer a `FLOAT16` sin alterar los shaders:
     - El buffer guarda floats reales (no divididos por 32).
     - El shader del juego los vuelve a multiplicar por 32.
     - Cualquier color $\ge 0.031$ satura al límite del monitor $\rightarrow$ **96.35% de pantalla blanca quemada en el pasillo 3D**.
  4. En el logo de Rebellion, `DumpRenderTargets` usa un compute shader con operaciones de bits `OpBFI` asumiendo enteros `SNORM16`. Al recibir los bits de un float IEEE 754, corrompe el canal verde a 0 $\rightarrow$ **94.97% magenta puro**.

### C. La afirmación "ROV está 100% resuelto"
* **Afirmación previa:** *"ROV corre a 59.5 FPS continuos con 35% de GPU y fidelidad perfecta."*
* **Realidad forense:**
  1. La última corrida del usuario estaba en `render_target_path_d3d12 = "rtv"`, no ROV.
  2. Los registros reales de ROV en la escena 3D (`neverdead_085.log` y `docs/AUDIT_REPORT.md`) muestran **25.7 – 30.1 FPS** y **91% de GPU constante** debido al cuello de botella de `PixelShaderInterlock` al serializar accesos concurrentes de píxeles en el motor Asura.
  3. ROV nunca ha corrido a 60 FPS estables en gameplay 3D real en este proyecto.

### D. La Puerta de Presentación (`0xFFC10`)
* **Afirmación previa:** *"Presentación 1:1 perfecta sin sobrecoste."*
* **Realidad forense:**
  1. `NdCheckShouldPaintAndPresent()` en `0xFFC10` **no verifica `force_paint`**, lo que anula el repintado de Windows (`WM_PAINT`) al restaurar o mover la ventana.
  2. Omitir `Present()` tras un `ExecuteCommandLists` puede impedir el avance del índice del backbuffer en `DXGI_SWAP_EFFECT_FLIP_DISCARD`, generando riesgo de tearing y tearing inconsistente de estados D3D12.

---

## 3. Plan de Acción Crudo y Realista

1. **Eliminar parches ciegos destructivos**:
   - Revertir `NdPatchAsyncPipelineCache` (`0xEB030`) o sustituirlo por una solución real que no dropee draw calls.
   - Retirar los parches de formato `FLOAT16` en RTV hasta no resolver la coherencia de shaders y compute shaders.
2. **Definir la vía de renderizado viable**:
   - Diagnosticar a fondo por qué ROV satura al 91% la GPU (perfilado de Shader Interlock en RenderDoc / PIX).
   - O bien corregir el pipeline RTV reprogramando los compute shaders de `DumpRenderTargets` y compensando los multiplicadores de shaders.
3. **Validación obligatoria en combate real**:
   - Ningún commit ni informe afirmará que un problema está solucionado sin una captura real de RenderDoc/pantalla en el pasillo del hospital psiquiátrico con Bryce en movimiento, HUD visible, luminancia balanceada y telemetría de frametimes.
