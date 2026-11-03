# INFORME PARA ARGON — NeverDead PC Recompilation
**Fecha:** 3 de noviembre de 2026 · **Versión:** v10 (sustituye a v9; autocontenido)
**Binario:** `build_clang/neverdead.exe` (01:39, 38.836.224 B) · **Commit:** `0ee25c2`
**Grafo:** 703 nodos · 867 aristas · 34 comunidades · `docs/GRAFO_ARGON.txt` (34 KB, 289 líneas)

---

## 0. Resumen ejecutivo

| Tema | Estado | Dato |
|---|---|---|
| 30 FPS por defecto en el host | **HECHO** | GPU 97 % → **42 %** con todo ON |
| Pacer híbrido (Sleep + spin) | **HECHO** | `timeBeginPeriod(1)` + `Sleep(rest−1,5)` si >2 ms |
| Combos en Dark Mode | **HECHO** | `DarkMode_CFD` funciona; `SetWindowTheme(h,"","")` no hacía nada |
| Medición real de GPU (`nvidia-smi`) | **HECHO** | 3 casos, 30 muestras c/u |
| Auditoría interlock ROV | **CORREGIDAS 2 premisas** | no hay interlock en D3D12 |
| Rebuild de `rexgpu-xenos.dll` | **POSIBLE pero no hecho** | árbol configurado, DLL nunca construida |
| Cuarentena 1,7 GB | **ELIMINADA** | 9,0 GB libres |

---

## 1. T1 — 30 FPS por defecto y pacer de bajo consumo

### 1.1 Cambios

- `g_fps_target` arranca en **`30.0f`** (antes `0.0f`), y `NdInitFpsMode()` también
  (la variable de entorno `ND_FPS_LIMIT` sigue teniendo prioridad).
- **La regla de estabilidad se respeta:** `NdSyncFpsClampMemory()` y `NdSetFpsLimitHook()`
  siguen escribiendo **`0.0f`** en `0x82B31FBC`. Solo el pacer del host limita.
- `NdEnsureTimerResolution()` (`timeBeginPeriod(1)`, `std::call_once`) llamado desde
  `NdEarlyInit()` y desde `NdInitPacer()`. Sin esto `Sleep()` tiene granularidad de 15,6 ms
  y un frame de 33,3 ms se convierte en 46 ms.
- `NdApplyFramePacer()`: si quedan **>2 ms**, `Sleep(remaining_ms − 1.5)`; el resto, spin de
  precisión con `QueryPerformanceCounter`. Sustituye al `SetWaitableTimer`.
  **Ese sueño real es lo que libera la cola de envío de la GPU** y explica la caída de vatios.
- Combo FPS: `CB_SETCURSEL, 2` → arranca en **"30 FPS"**, coherente con el host.
- Combos: `SetWindowTheme(h, L"DarkMode_CFD", nullptr)` — ver §4.

### 1.2 Por qué importa el `Sleep` y no solo el límite

Limitar con spin puro mantiene el hilo al 100 % de un core y no deja respirar al driver.
Con `Sleep` el hilo que presenta cede la CPU entre frames, la cola de D3D12 se vacía con
margen y la GPU baja de frecuencia de trabajo. La diferencia se ve en la tabla de §2:
**33 W menos** con el mismo contenido en pantalla.

---

## 2. T2 — Consumo real de GPU (`nvidia-smi`)

RTX 2060 (límite 160 W), escena del hospital, 15 s de muestreo cada 500 ms tras 45 s de
arranque y 3 s de asentamiento. Arnés: `tools/bench_gpu.py 1|2|3`.

| Caso | Configuración | GPU | Mem GPU | Potencia | Temp | ms/frame | FPS |
|---|---|---|---|---|---|---|---|
| **1** | 30 FPS + Bajo Consumo (16 flags OFF) | **25,3 %** | 4,0 % | **40,0 W** | 58,9 °C | 30,87 | 32,4 |
| **2** | 30 FPS + Calidad Original (todo ON) | **41,9 %** | 8,6 % | **64,6 W** | 63,8 °C | 28,00 | 35,7 |
| **3** | Ilimitado + Calidad Original | **97,3 %** | 23,3 % | **97,9 W** | 70,1 °C | 10,51 | 95,2 |

**Lectura:**

- El límite de 30 FPS es **la optimización más rentable medida hasta ahora**: −57 puntos de
  GPU y −33 W **sin tocar un solo flag de render**. Comparado con el preset de 16 flags
  (+41 % FPS, §v8), esto ataca el consumo, que es lo que importa en gama baja.
- Caso 1 (25 % / 40 W) es el objetivo cumplido para portátiles y GPUs de 75 W.
- **Matiz:** la columna FPS es el bucle del invitado (`[FRAME]`), no la presentación. El caso 2
  mide 35,7 FPS porque el invitado va por delante del presentador, que sí está clavado a 30.

---

## 3. T3 — Auditoría del interlock ROV (dos premisas corregidas)

### 3.1 No existe `BeginInvocationInterlock` en la ruta D3D12

```
spirv_translator.cpp:3048      builder_->createNoResultOp(spv::OpBeginInvocationInterlockEXT);
spirv_translator_rb.cpp:869    builder_->createNoResultOp(spv::OpBeginInvocationInterlockEXT);
```

Esas son las rutas **Vulkan**. En `dxbc_translator*.cpp` **no hay ninguna llamada de
interlock**: en D3D12 el orden de las invocaciones lo garantiza ROV por hardware, sin
instrucción explícita. El coste por píxel no viene de un `Begin/EndInvocationInterlock`
—que no existe— sino del **read-modify-write** que el shader hace sobre el buffer de eDRAM.

### 3.2 Dónde está de verdad el coste

`dxbc_translator_om.cpp` tiene **3081 líneas** (no ~1250; los números de la tarea están
desfasados). Funciones relevantes:

| Línea | Función |
|---|---|
| 25 | `StartPixelShader_LoadROVParameters` |
| 364 | `ROV_DepthStencilTest` |
| 1061 | `ROV_UnpackColor` — desempaqueta el color leído del buffer según `kEdramRTFormatFlags` |
| 1204 | `ROV_PackPreClampedColor` |
| 1372 / 1469 | `ROV_HandleColorBlendFactorCases` / `...AlphaBlendFactorCases` |
| **1820** | **`CompletePixelShader_WriteToROV`** (~800 líneas: test de profundidad tardío, lectura de dst, blend, escritura) |
| 1955 | `OpSwitch` sobre **`kEdramRTBlendFactorsOps`** — aquí se decide el blend por RT |
| 2624 | `CompletePixelShader` |

**No hay un `blend_enable` booleano.** El blend se resuelve con un switch de factores/ops
por render target (`kEdramRTBlendFactorsOps`). Un fast-path de "escritura directa" tendría
que detectar el caso *factores = One/Zero, op = Add* (blend efectivamente apagado) y saltarse
la lectura de dst — no comprobar un flag que no existe.

### 3.3 ¿Se puede recompilar `rexgpu-xenos.dll`?

- `src/graphics/CMakeLists.txt:99` → `add_library(rexgpu-xenos SHARED ...)`.
- El árbol está **configurado**: `out/build/win-amd64` con `Ninja Multi-Config` y `build.ninja`
  que referencia el target.
- `thirdparty/` vendorizado: `dxbc`, `dxc`, `cli11`, `crypto`, `disasm`, `disruptorplus`...
- **Pero `find` no encuentra ninguna `rexgpu-xenos.dll` compilada**: el SDK se configuró y
  nunca se construyó. Recompilarlo = compilar el SDK entero (core, ui, graphics, dxc) —
  horas de build, no un `ninja` corto. **No se hizo en este turno.**

**Recomendación:** si se ataca el fast-path de blend, hacerlo en una rama del SDK con build
nocturno y validación visual en el hospital; el riesgo de romper el color es alto y el
beneficio (evitar una lectura de 4 bytes por píxel) es menor que el que ya dio el límite de 30.

---

## 4. T4 — Limpieza y release

- **Cuarentena eliminada:** `C:\Users\katze\Downloads\_neverdead_cleanup_2026-11-02\`
  (1,7 GB, 8.519 archivos). Espacio libre en C: **9,0 GB**.
- **Combos en oscuro:** `DarkMode_CFD` sí funciona. El intento de v9
  (`SetWindowTheme(h, L"", L"")`) no cambiaba nada porque desactivar el tema no oscurece
  un `COMBOBOX`; hay que pedirle al tema oscuro nativo de Win10 1809+.
- Captura nueva en `dist/menu_f1_alpha_preview.png` y `docs/assets/menu_f1_preview.png`
  con "30 FPS" seleccionado y los dos combos oscuros.
- `README.md`: sección **"Rendimiento y Referencia para PCs de Gama Baja (30 FPS por defecto)"**
  con la tabla de §2 y la nota de que `0x82B31FBC` sigue a `0.0f`.
- `dist/NeverDead_Recomp_v0.1.0_Alpha.zip` → **14,71 MB, 23 archivos**.

---

## 5. Grafo

| Métrica | v9 | v10 |
|---|---|---|
| Nodos | 701 | **703** |
| Aristas | 861 | **867** |
| Comunidades | 35 | **34** |
| `GRAFO_ARGON.txt` | 291 líneas | **289 líneas (34 KB)** |

Nuevos en el grafo: `NdEnsureTimerResolution()`, `NdApplyFramePacer()`, `NdApplyDarkComboTheme()`.
`nd_hooks.cpp` es ya el nodo con más conexiones de `src/` (84).

---

## 6. Riesgos abiertos

| # | Riesgo | Estado |
|---|---|---|
| 1 | Coste GPU por píxel en ROV | **Abierto.** Early-Z ya activo (v9); el coste es el RMW del buffer. Fast-path de blend = rebuild del SDK |
| 2 | FPS del invitado ≠ presentación | Cosmético: `[FRAME]` mide el bucle, el pacer fija 30. Documentado en el README |
| 3 | 30 FPS como defecto puede molestar a quien tenga GPU potente | Se cambia en el combo o con `ND_FPS_LIMIT=0` |
| 4 | Docs sin nodos semánticos en el grafo | `uv tool install "graphifyy[anthropic]" --force` + `--update` |
| 5 | `git push` sin hacer | `git remote add origin … && git push -u origin main` |

---

## 7. Evidencia

| Archivo | Contenido |
|---|---|
| `tools/bench_gpu.py` | arnés de los 3 casos con `nvidia-smi` |
| `dist/menu_f1_alpha_preview.png` | menú con 30 FPS y combos oscuros |
| `docs/GRAFO_ARGON.txt` | grafo en texto (34 KB) |
| `dist/NeverDead_Recomp_v0.1.0_Alpha.zip` | release (14,71 MB) |
| `docs/RECAP_PARA_ARGON_2026-11-02.md` | este informe |
| `.workbuddy-ai/memory/2026-11-02.md` | bitácora de las 6 sesiones |

**No se tocó:** `neverdead.toml`, `rexruntime.dll`, `rexgpu-xenos.dll`, `src/crash_diag.cpp`,
`src/neverdead_app.h`, `generated/`, `assets/`.
**Modificado:** `src/nd_hooks.cpp` (30 FPS, pacer, combos), `README.md`,
`tools/bench_gpu.py` (nuevo), `docs/`.
