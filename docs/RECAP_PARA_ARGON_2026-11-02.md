# INFORME PARA ARGON — NeverDead PC Recompilation
**Fecha:** 3 de noviembre de 2026 · **Versión:** v9 (sustituye a v8; autocontenido)
**Binario:** `build_clang/neverdead.exe` (01:22, 38.836.224 B) · **Commit:** `0319c10`
**Grafo:** 701 nodos · 861 aristas · 35 comunidades · `docs/GRAFO_ARGON.txt` (34 KB, 291 líneas)

---

## 0. Resumen ejecutivo

| Tema | Estado | Evidencia |
|---|---|---|
| Branding NEVERDETH con el logo **original del autor** | **HECHO** | §1 · `docs/assets/` |
| Botones owner-draw carbón/bronce (adiós Windows 95) | **HECHO + captura** | §2 |
| Banner en el menú + icono de ventana | **HECHO** | §2.3 |
| Parche RAM Early-Z en `rexgpu-xenos.dll` | **INNECESARIO — el early-Z ya está activo** | §3 |
| Repo + ZIP de release | **HECHO** (206 archivos, 3,42 MB · ZIP 14,71 MB) | §4 |
| Grafo + TXT para Argon | **ACTUALIZADO** | §5 |

---

## 1. Branding: el logo real, no uno generado

**Corrección de un error mío de v8:** el autor ya tenía los logos en la raíz del proyecto
desde el 24 de octubre; yo generé uno falso sin buscarlos.

| Archivo fuente (raíz, **no** versionado) | Uso |
|---|---|
| `neverdeth_logo_transparent.png` (2066×550 RGBA) | máster → `docs/assets/neverdeth_logo.png` (1400 px) |
| `neverdeth_logo_transparent_4K.png` (4200×1118) | máster 4K, sin usar todavía (HiDPI futuro) |

`tools/make_branding.py` deriva del máster:

- **`docs/assets/neverdeth_logo.png`** — 488 KB, 1400 px de ancho (el README lo muestra a 480).
- **`docs/assets/neverdeth_logo_banner.bmp`** — 520×44, logo a 40 px centrado + dos reglas
  bronce flanqueándolo. **Es BMP a propósito:** `LoadImage` no carga PNG con alfa y el BMP
  lleva el carbón `#16161C` exacto del menú, así que se funde con el fondo sin GDI+.
- **`config/neverdeth.ico`** — monograma `N` en bronce con marco y línea roja, 6 tamaños.

**Por qué el icono NO es el wordmark:** se probó primero y a 32 px era una mancha dorada
ilegible. El monograma se lee a 16 px. Es una decisión de legibilidad, no de estilo.

`.gitignore` con las excepciones para que Git versione el branding:
`!docs/assets/*.png`, `!docs/assets/*.bmp`, `!config/*.ico`, `!config/*.bmp`.

---

## 2. Menú F1 v2 (owner-draw)

### 2.1 Geometría y banner

- Título `NeverDeth — Ajustes (F1)`, cliente **520×370** (antes 320).
- Banner en `y=10..54`, pintado en `WM_ERASEBKGND` con `BitBlt` desde el BMP.
- Todos los controles desplazados **+45 px** (combos 61, presets 99, casillas 143..247,
  botones 287).
- Icono de ventana por `WM_SETICON` (small + big).

### 2.2 Botones `BS_OWNERDRAW`

Los 4 botones llevan `BS_PUSHBUTTON | BS_OWNERDRAW` y se pintan en `WM_DRAWITEM`:

| Elemento | Valor |
|---|---|
| Fondo normal | `RGB(32, 29, 27)` — `#201D1B` |
| Fondo pulsado (`ODS_SELECTED`) | `RGB(48, 40, 32)` — `#302820` |
| Borde perfiles / volver | `RGB(200, 138, 50)` — bronce `#C88A32`, 1 px |
| Borde "Salir al Escritorio" | `RGB(180, 70, 60)` — rojo `#B4463C`, 1 px |
| Texto | `RGB(242, 225, 195)` — crema `#F2E1C3`, `DT_CENTER \| DT_VCENTER \| DT_SINGLELINE` |

Resultado: **cero botones blancos**. Los combos siguen con la cara del tema de Windows —
son `COMBOBOX`, no botones, y `WM_CTLCOLORLISTBOX` solo afecta a la lista desplegable.

### 2.3 Verificación en vivo

```
F1 abre: OK (visible=True)
captura 520x370
F1 con foco en el menu -> cerrado: True
F1 reabre: True
ESC cierra: True
```

Captura en `dist/menu_f1_alpha_preview.png` y `docs/assets/menu_f1_preview.png` (23 KB).

---

## 3. Early-Z en ROV: no hay nada que parchear

**Esto contradice la premisa de la tarea y conviene que quede claro.**

### 3.1 El código de `pipeline_cache.cpp:885` no se ejecuta en ROV

```cpp
// pipeline_cache.cpp:877
if (render_target_cache_.GetPath() == RenderTargetCache::Path::kHostRenderTargets) {
  ...
  // :885
  if (shader.implicit_early_z_write_allowed() && ...)
    modification.pixel.depth_stencil_mode = DepthStencilMode::kEarlyHint;
```

`neverdead.toml` usa `render_target_path_d3d12 = "rov"` → **ese bloque nunca entra**.
Parchear ese branch en RAM no cambiaría nada en nuestra ruta.

### 3.2 El early-Z de ROV va por otro mecanismo, y ya está encendido

```cpp
// command_processor.cpp:3703
if (edram_rov_used && depth_stencil_enabled) {
  ...
  // :3721  "Hint - if not applicable to the shader, will not have effect."
  if (alpha_test_function == xenos::CompareFunction::kAlways &&
      !rb_colorcontrol.alpha_to_mask_enable) {
    flags |= DxbcShaderTranslator::kSysFlag_ROVDepthStencilEarlyWrite;   // :3724
  }
}
```

Se consume en `dxbc_translator_om.cpp:958` y `:974`
(`if (depth_stencil_early && !implicit_early_z_write_allowed())` → marca el bit de
"escribir tarde" en `system_temp_rov_params_.x`).

**Conclusión:** para geometría opaca (alpha test `kAlways`, sin alpha-to-mask) el hint de
early-Z **ya se emite**. No se hizo parche en RAM porque no hay nada que ganar y sí riesgo de
crash al cazar un patrón de bytes sin símbolos para una rama que no se ejecuta.

**Implicación para el 95-99 % de GPU:** no es falta de early-Z; es la **serialización del
interlock ROV** en sí. Cerrarlo exige reconstruir `rexgpu-xenos.dll` desde `rexglue_sdk_src`
con otra estrategia de eDRAM (host-RT o ROV con resolves por tile), no un parche de 3 bytes.

---

## 4. Repo y release

```
git log → 0319c10 branding: logo NEVERDETH original (2066x550) en README, banner del menu e icono
          5f9d9ca Alpha v0.1.0: NeverDeth UI Dark/Bronze, SDK fpscr patch & Alpha tooling
git status --short → (vacío)
206 archivos · 3,42 MB
```

| Artefacto | Tamaño |
|---|---|
| `dist/NeverDead_Recomp_v0.1.0_Alpha.zip` | **14,71 MB · 23 archivos** (incluye `docs/assets/`) |
| `docs/assets/neverdeth_logo.png` | 488 KB |
| `docs/assets/neverdeth_logo_banner.bmp` | 68 KB |
| `config/neverdeth.ico` | 10 KB |
| `docs/assets/menu_f1_preview.png` | 23 KB |

Fix aplicado al packager: el guard confundía `docs/assets/` con el `assets/` de la raíz y
abortaba el ZIP. Ahora solo prohíbe el `assets/` raíz por prefijo.

---

## 5. Grafo actualizado

| Métrica | v8 | v9 |
|---|---|---|
| Nodos | 699 | **701** |
| Aristas | 858 | **861** |
| Comunidades | 33 | **35** |
| `GRAFO_ARGON.txt` | 287 líneas | **291 líneas (34 KB)** |

Nodos nuevos del turno ya presentes: `NdDrawBanner()`, `NdApplyPreset()`, `NdConfigWndProc()`
(re-extraídos de `src/nd_hooks.cpp`).

El grafo sigue **enfocado al código versionado**: `.gitignore` excluye `/generated/`, así que
son 701 nodos legibles y no los 25,5 k del codegen.

**La comunidad `[4]` sigue siendo la mina para el problema abierto:**
*"ROV overdraw saturation at 95-99% GPU"*, *"Xenos color_exp_bias −= 5 (divide by 32) vs shader
×32 multiplier"*, *"eDRAM emulation strategy question (compensate bias vs native ROV)"*.

**Limitación:** los documentos nuevos siguen sin nodos semánticos (backend LLM no instalado).
Para completarlo: `uv tool install "graphifyy[anthropic]" --force` y `graphify . --update`.

---

## 6. Riesgos abiertos

| # | Riesgo | Estado |
|---|---|---|
| 1 | Coste GPU 720p | **Abierto.** Descartado early-Z (§3). La hipótesis viva es el interlock ROV y el `color_exp_bias` |
| 2 | Combos con cara clara | Cosmético; requeriría owner-draw del combo (no soportado nativamente) |
| 3 | Icono a 16 px | Monograma legible, pero es una aproximación, no el wordmark |
| 4 | Docs sin nodos en el grafo | Instalar backend LLM |
| 5 | Cuarentena 1,7 GB | `C:\Users\katze\Downloads\_neverdead_cleanup_2026-11-02\` |
| 6 | `git push` sin hacer | `git remote add origin … && git push -u origin main` |

---

## 7. Evidencia

| Archivo | Contenido |
|---|---|
| `dist/menu_f1_alpha_preview.png` | menú final (520×370, banner + botones bronce) |
| `dist/NeverDead_Recomp_v0.1.0_Alpha.zip` | release (14,71 MB) |
| `docs/GRAFO_ARGON.txt` | grafo en texto (34 KB) |
| `docs/assets/neverdeth_logo.png` · `neverdeth_logo_banner.bmp` | branding derivado del máster |
| `config/neverdeth.ico` | icono 6 tamaños |
| `tools/make_branding.py` | regenera el branding desde el máster |
| `patches/sdk/rex/ppc/context.h` | parche `fpscr`/`ldmxcsr` versionado |
| `.workbuddy-ai/memory/2026-11-02.md` | bitácora de las 5 sesiones |

**No se tocó:** `neverdead.toml`, `rexruntime.dll`, `rexgpu-xenos.dll`, `src/crash_diag.cpp`,
`src/neverdead_app.h`, `generated/`, `assets/`.
**Modificado:** `src/nd_hooks.cpp` (menú/banner/owner-draw/icono), `README.md`, `.gitignore`,
`tools/{make_branding,package_alpha_release,export_graph_txt}.py`.
