# HANDOFF GEMINI ARGON v3 — Opciones del menú F1: ¿activarlas o dejarlas?

**Fecha:** 29 de octubre de 2026, 23:20 (GMT-4)
**Destinatario:** Gemini Argon (auditor técnico / siguiente IA de relevo)
**Proyecto:** NeverDead PC Recompilation — `C:\Users\katze\Downloads\_Proyectos_y_Backups\neverdead_project`
**Origen:** sesión de depuración con el usuario. Este documento sustituye en lo relativo al menú F1 a `HANDOFF_RELEVO_INGENIERIA_NEVERDEAD.md`, que da por operativos controles que **no lo están**.

---

## 1. Objetivo de este informe

Responder a una pregunta concreta: **las opciones gráficas del menú F1 (SSAO, motion blur, sombras, MRT EDRAM) no producen ningún efecto. ¿Se pueden activar de verdad, o hay que dejarlas como están / retirarlas?**

El informe separa explícitamente:

- **[VERIFICADO]** — medido o leído en código, con evidencia reproducible.
- **[HIPÓTESIS]** — plausible pero sin comprobar.
- **[BLOQUEO]** — impedimento técnico concreto encontrado.

---

## 2. Resumen ejecutivo

| Control del F1 | Estado | Evidencia |
|---|---|---|
| Límite de FPS (30/60/90/120/Ilimitado) | **[VERIFICADO] Funciona** | 30 FPS → 33.33 ms estables; 120 → 8.33 ms |
| Refresco del monitor (Hz) | **[VERIFICADO] Funciona** (arreglado hoy) | 200 → 120 → 60 → 200 Hz confirmado por Windows |
| Modo de ventana (720p/1080p/sin bordes) | **[VERIFICADO] Funciona** | Log `[CONFIG-UI] Modo de pantalla: ...` + ventana cambia |
| Salir al escritorio | **[VERIFICADO] Funciona** | `[CLEAN-EXIT]` sin `AppHangB1` |
| Post-procesado (none/fxaa) | **[SIN VERIFICAR]** | Solo escribe CVar en RAM + TOML; no medido con rigor |
| SSAO (`0x82B347EB`) | **[VERIFICADO] NO funciona** | −0,1 % de tiempo de frame, indistinguible del ruido |
| Motion blur (`0x82B347EF` / `0x82801CEF`) | **[NO VERIFICADO]** | Mismo origen que SSAO, sin medir |
| Sombras (`0x82B347E8` / `0x82B347EE`) | **[NO VERIFICADO]** | Mismo origen que SSAO, sin medir |
| MRT EDRAM clamp | **[NO VERIFICADO]** | CVar de runtime; no medido |

**Recomendación:** los cuatro checkboxes de `0x82B347xx` **no se pueden activar sin localizar antes las CVars reales del motor Asura**. Se propone (a) retirarlos o marcarlos como no funcionales, o (b) abrir una línea de trabajo de RE con el plan de la sección 8. No hay término medio barato: no existe forma de "arreglarlos" ajustando el valor actual, porque la dirección no corresponde a ningún efecto observable.

---

## 3. Estado verificado del menú F1

### 3.1 Qué hace cada control (código actual)

`src/nd_hooks.cpp`, `NdConfigWndProc` (L1774) procesa `WM_COMMAND`:

| ID | Control | Acción en código |
|---|---|---|
| 1001 | Combo FPS | `g_fps_target.store(target)` + `NdApplyDisplayRefresh()` + escribe `0.0f` en `0x82B31FBC` |
| 1002 | Combo ventana | `SetWindowLongPtrW` + `SetWindowPos` sobre el HWND del juego |
| 1003 | Combo post | `NdSetSwapPostEffectCVarInRam(val)` + `NdUpdateTomlSetting("swap_post_effect")` |
| 1004 | SSAO | `write_guest_u8(0x82B347EB, val)` — L1882 |
| 1005 | Motion blur | `write_guest_u8(0x82B347EF, val)` + `write_guest_u8(0x82801CEF, val)` — L1888-1889 |
| 1006 | Sombras | `write_guest_u8(0x82B347E8, val)` — L1895 |
| 1007 | MRT EDRAM | `NdSetMrtEdramClampCVarInRam(val)` + TOML |
| 1008 | Sombras avanzadas | `write_guest_u8(0x82B347EE, val)` — L1908 |
| 1009 | Cerrar menú | `DestroyWindow` |
| 1010 | Salir al escritorio | `TerminateProcess` |

### 3.2 Las escrituras sí llegan a memoria

**[VERIFICADO]** El byte escrito persiste. Prueba: se abre el menú, se pulsa SSAO, se cierra con "Cerrar menú", se vuelve a abrir. El checkbox conserva el estado nuevo (se relee de `read_guest_u8(0x82B347EB)` al crear el diálogo, L~1680). Por tanto **no es un problema de escritura ni de que el motor lo sobrescriba**: el valor está ahí, el motor simplemente no lo usa.

---

## 4. Evidencia de las mediciones

### 4.1 Metodología (importante: dos intentos previos fueron inválidos)

**Error cometido y descartado:** medir con el menú F1 abierto. Con el menú abierto el juego **deja de presentar** (`dxgi_present_fps` cae a ~0,3 en `neverdead_232.log`), la imagen se congela y cualquier comparación de píxeles es nula. El usuario detectó el fallo correctamente.

**Método válido aplicado:**

1. Juego en **partida real** (capítulo Asylum, enemigo delante, HUD activo) — confirmado por captura `docs/arranque_ok_titulo.png` y capturas de sesión.
2. Menú **cerrado** durante toda la medición.
3. GPU **saturada al 99 %** (escena limitada por GPU, no por el pacer ni por el refresco) → el tiempo de frame es proporcional al coste real.
4. Lectura del tiempo de frame desde el log: `[FRAME] N frames avg=X ms`, que promedia 300 frames.
5. Diseño **A/B/A/B** (dos alternancias) para separar el efecto de la deriva de la escena.

### 4.2 Resultado (log `neverdead_235.log`)

| Fase | Media | Desv. típica | Muestras |
|---|---|---|---|
| A1 — SSAO activado | 10.46 ms | 0.09 | 10.48 10.54 10.37 10.57 10.52 10.31 10.38 10.49 |
| B1 — SSAO desactivado | 10.46 ms | 0.11 | 10.52 10.31 10.38 10.49 10.40 10.59 10.62 10.36 |
| A2 — SSAO activado | 10.56 ms | 0.11 | 10.59 10.62 10.36 10.51 10.71 10.53 10.47 10.72 |
| B2 — SSAO desactivado | 10.54 ms | 0.13 | 10.71 10.53 10.47 10.72 10.43 10.36 10.47 10.66 |

**Media ON = 10.51 ms · Media OFF = 10.50 ms · Diferencia = −0,1 %**

Con desviación típica de 0,1 ms, un SSAO real (coste habitual 5-15 % de frame) habría producido una diferencia de 0,5-1,5 ms, es decir 5-15 veces el ruido. **No se detecta nada.** Conclusión: el efecto no se está aplicando en ninguno de los dos estados.

---

## 5. Lo que se arregló en esta sesión (para que Argon no lo vuelva a tocar)

### 5.1 Ventana negra al arrancar — CAUSA RAÍZ

El motor Asura recibía un límite de FPS distinto de 0 (`NdSetFpsLimitHook` devolvía `f0 = 60.0f`, `NdSyncFpsClampMemory` escribía `60.0f` en `0x82B31FBC`, `NdForceIntervalOneHook` pedía intervalo 2). Con eso el invitado hace **un solo `VdSwap`** y se queda esperando vblanks que nunca llegan. El hilo de UI sigue presentando ~200 frames/s **de negro**, por eso el log muestra `dxgi_present_fps=200` y la GPU está al 1-2 %.

**Arreglo:** el invitado recibe **siempre 0.0f**. `NdSyncFpsClampMemory` (L1986) ignora su argumento y escribe 0.0f; `NdForceIntervalOneHook` (L2044) devuelve 0; `NdSetFpsLimitHook` (L2062) devuelve 0.0f. El límite real lo aplica el pacer del host (`NdApplyFramePacer`).

**Indicador fiable de que el juego renderiza:** la línea `[FRAME] N frames avg=`. Si solo aparecen líneas `dxgi_present_fps=`, el invitado está parado y la imagen es negra.

### 5.2 Cuelgue al cambiar FPS en el menú

El handler del combo (L~1830) escribía el valor elegido **directamente** en `0x82B31FBC`, saltándose la protección. Carrera contra el hook que repone 0.0f: si el motor leía el valor antes, se bloqueaba para siempre. Evidencia en `neverdead_232.log`: último frame del invitado 22:49:43.962, evento "30.0 FPS" a las 22:49:43.970, y **ningún frame más**.

**Arreglo:** el menú escribe siempre `0.0f` en esa variable.

### 5.3 El Hz no seguía al FPS

El combo prometía "60 Hz" pero solo limitaba la presentación; el monitor seguía a 200 Hz.

**Arreglo:** nueva función `NdApplyDisplayRefresh(int hz)` (L1725): enumera modos con `EnumDisplaySettingsW`, elige el más cercano a la resolución actual y aplica `ChangeDisplaySettingsW(..., CDS_FULLSCREEN)` (transitorio: Windows restaura al salir). Con "Ilimitado" llama a `ChangeDisplaySettingsW(nullptr, 0)`.

**Verificado** (`neverdead_236.log` + `Win32_VideoController.CurrentRefreshRate`):

```
Refresco del monitor: 200 Hz -> 120 Hz (resultado 0)
Refresco del monitor: 120 Hz -> 60 Hz  (resultado 0)
Refresco del monitor restaurado al modo por defecto
```

---

## 6. Correcciones a informes previos (no heredar estos errores)

| Afirmación previa | Estado real |
|---|---|
| `HANDOFF_RELEVO_INGENIERIA_NEVERDEAD.md` §2: "arranque verificado sin bloqueos" | **[FALSO]** Desde el 27-oct ningún log presentaba un solo frame del invitado hasta hoy |
| "Los toggles de Asura reducen la carga de GPU" (mismo dossier, §7.2) | **[NO VERIFICADO]** El propio documento pedía verificarlo; ya está verificado y es negativo |
| "El parche de PSO con timeout 0 ms causaba el cuelgue" | **[FALSO]** El cuelgue era el límite de FPS. El PSO a 1 ms se mantiene porque es el valor correcto, no porque fuera la causa |
| "La PAINT-GATE bloquea la presentación" | **[FALSO]** Desactivarla hace que el hilo UI presente frames vacíos: el log parece sano y la pantalla sigue negra. Es una pista falsa peligrosa |
| "Las capturas de pantalla salen negras, no se puede verificar" | **[FALSO]** GDI devuelve negro, pero `PrintWindow(hwnd, hdc, 2)` funciona. Filtrar la ventana por título `rexglue`, no por `neverdead` (el Explorador coincide) |

---

## 7. Por qué los toggles no funcionan

**[VERIFICADO]** Las cinco direcciones proceden de un escaneo de `.data`, no de un trazado del sistema de CVars. El dossier original lo admite en su §7.2: *"verificar si los offsets descubiertos en `.data` ... reducen mediblemente la carga de GPU"*. Esa verificación nunca se hizo. Hecho ahora: negativa.

**[BLOQUEO 1]** `NeverDead_base.bin` (12 MB, volcado PPC) **no contiene ni una sola cadena ASCII legible** (`strings` devuelve 0). El XEX va comprimido, así que no se puede buscar `r_ssao`, `mblur`, etc. para anclar la búsqueda.

**[BLOQUEO 2]** El código recompilado en `generated/` **no referencia esas direcciones absolutas**: el recompilador emite accesos relativos a registro (`PPC_LOAD_U32(ctx.rX.u32 + offset)`), de modo que no hay forma de rastrear quién lee `0x82B347EB`.

**[BLOQUEO 3]** El grafo del proyecto (ver §8) **no contiene ningún nodo** sobre esas direcciones, el SSAO, el motion blur ni el menú F1. No hay relación documentada que seguir.

Sin nombre, sin referencia y sin grafo, localizar el byte correcto entre los ~4 MB de `.data` a ciegas es inviable: cada candidato exige ~20 s de medición A/B.

---

## 8. Grafos actuales del proyecto

Generados con `graphify` el 27-oct-2026 02:54 en `graphify-out/`.

| Métrica | Valor |
|---|---|
| Nodos | 25.519 |
| Aristas | 25.728 |
| Comunidades | 220 (29 mostradas, 191 omitidas por delgadas) |
| Procedencia | 99 % EXTRACTED · 1 % INFERRED (165 aristas, confianza media 0.85) |
| Corpus | 3 archivos · ~12,88 M palabras (dominado por `generated/neverdead_default/*.cpp`) |
| Coste | 0 tokens (extracción estructural AST) |

**Nodos relevantes presentes en el grafo** (todos en `src/nd_hooks.cpp`, comunidad 122):

`NdVdSwapHook()`, `NdVdInitHook()`, `NdEarlyInit()`, `NdCheckFpsToggleKey()`, `NdInitFpsMode()`, `NdSyncFpsClampMemory()`, `NdForceIntervalOneHook()`, `NdSetFpsLimitHook()`, `NdAdjustRenderTimeoutHook()`, `NdPatchAsyncPipelineCache()`, `NdProcCounters`, `NdThreadCpu`.

**Nodos relevantes AUSENTES del grafo** (comprobado con búsqueda directa sobre `graph.json`):

- `NdConfigWndProc` / `NdRunConfigUIDialog` / cualquier nodo `CONFIG-UI` → **0 coincidencias**
- `0x82B347EB` y resto de la familia `0x82B347xx` → **0 coincidencias**
- `SSAO` / `motion blur` / `mblur` → **0 coincidencias**

Es decir: **el menú F1 y sus toggles están fuera de la cobertura del grafo**. Cualquier trabajo futuro sobre ellos debería empezar regenerando el grafo (`graphify . --update`) para que estas funciones entren, y solo después intentar el trazado.

Consultas reproducibles:

```bash
cd <proyecto>
graphify query "SSAO motion blur shadow CVar toggle menu" --budget 1300
graphify query "NdConfigWndProc NdRunConfigUIDialog F1 config menu" --budget 1300
```

---

## 9. Opciones: activarlos o dejarlos

### Opción A — Dejarlos como están (coste: minutos)

No hacer nada. **Riesgo:** el menú promete control que no existe; el usuario ya ha perdido tiempo con ello. Si se elige esta vía, lo mínimo honesto es marcarlos en la UI como *"experimental / sin efecto verificado"* o retirarlos.

### Opción B — Retirarlos (coste: ~1 h)

Eliminar los cinco checkboxes de `NdConfigWndProc` y del diálogo. Quedan FPS, Hz, modo de ventana, post-procesado (pendiente de verificar) y salida limpia. Es la opción que menos promete y menos decepciona.

### Opción C — Activarlos de verdad (coste: alto, resultado incierto)

Única vía técnica realista. Plan concreto para Argon:

1. **Descomprimir el XEX** (`default.xex` en `assets/`) con `xeUnpack`/`xextool` para recuperar `.rodata` con las cadenas. Sin esto no hay punto de anclaje.
2. **Localizar nombres de CVar** del motor Asura (`r_ssao`, `mblur`, `shadow_quality`, `edram_clamp` o equivalentes). Confirmar cuáles existen realmente.
3. **Encontrar la tabla de CVars** en `.data`: estructura típica `{const char* nombre; void* valor; uint32 tipo; ...}`. Buscar punteros big-endian a las cadenas halladas en el paso 2.
4. **Leer la dirección real del valor** de cada entrada y sustituir los cinco offsets de `nd_hooks.cpp` (L1882, L1888, L1889, L1895, L1908).
5. **Validar cada toggle con la metodología de §4** (A/B/A/B, menú cerrado, GPU saturada). No aceptar un toggle sin diferencia > 3× la desviación típica.

**Alternativa a C si el paso 3 falla:** identificar el pase de SSAO por shader (hash del pixel shader) e interceptarlo en el renderer. Más invasivo y más frágil; solo como último recurso.

**Estimación honesta:** no puedo garantizar que el paso 3 tenga éxito. El motor Asura de NeverDead no está documentado públicamente y el binario está comprimido. Es un trabajo de varias horas con resultado incierto.

---

## 10. Anexo — Reproducir las mediciones

```bash
# 1. Lanzar y entrar en partida (Enter en la pantalla de carga)
cmd /c JUGAR_NEVERDEAD.bat

# 2. Confirmar que el invitado renderiza (debe aparecer esta línea)
grep "\[FRAME\] [0-9]* frames avg=" build_clang/logs/neverdead_*.log | tail -3

# 3. Confirmar saturación de GPU
nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader

# 4. Medir A/B con el menú CERRADO: abrir F1, pulsar el toggle, cerrar,
#    y leer 8 líneas de [FRAME] antes y después.
```

**Reglas de medición que no se deben romper:**

- Nunca medir con el menú F1 abierto (el juego deja de presentar).
- Nunca fiarse de `dxgi_present_fps`: cuenta presentaciones del hilo UI, no trabajo del invitado.
- Confirmar que la escena está viva (dos capturas separadas 3 s deben diferir).
- Filtrar la ventana del juego por `rexglue`, no por `neverdead`.

---

## 11. Estado del repositorio al cierre

- Raíz con los 7 archivos canónicos (`build_ninja.bat`, `CMakeLists.txt`, `CMakePresets.json`, `JUGAR_NEVERDEAD.bat`, `neverdead.toml`, `NeverDead_base.bin`, `neverdead_manifest.toml`).
- `JUGAR_NEVERDEAD.bat` sin variables de entorno: los valores correctos están compilados.
- Backup del ejecutable previo: `archive/neverdead.exe.antes-de-fps0.bak`.
- `build_clang/neverdead.exe` recompilado hoy con Clang 23 + Ninja (4 compilaciones sucesivas, todas limpias salvo warnings de deprecación de `getenv`/`fopen`).
- Última sesión de juego estable: 22:58-23:03 a 30 FPS con 100 muestras consecutivas de `dxgi_present_fps=30.0`.
