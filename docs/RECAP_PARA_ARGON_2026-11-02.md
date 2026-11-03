# INFORME PARA ARGON — NeverDead PC Recompilation
**Fecha:** 3 de noviembre de 2026 · **Versión:** v8 (sustituye a v7; autocontenido)
**Binario:** `build_clang/neverdead.exe` (00:58, 38.834.688 B) · **Commit:** `1b7f428`
**Grafo:** `graphify-out/` regenerado → **699 nodos · 858 aristas · 33 comunidades**

---

## 0. Resumen ejecutivo

| Tema | Estado | Evidencia |
|---|---|---|
| Menú F1 rediseñado (Dark, 520×320, sin echoing) | **HECHO + verificado en vivo** | §1 · captura `dist/menu_f1_alpha_preview.png` |
| Toggle F1/ESC desde cualquier ventana | **HECHO** (2 bugs corregidos) | §1.4 |
| Parche ROV Early-Z en el SDK | **NO VIABLE en este turno** (diagnóstico cerrado) | §2.1 |
| Benchmark flags "Downpour" | **MEDIDO: empeora** (79,3 vs 87,5–101,0 FPS) | §2.2 |
| Repo Git + paquete Alpha | **HECHO** (201 archivos, 2,88 MB, ZIP 14,21 MB) | §3 |
| Grafo del proyecto | **ACTUALIZADO y enfocado** (25.536 → 699 nodos) | §4 |
| Coste GPU real a 720p | **SIGUE ABIERTO** | §6 |

---

## 1. T1 — Menú F1 rediseñado

### 1.1 Lo que se implementó

Reescritura completa del bloque `enum`→`NdOpenConfigUI` en `src/nd_hooks.cpp` (424 líneas nuevas):

- **Geometría**: 520×320 de área cliente exacta (`AdjustWindowRectEx`), centrada, `WS_POPUP |
  WS_CAPTION | WS_SYSMENU`, título `NeverDead — Ajustes`.
- **Dark nativo**: `DwmSetWindowAttribute(hwnd, 20, ...)` con `dwmapi.dll` resuelto por
  `GetProcAddress` (cero cambios en `CMakeLists.txt`).
- **Pintado**: dos pinceles `static` en el `WndProc` — `#16161C` (fondo) y `#22222C` (controles) —
  con `WM_ERASEBKGND`, `WM_CTLCOLORDLG/STATIC/BTN` y `WM_CTLCOLORLISTBOX/EDIT`.
- **Layout**: fila de combos (Pantalla / FPS) · fila de perfiles · 10 casillas en 2 columnas ·
  2 botones abajo. Sin hexadecimales, sin "Desactivar" repetido, sin párrafos de ayuda.
- **Lógica positiva**: `BST_CHECKED` cuando `read_guest_u8(va) != 0`; al hacer clic escribe
  `checked ? 1 : 0`. Sombras escribe también su flag compañero `0x828E1FC5` (CSM).
- **Presets**: `IDC_CFG_PRESET_HIGH` (1033) pone los 16 flags a 1; `IDC_CFG_PRESET_LOW` (1032)
  los pone a 0 dejando `HUD = 1`. Ambos resincronizan las casillas con `BM_SETCHECK`.
- **Logs**: todos los `REXLOG_INFO` del `WndProc` van dentro de `if (NdIsDiagEnabled())`.

### 1.2 Dos bugs que había que corregir sí o sí

1. **El ESC del pseudocódigo nunca disparaba**: actualizaba `s_esc_prev = esc_down` *antes* de
   evaluar `esc_down && !s_esc_prev`, con lo que la condición era siempre falsa. Reordenado con
   detección de flanco correcta.
2. **El primer F1 no abría nada**: el timer vive en la ventana del menú, que no existe hasta
   crearla. Ahora el subclass del juego crea la ventana en el primer F1; a partir de ahí solo
   marca `g_menu_toggle_request`, que consume el timer. Así no hay doble toggle cuando el foco
   está en el juego (flag `*_wm_handled` compartido entre `WM_KEYDOWN` y `WM_TIMER`).

### 1.3 Incidencia durante la sustitución (resuelta)

Al reemplazar el bloque del menú se borró también `NdDumpAsuraCVarsOnce` (estaba entre el `enum`
y el diálogo) → el enlace falló con `undefined symbol`. Recuperada del backup
`scratch/nd_hooks.cpp.bak_turno4` (en la cuarentena). Los otros dos helpers
(`NdSetAsuraCVarByLow16Flag`, `NdReadAsuraCVarOrGuest`) eran del menú viejo y quedaron fuera
a propósito: el menú nuevo escribe los bytes de `.data` directamente.

### 1.4 Verificación en ejecución real

```
juego listo en 1s, hwnd=5311090
F1 abre: OK (hwnd=1969288, visible=True)
captura 520x320 -> dist/menu_f1_alpha_preview.bmp
F1 con foco en el menu -> cerrado: True
F1 reabre: True
ESC cierra: True
sin procesos neverdead huerfanos
```

### 1.5 Defecto cosmético conocido

Botones y combos conservan la cara clara del tema de Windows: `WM_CTLCOLORBTN` no aplica a
controles con *visual styles*, y `SetWindowTheme(h, L"", L"")` no cambió el resultado en Win11
(la captura salió idéntica). Para botones realmente oscuros hace falta `BS_OWNERDRAW` con
dibujado manual — ~60 líneas, no incluidas en este turno.

---

## 2. T2 — GPU: el parche ROV Early-Z y los flags de Downpour

### 2.1 El early-Z del SDK no aplica a nuestra ruta

`pipeline_cache.cpp:885` contiene el hint de early-Z, pero está **dentro** de:

```cpp
if (render_target_cache_.GetPath() == RenderTargetCache::Path::kHostRenderTargets) {
  ...
  if (shader.implicit_early_z_write_allowed() && ...)
    modification.pixel.depth_stencil_mode = DepthStencilMode::kEarlyHint;
```

Nuestro `neverdead.toml` usa `render_target_path_d3d12 = "rov"` → **ese bloque no se ejecuta**.
El interlock que serializa píxeles lo emiten los traductores (`spirv_translator.cpp:3048`,
`spirv_translator_rb.cpp:869`, y el equivalente DXBC), que viven **dentro de
`rexgpu-xenos.dll`**.

**Conclusión honesta:** aplicar ese parche exige **reconstruir `rexgpu-xenos.dll`** desde
`rexglue_sdk_src` (build completo del SDK, no un `ninja` de 2 minutos) y validar el cambio de
semántica del interlock. Parchearlo en memoria no es realista: habría que reescribir en caliente
el traductor de shaders. **No se hizo.**

### 2.2 Benchmark de los flags "Downpour"

```
python tools/bench_v6.py A --no-d3d12_submit_on_primary_buffer_end \
  --no-d3d12_readback_resolve --no-d3d12_readback_memexport --no-pre_mask_resolve_l2_block
→ 12,60 ms/frame = 79,3 FPS   ([CS-GUARD]=0, [PAINT-MODE] restaurado=1)
```

| Config | FPS |
|---|---|
| A base (referencia, 3 corridas) | **87,5 – 101,0** |
| A + los 4 flags | **79,3** |

**Empeora.** No se inyectan por defecto ni se añaden a `JUGAR_NEVERDEAD.bat`.

---

## 3. T3 — Repositorio Git y paquete Alpha

### 3.1 Repo

```
git log --oneline -1 → 1b7f428 Alpha v0.1.0: menu F1 dark mode, parche fpscr del SDK, presets y arnes
git status --short   → (vacío)
201 archivos · 2,88 MB · 0 fugas
```

`.gitignore` con la lista exacta pedida (`/assets/`, junctions, `/NeverDead_base.bin`,
`/build_clang/`, `/cache/`, `/dist/`, `/generated/`, `/graphify-out/`, `/.workbuddy-ai/`,
`*.log/bmp/png/dmp/exe/dll/pdb/obj`).

**Excepción consciente a la regla de los 7 canónicos:** `.gitignore` y `README.md` viven en la
raíz porque git los exige ahí. Son los dos únicos archivos añadidos.

### 3.2 El parche del SDK viaja en el repo

`patches/sdk/rex/ppc/context.h` (13.142 B, 3 marcas `ND-PATCH`) + `patches/sdk/README.md` con el
comando de aplicación (`xcopy /E /Y patches\sdk\rex <SDK>\include\rex\`). Sin esto, cualquiera que
clone el repo recompila con el ping-pong de `ldmxcsr` de vuelta.

### 3.3 Paquete de release

`tools/package_alpha_release.py` → **`dist/NeverDead_Recomp_v0.1.0_Alpha.zip`**
(14.896.682 B · 14,21 MB · 19 archivos):

```
neverdead.exe 38.834.688 · rexruntime.dll 9.964.032 · rexgpu-xenos.dll 2.726.400
neverdead.toml · neverdead_manifest.toml · JUGAR_NEVERDEAD.bat · README.md
config/ (8 toml) · tools/analyze_assets.py · tools/BENCH_NEVERDEAD.bat
patches/sdk/rex/ppc/context.h · patches/sdk/README.md
```

El script **aborta** si detecta rutas prohibidas (`assets/`, `NeverDead_base.bin`,
`build_clang/logs`, `generated/`). Dos sustituciones: `tools/extract_neverdead_assets.py` **no
existe** (se incluyó `analyze_assets.py`) y el extractor real (`tools/asura_extractor`, 31 MB de
un `.exe` de terceros) queda fuera del paquete.

---

## 4. T5 — Grafo actualizado (cambio importante)

`.gitignore` excluye `/generated/`, y graphify **respeta .gitignore**: el grafo pasó de
25.536 nodos dominados por el codegen a un mapa real del código versionado.

| Métrica | v7 (antes) | v8 (ahora) |
|---|---|---|
| Nodos | 25.536 | **699** |
| Aristas | 25.776 | **858** |
| Comunidades | 221 (190 thin) | **33** |
| Nodos legibles | 702 | **612** en 37 archivos |
| Aristas entre archivos | 420 | **63** |
| `docs/GRAFO_ARGON.txt` | 67 KB / 858 líneas | **34 KB / 287 líneas** |

Relaciones ahora útiles: `defines=263`, `references=201`, `contains=100`, `calls=94`,
`imports=77`. Comunidades con nombre derivado de sus nodos top (cluster-only pierde los nombres
curados): `PPCContext / SaveNonVolatiles` (211), `context.h / Register` (40),
`PSO blocking compile stutter / ROV overdraw saturation` (27), `RTV render-target path visual
collapse` (25), `Cuello de Botella CPU en Combate` (24), `nd_import_diag.h / REX_HOOK_RAW` (18).

**Para Argon:** `docs/GRAFO_ARGON.txt` es el mapa a leer. La comunidad `[4]` sigue siendo la
mina: *"ROV overdraw saturation at 95-99% GPU"*, *"Xenos color_exp_bias −= 5 vs shader ×32"*,
*"eDRAM emulation strategy question"* — las hipótesis de GPU que nunca se cerraron.

**Limitación:** los 15 documentos nuevos siguen sin nodos semánticos (backend LLM no instalado).
Para completarlo: `uv tool install "graphifyy[anthropic]" --force` y `graphify . --update`.

---

## 5. T4 — Compilación y verificación

```
ninja -C build_clang neverdead → "0 written, 0 unchanged, 0 deleted, 1 module(s) up to date" (0.2 s)
neverdead.exe → 38.834.688 bytes (00:58)
```

Build limpio tras 3 iteraciones (error de `fmt` con `wchar_t*`, símbolo perdido, y el fix del
toggle). Sin warnings nuevos.

---

## 6. Riesgos abiertos

| # | Riesgo | Estado |
|---|---|---|
| 1 | Coste GPU a 720p | Descartados: tiling shm, clamp eDRAM + unclipped VS, `direct_host_resolve`, los 4 flags Downpour. **Sin identificar.** Candidato vivo: `pre_mask_resolve_l2_block` (individual), `resolve_resolution_scale_fill_half_pixel_offset` |
| 2 | ROV interlock / Early-Z | Requiere rebuild de `rexgpu-xenos.dll` desde `rexglue_sdk_src` + validación de semántica |
| 3 | Botones claros en el menú | `BS_OWNERDRAW` pendiente (~60 líneas) |
| 4 | Docs sin nodos en el grafo | Instalar backend LLM y `--update` |
| 5 | Cuarentena 1,7 GB | `C:\Users\katze\Downloads\_neverdead_cleanup_2026-11-02\` |
| 6 | `git push` sin hacer | `git remote add origin … && git push -u origin main` |

---

## 7. Evidencia en disco

| Archivo | Contenido |
|---|---|
| `dist/menu_f1_alpha_preview.png` | captura del menú final (520×320) |
| `dist/NeverDead_Recomp_v0.1.0_Alpha.zip` | release para el jugador (14,21 MB) |
| `docs/GRAFO_ARGON.txt` | grafo en texto (34 KB, 287 líneas) |
| `graphify-out/` | `graph.json`, `GRAPH_REPORT.md`, `graph.html` (699 nodos) |
| `patches/sdk/rex/ppc/context.h` | parche `fpscr`/`ldmxcsr` versionado |
| `tools/{bench_v6,package_alpha_release,preview_menu,export_graph_txt,extract_rex_cvars}.py` | arnés reproducible |
| `.workbuddy-ai/memory/2026-11-02.md` | bitácora de las 4 sesiones |

**No se tocó:** `neverdead.toml`, `rexruntime.dll`, `rexgpu-xenos.dll`, `src/crash_diag.cpp`,
`src/neverdead_app.h`, `generated/`, `assets/`.
**Modificado:** `src/nd_hooks.cpp` (menú), `src/nd_import_diag.h` (pin FTZ), header del SDK
(parche `fpscr`), `JUGAR_NEVERDEAD.bat` (mensaje), `.gitignore` y `README.md` (nuevos).
