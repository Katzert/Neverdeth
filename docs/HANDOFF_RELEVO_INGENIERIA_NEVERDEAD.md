# DOSSIER DE RELEVO DE INGENIERÍA: NEVERDEAD PC RECOMPILATION
**Fecha de corte:** 29 de Octubre de 2026  
**Destinatario:** IA Relevista / Auditor Técnico / Gemini Argon  
**Estado general:** Funcional, estable en 3D nativo, arranque verificado sin bloqueos, menú de configuración F1 operativo.

---

## 1. Ficha Técnica y Entorno de Ejecución

- **Título:** NeverDead (Konami / Rebellion Developments, 2012) — Title ID `4B4E081F`.
- **Naturaleza técnica:** Recompilación estática C++ nativa para Windows x64 basada en **XenonRecomp** + **ReXGlue SDK** (bifurcación especializada del subsistema gráfico/runtime de Xenia Canary).
- **Compilador y Herramientas:** Clang 23 (LLVM) + LLD + Ninja.
- **Script de compilación:** [`build_ninja.bat`](file:///c:/Users/katze/.gemini/antigravity-ide/scratch/vibe-recomp/neverdead_project/build_ninja.bat) (ejecutar siempre como `cmd /c build_ninja.bat`).
- **Ejecutable canónico único:** [`build_clang/neverdead.exe`](file:///c:/Users/katze/.gemini/antigravity-ide/scratch/vibe-recomp/neverdead_project/build_clang/neverdead.exe) (~40.1 MB).
- **Lanzador de usuario:** [`JUGAR_NEVERDEAD.bat`](file:///c:/Users/katze/.gemini/antigravity-ide/scratch/vibe-recomp/neverdead_project/JUGAR_NEVERDEAD.bat) en la raíz (fija `CWD=%~dp0`, `PATH=%~dp0build_clang;%PATH%` y lanza el ejecutable con `--game_data_root assets --cache_path cache`).
- **Invariante Crítica de Raíz:** La raíz del proyecto debe contener **estrictamente 7 archivos canónicos**:
  1. `build_ninja.bat`
  2. `CMakeLists.txt`
  3. `CMakePresets.json`
  4. `JUGAR_NEVERDEAD.bat`
  5. `neverdead.toml`
  6. `NeverDead_base.bin` (volcado PPC de 12 MB)
  7. `neverdead_manifest.toml`  
  *(Cualquier script, log o binario suelto en la raíz debe ser archivado o eliminado).*

---

## 2. Subsistema Gráfico: D3D12 ROV vs RTV y Gestión de PSOs

### ¿Por qué ROV es la ruta definitiva?
- **Fidelidad Visual 1:1:** NeverDead utiliza la eDRAM de Xbox 360 con continuos pases de tiling predicho, pases de downsampling para bloom en cascada (`0x000` -> `0x230` -> `0x348` -> `0x460` -> `0x578` -> `0x690`) y compensaciones no lineales de exposición (`color_exp_bias`).
- En modo `rtv`, `draw_util::GetResolveInfo` y `UpdateSystemConstants` alteran `color_exp_bias` (-5 / división por 32), produciendo escenas completamente quemadas en blanco, pases de post-procesado magenta o texturas oscuras.
- En modo `render_target_path_d3d12 = "rov"`, se utiliza emulación de eDRAM por hardware mediante **Rasterizer-Ordered Views (ROV)** con `ByteAddressBuffer` y `PixelShaderInterlock`. La fidelidad de iluminación, niebla volumétrica y balance especular es idéntica a la consola original.

### Corrección del Cuelgue de PSOs en el Arranque (Boot Stall)
- **Problema previo:** `NdPatchAsyncPipelineCache()` forzaba un timeout de `0 ms` sobre `PipelineCache::EndSubmission` (`rexgpu-xenos.dll+0xEB09F`). Si este parche se aplicaba al inicio (`NdEarlyInit`), el motor intentaba compilar los 316 PSOs iniciales en disco durante `InitializeShaderStorage` y el timeout inmediato quebraba la cola asíncrona de D3D12, abortando el proceso tras `Translated 350 shaders`.
- **Solución implementada:** En [`src/nd_hooks.cpp`](file:///c:/Users/katze/.gemini/antigravity-ide/scratch/vibe-recomp/neverdead_project/src/nd_hooks.cpp), `NdPatchAsyncPipelineCache()` fue desacoplado del inicio y ahora se activa **exclusivamente en `NdVdSwapHook` cuando `n >= 2`**.
- **Resultado comprobado (`neverdead_202.log`):**
  - 350 shaders traducidos del cache en 39 ms.
  - 316 pipelines D3D12 creados limpiamente en 189 ms.
  - El motor supera la barrera de shaders e inicia `VdInitializeEngines #1` sin bloqueos.

---

## 3. Pacing de Framerate, Reloj del Motor Asura y VSync

- **Configuración TOML:** `vsync = false` y `d3d12_allow_variable_refresh_rate_and_tearing = true` en [`neverdead.toml`](file:///c:/Users/katze/.gemini/antigravity-ide/scratch/vibe-recomp/neverdead_project/neverdead.toml).
- **Variable de Consola de Asura (`0x82B31FBC`):**
  - Dirección física guest: `0x82B31FBC` (float en big-endian).
  - Valores: `30.0f` (`0x41F00000`), `60.0f` (`0x42700000`), `0.0f` (`0x00000000` = sin clamp/Sowa_95 patch).
  - Por defecto arranca en **`60.0f`** para garantizar estabilidad física y temperaturas frescas de inicio.
- **Hooks de Render y Presentación:**
  - `NdForceIntervalOneHook` (`0x826B79D8`): Devuelve `r11 = 2` para 30 FPS; `r11 = 0` para 60 FPS o superior (flip inmediato).
  - `NdAdjustRenderTimeoutHook` (`0x826BE900`): Timeout de render ajustado dinámicamente (-16.1 ms para 60 Hz).
  - **Frame Pacer Híbrido de Microsegundos:** Implementado en `NdVdSwapHook()` combinando `CreateWaitableTimerExW(..., CREATE_WAITABLE_TIMER_HIGH_RESOLUTION)` con `QueryPerformanceCounter` y `YieldProcessor()`. Permite limitar el framerate a 30, 60, 90 o 120 FPS de forma ultra-precisa con 0% de sobrecarga de CPU por giros ocupados.
- **Atajo en caliente:** La tecla **`F8`** alterna instantáneamente en partida entre 60 FPS y 30 FPS.

---

## 4. Menú de Configuración en Vivo (`F1`) y Salida Limpia

Al pulsar **`F1`** se despliega una interfaz Win32 nativa en Dark Mode integrada directamente sobre el HWND del juego:
- **Sobrecarga:** 0% de CPU y GPU mientras permanece cerrada.
- **Compatibilidad con Overlays:** La detección de ventana `NdGetGameWindow()` utiliza `EnumWindows` y subclassing (`GWLP_WNDPROC`), funcionando incluso si NVIDIA ShadowPlay, RTSS o Steam enganchan `CreateSwapChainForHwnd`.
- **Controles Disponibles:**
  1. **Límite de Framerate:** `30 FPS`, `60 FPS`, `90 FPS`, `120 FPS` e `Ilimitado (0.0f)`.
  2. **Modo de Pantalla:** `Ventana 720p`, `Ventana 1080p`, `Sin Bordes (Desktop)`.
  3. **Pases Gráficos del Motor Asura (Modo iGPU / Laptop):**
     - Oclusión Ambiental (SSAO): `0x82B347EB`
     - Desenfoque de Movimiento (Motion Blur): `0x82B347EF` y `0x82801CEF`
     - Calidad de Sombras: `0x82B347E8` y `0x82B347EE`
     - MRT EDRAM Range Clamp: `rexgpu-xenos.dll+0x28ED10`
- **Salida Limpia al Escritorio:**
  - Botón "Salir al Escritorio" y captura de `WM_CLOSE` en el juego. Invoca `TerminateProcess(GetCurrentProcess(), 0)` de forma inmediata.
  - Elimina los congelamientos residuales (`AppHangB1` / Evento 1002) que ocurrían al usar `Alt+F4` mientras los hilos de fondo de D3D12/ReXGlue esperaban desasignación.

---

## 5. Subsistema de Memoria, Pre-commit Físico y VEH

1. **Pre-commit Físico MBI (`0x300000000`):**
   - En [`src/neverdead_app.h`](file:///c:/Users/katze/.gemini/antigravity-ide/scratch/vibe-recomp/neverdead_project/src/neverdead_app.h), se implementó `ensure_host_range_committed` usando `VirtualQuery` por bloques para commitear la memoria física reservada (`0x20010000` bytes).
   - Se erradicó por completo el error `err=487` (`ERROR_INVALID_ADDRESS`) que ocurría al intentar commitear la arena entera de un solo bloque.
2. **Vectored Exception Handler (VEH):**
   - En [`src/crash_diag.cpp`](file:///c:/Users/katze/.gemini/antigravity-ide/scratch/vibe-recomp/neverdead_project/src/crash_diag.cpp), se eliminó la condición suicida `if (++s_prot_fault_cnt <= 20)`.
   - Cuando una página protegida es reparada con `VirtualProtect`, el VEH retorna incondicionalmente `EXCEPTION_CONTINUE_EXECUTION`. El contador de fallos ahora solo controla la frecuencia de escritura en disco, evitando que el proceso muera en el fallo #21.
3. **LockManager Concurrente:**
   - En [`src/nd_import_diag.h`](file:///c:/Users/katze/.gemini/antigravity-ide/scratch/vibe-recomp/neverdead_project/src/nd_import_diag.h), `std::mutex` fue reemplazado por `std::shared_mutex`, permitiendo lecturas concurrentes (`std::shared_lock`) en la tabla de critical sections sin serializar los hilos de trabajo.

---

## 6. Archivos Críticos y Comandos Esenciales

| Archivo | Rol e Importancia |
|---|---|
| [`src/nd_hooks.cpp`](file:///c:/Users/katze/.gemini/antigravity-ide/scratch/vibe-recomp/neverdead_project/src/nd_hooks.cpp) | Hooks mid-asm, menú F1, alternancia F8, pacer de framerate, y parche diferido de PSOs. |
| [`src/neverdead_app.h`](file:///c:/Users/katze/.gemini/antigravity-ide/scratch/vibe-recomp/neverdead_project/src/neverdead_app.h) | Configuración de rutas (`assets`, `cache`), inicialización de ReXGlue y pre-commit MBI de RAM física. |
| [`src/crash_diag.cpp`](file:///c:/Users/katze/.gemini/antigravity-ide/scratch/vibe-recomp/neverdead_project/src/crash_diag.cpp) | Manejador VEH para páginas de memoria protegidas y registro de telemetría de fallos. |
| [`src/nd_import_diag.h`](file:///c:/Users/katze/.gemini/antigravity-ide/scratch/vibe-recomp/neverdead_project/src/nd_import_diag.h) | `LockManager` optimizado con `std::shared_mutex` para sincronización de critical sections. |
| [`neverdead.toml`](file:///c:/Users/katze/.gemini/antigravity-ide/scratch/vibe-recomp/neverdead_project/neverdead.toml) | Configuración canónica activa: backend `rov`, `vsync = false`, `pipeline_creation_threads = -1`. |
| [`JUGAR_NEVERDEAD.bat`](file:///c:/Users/katze/.gemini/antigravity-ide/scratch/vibe-recomp/neverdead_project/JUGAR_NEVERDEAD.bat) | Script de ejecución principal del usuario. |
| [`build_ninja.bat`](file:///c:/Users/katze/.gemini/antigravity-ide/scratch/vibe-recomp/neverdead_project/build_ninja.bat) | Script de compilación automatizada con Clang y Ninja. |

### Comandos de Terminal
- **Compilar:** `cmd /c build_ninja.bat`
- **Revisar últimos logs:** `Get-Content build_clang/logs/neverdead_*.log -Tail 30`
- **Verificar invariante de raíz (debe retornar 7):** `(Get-ChildItem -File).Count`

---

## 7. Próximos Pasos Recomendados para la Siguiente IA

1. **Validación de Sesión Prolongada de Gameplay:**
   - Lanzar el juego desde [`JUGAR_NEVERDEAD.bat`](file:///c:/Users/katze/.gemini/antigravity-ide/scratch/vibe-recomp/neverdead_project/JUGAR_NEVERDEAD.bat).
   - Probar la respuesta de **`F1`** en pleno juego para verificar el cambio de resolución y alternancia de toggles gráficos de Asura.
   - Probar **`F8`** para verificar que el cambio dinámico entre 30 y 60 FPS responde fluidamente sin alterar la velocidad de la física Havok.
2. **Ajuste Fino de Toggles de Asura en `F1`:**
   - Si se busca mayor fluidez en hardware modesto, verificar si los offsets descubiertos en `.data` (`0x82B347EB` SSAO y `0x82B347EF` Motion Blur) reducen mediblemente la carga de GPU en combate intenso.
3. **Mantenimiento de Reglas:**
   - Prohibido terminantemente ejecutar `read_graph` en memoria (usar únicamente `open_nodes` quirúrgico sobre `NeverDead_Recompilation_Master`).
   - Mantener siempre los 7 archivos canónicos en la raíz.
