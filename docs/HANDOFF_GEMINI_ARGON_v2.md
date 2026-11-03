# NeverDead × ReXGlue — Handoff v2 y preguntas para Gemini Argon

> **Actualización 2026-10-24 21:20:** este handoff se ejecutó. Resultados en
> `docs/INFORME_EJECUCION_2026-10-24.md`. Resumen: la instrumentación `[[midasm_hook]]`
> funciona (VdInitializeEngines 1 vez, VdSwap ~30 FPS), el chunk 0x8224CFF8 quedó
> resuelto, el VEH tuvo 0 activaciones y el juego entra a gameplay (HUD + pausa). El 3D
> in-game está bloqueado por un asset ausente (`envs/sp/sp_00_prologue.xbox360`), no por
> el recomp. Las preguntas de §11 siguen siendo válidas para la deuda estructural.
>
> **Para qué sirve este documento:** es un contexto autocontenido del estado real del
> proyecto al 2026-10-24 20:30, escrito para pegarlo en una sesión de Gemini Argon y
> pedirle criterio técnico. No requiere la conversación original.
>
> **Qué ha cambiado respecto al handoff v1 (`docs/HANDOFF_GEMINI_ARGON.md`):** aquel
> describía el árbol derivado de `NeverDead_base.exe` y un bug raíz de offsets. Ese
> camino está **abandonado**. El codegen ahora se hace desde `assets/default.xex`
> (shift 0) y el juego **ya muestra el título y llegó una vez al menú principal 3D**.
> El problema actual no es "no arranca": es **observabilidad, reproducibilidad y
> configuración declarativa a medias**.

---

## 0. TL;DR (lo que necesito de ti, Gemini)

Recompilación estática de **NeverDead** (Xbox 360, Konami/Rebellion, Asura Engine 2012)
a x64 con **ReXGlue 0.10.0.24-dev**. Estado:

- Pantalla de título: ✅ estable, renderizada, 40 s sin caída.
- **Menú principal 3D: ✅ capturado una vez** (personaje, luces, sombras, 200 FPS),
  **pero no se ha vuelto a reproducir** tras el rebuild de las 17:50.
- Gameplay 3D: ❌ no verificado.
- El motor **no falla por GPU**: el backend xenos/D3D12 arranca (36 shaders, 26→31
  pipelines) y presenta frames.

Tres problemas estructurales, todos verificados:

1. **Toda la capa de hooks está muerta.** Los hooks apuntan a `0x827BExxx`; los stubs
   reales que llama el código generado están en `0x827C0xxx` (**−0x2000**). Cero
   telemetría de `VdInitializeEngines` / `VdSwap`.
2. **El VEH del host repara fallos en caliente** (`crash_diag.cpp`): convierte AVs y
   divisones por cero en "continuar", así que un 3D roto puede parecer sano.
3. **La configuración declarativa está a medias:** `functions.toml` (19.982 entradas)
   **no está en el manifest** y el codegen no lo consume; `vtables.toml` (3.420) declara
   labels interiores como *chunks*; `gap_chunks.toml` deja 8 targets sin resolver.

**Lo que pido:** criterio sobre el mecanismo correcto de ReXGlue para (a) instrumentar
imports, (b) declarar entry points interiores que son targets de llamada indirecta,
(c) validar que hay 3D real y no un quad 2D, y (d) explicar qué tipo de cambio de
codegen puede romper una transición de menú sin AV. Preguntas concretas en §11.

---

## 1. Objetivo y criterio de éxito

**Objetivo de producto:** llegar al menú y ejecutar 3D.

**Definición de hecho (medible, no interpretable):**

| Nivel | Criterio | Estado |
|---|---|---|
| N0 | Ventana D3D12 + presentación de frames | ✅ |
| N1 | Pantalla de título estable ≥40 s | ✅ |
| N2 | Menú principal renderizado (2D o 3D) | ✅ una vez |
| N3 | Menú principal con **geometría 3D** (personaje/escena) | ✅ una vez (captura) |
| N4 | Transición menú → Single Player sin caída | ❌ |
| N5 | Frame in-game con depth buffer y draw calls > 0 | ❌ |

El objetivo del usuario ("menú + 3D") está cumplido a nivel N3 **una vez**. Lo que falta
es **N4/N5 y, sobre todo, que N3 sea reproducible y demostrable con telemetría**, no con
una captura aislada.

---

## 2. Stack y operación

| | |
|---|---|
| Raíz | `C:\Users\katze\Downloads\_Proyectos_y_Backups\neverdead_project` |
| SDK | ReXGlue 0.10.0.24-dev (`rexglue-v0.10.0.24-dev.gbd833a2-Release`) |
| Toolchain | Clang 23.1 + Ninja + MSVC 14.44.35207 + SDK 10.0.26100.0 |
| Backend | `xenos` sobre **D3D12**, NVIDIA RTX 2060 |
| Binario | `build_clang/neverdead.exe` (40.031.744 B, 2026-10-24 17:50:25) |
| Árbol compilado | `generated/neverdead_default/` — 121 TU, 108,4 MB (103,4 MiB), 24.591 funciones emitidas |
| Entrada de codegen | `assets/default.xex` (retail, 9.580.544 B) |
| Config | `neverdead_manifest.toml` → `config/{vtables,gap_chunks,crt,analysis,invalid_instructions}.toml` |
| Logs | `build_clang/logs/neverdead_*.log` (el más reciente: 067) |
| Capturas | `build_clang/after_start_*.png`, `build_clang/shots/` |

### 2.1 Build (el script normal no funciona en sandbox)

`build_neverdead.ps1` llama a `vcvarsall.bat` → `reg.exe`, bloqueado. Usar:

```bash
cd "C:/Users/katze/Downloads/_Proyectos_y_Backups/neverdead_project"
export MSVC="C:\\Program Files (x86)\\Microsoft Visual Studio\\2022\\BuildTools\\VC\\Tools\\MSVC\\14.44.35207"
export SDK="C:\\Program Files (x86)\\Windows Kits\\10"; export SDKV="10.0.26100.0"
export INCLUDE="$MSVC\\include;$SDK\\Include\\$SDKV\\ucrt;$SDK\\Include\\$SDKV\\shared;$SDK\\Include\\$SDKV\\um;$SDK\\Include\\$SDKV\\winrt;$SDK\\Include\\$SDKV\\cppwinrt"
export LIB="$MSVC\\lib\\x64;$SDK\\Lib\\$SDKV\\ucrt\\x64;$SDK\\Lib\\$SDKV\\um\\x64"
export PATH="/c/Program Files/LLVM/bin:$PATH"
cmake --build build_clang --config Release        # 4-7 min
```

También existen `tools/run_build.py` (wrapper de Ninja/Clang con entorno MSVC) y
`tools/profile_run.py`. **Ojo:** `run_game_watchdog.py` tiene rutas hardcodeadas al
directorio viejo de Antigravity (`.gemini/antigravity-ide/scratch/...`); no es portable.

### 2.2 Cómo se mide

```bash
L=build_clang/logs/neverdead_067.log
grep -c "graphics pipelines" "$L"
grep -c "VdInitializeEngines\|VdSwap" "$L"     # HOY SIEMPRE 0 (ver §5)
grep -c "XFileXctdCompressionInformation" "$L" # 148
grep -ciE "Unhandled|AV |exception" "$L"
```

**Aviso:** un `grep -c` sin coincidencias devuelve exit code 1; no lo interpretes como
fallo del build.

---

## 3. Arquitectura actual del runtime

```
default.xex (PPC big-endian)
      │  rexglue codegen (shift 0)
      ▼
generated/neverdead_default/  ── 121 TU, 24.591 funciones, 24.848 registros
      │  (cada import se emite como __imp__X(ctx, base))
      ▼
neverdead.exe = ReXApp (WindowedApp)
      ├─ OnPreSetup      gpu_plugin="xenos"
      ├─ OnLoadXexImage  fuerza game:\default.xex
      ├─ OnPostLoadXexImage
      │     ├─ MapBaseExeSections (sólo .data holes; .rdata/.pdata/.post OFF)
      │     ├─ pre-commit de arena física y heaps
      │     └─ probes de vtable / dtor-array / cs-table
      ├─ OnPostSetup     alias VFS (11)
      └─ VEH de crash_diag.cpp  ← repara fallos en caliente (ver §8)
```

**Lo que es declarativo:** límites de funciones y chunks (`vtables.toml`,
`gap_chunks.toml`), CRT (`crt.toml`), instrucciones inválidas, análisis.
**Lo que es parche en C++:** `MapBaseExeSections`, pre-commits, VEH, y una capa de hooks
que **no se ejecuta** (§5).

---

## 4. Estado medido (evidencia, no impresiones)

| Hecho | Evidencia | Fecha |
|---|---|---|
| Título renderizado y estable 40 s | `build_clang/after_start_40s.png` (1,33 MB) | 17:17:03 |
| **Menú principal 3D** (personaje, luces, 200 FPS) | `build_clang/shots/04_after_enter_6s.png` | **17:25:07** |
| Mismo renderer que el título (no es Xenia) | diff estructural título vs menú: 47,3 medio; título vs título: 4-5 | verificado |
| 26 pipelines en la corrida del menú | `logs/neverdead_051.log` | 17:25 |
| 28 pipelines | `logs/neverdead_052.log` | 17:26 |
| **31 pipelines** desde el rebuild | `logs/neverdead_053.log` … `067.log:76` | 17:50+ |
| Título de nuevo, sin menú, en 12+ intentos | `shots/{A1,A2,X,B1,M2,N1,P1,Q0,K1,R0,D1,D2}.png` | 19:02-19:35 |
| Mando Xbox 360 en slot 0 en todas las corridas | `logs/051,053,067` | — |
| `CESA.bik` ausente (0xC000000F), el motor continúa | `logs/067` | — |
| 148 errores `XFileXctdCompressionInformation` por corrida | `logs/067` | — |
| Sin shutdown limpio: el log corta a media línea | `logs/067` (239 líneas, 15 s) | — |

**Conclusión honesta:** N3 se alcanzó, pero **no hay una corrida reproducible que lo
demuestre**. La captura del menú es de las 17:25:07; el log de esa corrida
(`neverdead_051.log`) termina a las 17:25:04, tres segundos antes. La evidencia visual es
buena; la trazabilidad, inexistente.

---

## 5. Hallazgo A — La capa de hooks está muerta (−0x2000)

### 5.1 Qué se creía

`src/nd_import_diag.h` define hooks con `REX_HOOK_RAW(sub_827BE084)` para
`VdInitializeEngines`, `sub_827BDFD4` para `VdSwap`, etc., y logs `IMPORT-REAL` la primera
vez que se llaman. En **todas** las corridas: **0 líneas `IMPORT-REAL`**.

### 5.2 Qué pasa en realidad

El codegen emite los imports como `__imp__X(ctx, base)` y los registra en la tabla de
dispatch en direcciones **0x2000 por encima** de las que hookea el proyecto:

| Import | Hook del proyecto | Stub real en el árbol | Δ |
|---|---|---|---|
| `VdInitializeEngines` | `0x827BE084` (`nd_import_diag.h:432`) | `0x827C0084` (`neverdead_init.cpp:24791`) | **−0x2000** |
| `VdSwap` | `0x827BDFD4` (`nd_import_diag.h:579`) | `0x827BFFD4` (`neverdead_init.cpp:24780`) | **−0x2000** |
| `KeTlsSetValue` | `0x827BD8F4` (`nd_ke_tls_fix.h:67`) | `0x827BF8F4` (`neverdead_init.cpp:24668`) | **−0x2000** |

Comprobado: `grep 827BE084 generated/` → **0 coincidencias**. El símbolo `sub_827BE084`
existe en el `.obj` pero **nadie lo referencia**; el linker lo deja fuera. El código
generado llama directamente a `__imp__VdInitializeEngines` (`neverdead_recomp.7.cpp:22746`)
y a `__imp__VdSwap` (`neverdead_recomp.68.cpp:30946`).

### 5.3 Por qué es importante

- No se puede afirmar **ni negar** que el juego llame a `VdInitializeEngines`/`VdSwap`.
- El "3D funciona" se sostiene sólo por la captura, no por telemetría.
- Cualquier diagnóstico futuro sobre GPU es fe.

### 5.4 Dirección correcta (a validar con Gemini)

La wiki de ReXGlue dice: *"Every recompiled function is emitted as a weak alias to a
strong `__imp__`-prefixed implementation. You override a function by defining a strong
symbol with the same name."* Para imports, el SDK hookea el nombre `__imp__`
(`REX_HOOK_RAW(__imp__DbgPrint)` en `xboxkrnl_strings.cpp`). Por tanto el hook correcto
es **`__imp__VdSwap` / `__imp__VdInitializeEngines`**, no `VdSwap` / `sub_827BExxx`.

**Riesgo a preguntar:** redefinir `__imp__*` puede colisionar con la definición fuerte del
SDK según la granularidad del archivo objeto. ¿Cuál es el patrón sancionado?

---

## 6. Hallazgo B — El menú 3D se alcanzó una vez y no se reproduce

### 6.1 Cronología

| Hora | Evento | Pipelines |
|---|---|---|
| 17:15-17:17 | Título estable, capturas `after_start_*` | 26 |
| **17:25:00** | `01_title.png` | 26 |
| **17:25:07** | **`04_after_enter_6s.png` → MAIN MENU 3D** | 26 |
| 17:26:25 | `10_menu.png`: fondo del título, logo ausente, tinte rojo (frame de transición) | 28 |
| **17:45** | se añade `config/gap_chunks.toml` | — |
| 17:46-17:49 | regeneración del árbol (`_try`, `_prev`, `default`) | — |
| 17:50:25 | build actual | 31 |
| 17:51-19:35 | 12+ intentos: **siempre título**, nunca menú | 31 |

### 6.2 Hipótesis (no confirmadas)

- **H1 (codegen):** añadir `gap_chunks.toml` y regenerar cambió el árbol lo suficiente
  para romper la transición START → menú. Correlación temporal fuerte; causalidad **no
  probada**.
- **H2 (input):** la captura se hizo con Enter de teclado ("after_enter"); las corridas
  posteriores quizá sólo probaron el mando. El mando está conectado en todas, pero no
  sabemos si se pulsó START.
- **H3 (estado transitorio):** el menú de las 17:25 era un estado que el motor abandona
  solo (transición a otra pantalla) y por eso `10_menu.png` ya no lo muestra.

### 6.3 Test de discriminación (30 min, sin rebuild)

1. Ejecutar el build actual y enviar **START del mando (b7) y Enter** a t=5 s.
2. Capturar a t=8, 10, 15 s. Si aparece el menú → H2 (era input). Si no → H1/H3.
3. Repetir 3 veces para descartar carrera.

Si no aparece: rebuild **sin** `gap_chunks.toml` (dejar el resto igual) y repetir. Si
vuelve el menú, H1 confirmada y el bisect se reduce a esos 6 chunks.

**Deuda de método:** el build bueno de las 17:25 no se archivó. A partir de ahora,
guardar `neverdead.exe` + `generated/` + hash de configs por cada build que alcance un
hito.

---

## 7. Hallazgo C — La configuración declarativa está a medias

| Fichero | Qué contiene | ¿Lo usa el codegen? | Problema |
|---|---|---|---|
| `config/functions.toml` | **19.982** entradas (18.087 funciones + 1.895 chunks) | **NO** — no está en `neverdead_manifest.toml` ni en `codegen.d` | Semilla huérfana. El árbol se genera sólo con `.pdata` + `vtables.toml` + `gap_chunks.toml` |
| `config/vtables.toml` | **3.420** entradas `[functions]` con `parent`/`end`/`name="vtable_slot_*"` | Sí | Usa el mecanismo de *chunks* para hacer llamables labels interiores. La doc dice: *"Chunks are not entry points themselves"* |
| `config/gap_chunks.toml` | 6 entry points interiores | Sí | `0x8224CFF8` termina en `0x8224D048`, exactamente el target de una rama → **unresolved** |
| `config/crt.toml` | 4 mapas `[rexcrt]` (memcpy/memset/strchr/strncpy) | Sí | Correcto, verificado |
| `config/invalid_instructions.toml` | 4 regiones de datos | Sí | Correcto, verificado |

**Unresolved del último codegen** (`build_clang/codegen_gapchunks.log`, 33 líneas de
error, 8 targets únicos):

```
0x8224D048 (conditional branch desde 0x8224D000)
0x8278C3A8  0x8278CB30  0x8278F22C  0x82793AF0
0x82797B8C  0x8279952C  0x82799548
```

Los siete últimos están en la zona de helpers del CRT/runtime (`0x8278-0x8279`). Si
alguno se alcanza en runtime, el dispatcher lanzará `Call to invalid or unregistered
function`. **No sabemos si están en código muerto.**

**Pregunta de fondo para Gemini:** ¿cuál es el mecanismo sancionado para declarar un
entry point interior que es target de llamada indirecta y vive dentro del extent `.pdata`
de otra función? La doc de `functions` dice que los chunks no son entry points y que las
funciones standalone no pueden solaparse. El proyecto resolvió esto "haciendo chunks
llamables", que funciona pero no es lo que dice la doc.

---

## 8. Hallazgo D — El VEH enmascara fallos

`src/crash_diag.cpp` (activo, compilado en `CMakeLists.txt:36`) hace dos cosas:

1. **Divide-by-zero del worker de GPU** (`0xC0000094`): si el opcode es `div ecx`, pone
   `rax=rdx=0`, escribe ceros en `[rbp+0x4C/0x50]` y salta 19 bytes
   (`crash_diag.cpp:47-69`). Comentario: *"Bypassed GPU divide-by-zero on uninitialized
   ringbuffer"*.
2. **Access violations** en la arena `[0x200000000, 0x400000000)`: hace `VirtualProtect` o
   commitea 16 MB y **continúa** (`crash_diag.cpp:73-124`).

Consecuencia: un 3D que escribe fuera de rango o divide por cero **no se ve**. El juego
"sobrevive" y la captura puede parecer sana. Para el handoff: **el VEH debe pasar a
log-only** (o a assert) antes de sacar conclusiones de estabilidad.

---

## 9. Activos, FMV y los tres ZIP que se adjuntaron

### 9.1 `CESA.bik` no existe en este SKU

- `assets/FMV/` tiene **27 `.bik`** standalone (magic `BIKi`), ninguno es `CESA.bik`.
- `default.xex.bak` contiene la cadena `GAME:\FMV\*.bik`: el intro se busca por **wildcard
  sobre archivos sueltos**, no dentro de un archivo.
- Barrido de los 54 `.asrBE`: **no hay `CESA` ni `.bik`** (el único `CESA` es el widget
  `CESA_Warning` de `loadingInitial.guiBE`).
- **Veredicto:** no es un bug de path; el archivo no está y no es recuperable de los
  assets. El motor ya cae al título. Perseguirlo es whack-a-mole.

### 9.2 `.asrBE` es RSFL **big-endian** (no lo que espera el extractor)

Los 54 `.asrBE` empiezan por `"Asura   "` + fourcc invertido (`RSFL`→`LFSR`,
`RSCF`→`FCSR`), pero la tabla de entradas es little-endian y no hay header RSFL interno.
El extractor Python (`AsuraEngineExtractor`) espera RSFL little-endian y falla con
`corrupted RSFL inner header`. Un parser de ~40 líneas sí resuelve los 54 (verificado
sobre 3 muestras: `Fonts.asrBE` 3 entradas, `Common.asrBE` 12, `PrimitiveRenderFonts` 1).

**Pero:** `assets/` sólo contiene fuentes, layouts de GUI, textos y audio. **No hay
geometría, ni shaders, ni código.** Extraer assets no desbloquea el 3D.

### 9.3 Los tres ZIP

| ZIP | Contenido | Veredicto |
|---|---|---|
| `quickbms.zip` | `quickbms.exe` 0.12 | **Útil más tarde.** Su `asura.bms` entiende `RSCF`/`ASTS` (banco de sonido), pero compara fourcc literales y falla con los invertidos; necesita 4 constantes parcheadas |
| `quickbms_scripts.zip` | 2.745 scripts, incl. `asura.bms` | **Útil más tarde** por `ASTS`; no aporta al 3D |
| `AsuraEngineExtractor.1.zip` | Python + GUI, RSFL LE | **Útil más tarde.** Modelo conceptual correcto, endianness equivocada; serviría para un fork BE |
| `Sniper.Elite.V2-SKIDROW/sr-sev2.iso` | 5,5 GB, **instalador** SKIDROW (`Setup.exe` + `Setup-1a.bin`…) | **No útil** sin extraer el ISO; no hay `.asr`/`.pc` en la raíz |

Ninguno de los cuatro desbloquea N4/N5. El 3D depende de `default.xex` y de los
archivos de nivel que no están en `assets/`.

---

## 10. Reglas del proyecto (vibe coding, sin whack-a-mole)

1. **ReXGlue first.** Todo lo que se pueda expresar en `config/*.toml` va ahí; el C++ del
   host sólo para infraestructura (memoria, VFS, logging).
2. **Prohibido** `ctx.r3.u64 = 0/1` para silenciar. Prohibido tocar
   `generated/neverdead_default/*.cpp`. Prohibido parchear el binario a mano.
3. **Un cambio por build.** Un build son 4-7 min; se diseña completo, se revisa en
   estático y se compila una vez.
4. **Medir antes/después** con el mismo comando y el mismo tiempo de ejecución. Sin
   métrica, no hay cambio.
5. **Archivar cada build que alcance un hito** (exe + árbol + hash de configs). El build
   bueno de las 17:25 se perdió y eso es la causa de la mitad de la incertidumbre actual.
6. **Un log que no distingue "no se llamó" de "no se instrumentó" no es evidencia.**
   Arreglar la observabilidad es P0, antes que cualquier feature.

---

## 11. Preguntas concretas para Gemini Argon

> Responder por número, con la API/macro exacta cuando exista. Si algo no está
> documentado, decirlo y marcar la hipótesis.

1. **Hooks de imports.** El código generado llama `__imp__VdSwap(ctx, base)` directamente.
   ¿Cuál es el patrón canónico para interceptarlo: `REX_HOOK_RAW(__imp__VdSwap)` con
   `REX_EXTERN(__imp__VdSwap)`, o hay una tabla de overrides en TOML? ¿Cómo se evita la
   colisión con la definición fuerte del SDK?
2. **Observabilidad sin tocar cada stub.** ¿Hay un punto único (resolver de imports,
   `FunctionDispatcher`, command processor del backend xenos) donde instrumentar todas las
   llamadas a `Vd*` y contar presents/draws sin hookear 40 símbolos?
3. **Entry points interiores.** La doc de `[functions]` dice que `parent` define *chunks* y
   que *"chunks are not entry points themselves"*; también que las funciones standalone no
   pueden solaparse. Entonces, ¿cómo se declara un label interior que **sí** es target de
   `bctrl`/`bl` desde fuera? ¿`size`/`end` standalone aunque solape? ¿`midasm_hook`? ¿O el
   proyecto está abusando de `parent` y funciona por accidente?
4. **Fragmentos de epílogo compartido.** `0x824DDF90` se alcanza con `bctrl` pero no es
   entrada `.pdata`: es un fragmento de epílogo (`addi r3,r5,0x64 … addi r1,r1,0x80; lwz
   r12,-8; mtlr; blr`). ¿Se declara como función, como chunk del padre, o se intercepta?
   ¿Cómo distinguir un thunk legítimo de un fragmento no llamable?
5. **`functions.toml` huérfano.** Hay 19.982 entradas construidas desde `.pdata` que el
   codegen **no consume** (no están en el manifest). ¿Deberían incluirse en un codegen
   desde XEX con shift 0, o son un riesgo de fronteras obsoletas? ¿Cómo validarlas contra
   `.pdata` antes de incluirlas?
6. **8 unresolved.** `0x8224D048`, `0x8278C3A8`, `0x8278CB30`, `0x8278F22C`, `0x82793AF0`,
   `0x82797B8C`, `0x8279952C`, `0x82799548`. ¿Cuál es el patrón correcto para resolverlos
   sin parchear el generado? ¿Cómo saber si están en código muerto antes de invertir tiempo?
7. **Validar 3D real.** ¿Qué métricas expone el backend xenos/D3D12 para probar que hay
   geometría (draw calls, depth buffer, PM4 packets, presents) y no un quad 2D? ¿Se puede
   volcar un frame o un contador sin instrumentar el juego?
8. **La regresión del menú.** ¿Qué clase de cambio de codegen puede hacer que una
   transición de estado de UI funcione una vez y deje de funcionar sin AV? ¿Dónde mirar
   primero: polling de input, timers, orden de init, slots de vtable, o el propio árbol
   regenerado?
9. **El VEH.** ¿Es aceptable como muleta de desarrollo? ¿Cómo pasar a log-only sin que el
   juego muera en el primer frame, y qué métrica usar para distinguir "no falla" de "falla
   reparado"?
10. **Referencias.** ¿Conoces algún proyecto ReXGlue con Asura Engine o con funclets SEH de
    MSVC (`share_registers`) que sirva de patrón? Sabemos que no hay NeverDead recompilado;
    buscamos arquitectura, no receta.
11. **Si tuvieras 2 horas** con este estado exacto, ¿qué tres cosas harías en orden y qué
    medirías después de cada una?

---

## 12. Plan propuesto (48 h, sujeto a lo que responda Gemini)

| Fase | Tiempo | Acción | Medición |
|---|---|---|---|
| P0 | 30 min | Congelar evidencia: archivar exe + árbol + configs; correr 3× con input determinista (pad START + Enter a t=5 s) | ¿Aparece el menú? 3/3 o 0/3 |
| P1 | 1 h | Arreglar observabilidad: hook `__imp__VdSwap` / `__imp__VdInitializeEngines` con contador; log de present | ≥1 línea por import en 10 s |
| P2 | 1 h | VEH a log-only (sin reparar); correr 10 s | Nº de AVs reales (hoy ocultos) |
| P3 | 2 h | Bisect de la regresión: build sin `gap_chunks.toml`; build con `functions.toml`; comparar | Menú sí/no + pipelines |
| P4 | 2 h | Resolver los 8 unresolved con el mecanismo que indique Gemini | 0 unresolved en codegen |
| P5 | — | Validar N4/N5: menú → Single Player → frame in-game | Draw calls > 0, depth buffer, captura |

**Orden innegociable:** P1 y P2 antes de P3. Sin telemetría, el bisect es a ciegas.

---

## Anexo A — Evidencias citadas

| Afirmación | Fichero / línea |
|---|---|
| Hook `VdInitializeEngines` en 0x827BE084 | `src/nd_import_diag.h:432` |
| Hook `VdSwap` en 0x827BDFD4 | `src/nd_import_diag.h:579` |
| Stub real `VdInitializeEngines` 0x827C0084 | `generated/neverdead_default/neverdead_init.cpp:24791` |
| Stub real `VdSwap` 0x827BFFD4 | `generated/neverdead_default/neverdead_init.cpp:24780` |
| Llamada directa del generado | `generated/neverdead_default/neverdead_recomp.7.cpp:22746`, `neverdead_recomp.68.cpp:30946` |
| 31 pipelines | `build_clang/logs/neverdead_067.log:76` |
| 26 pipelines (menú) | `build_clang/logs/neverdead_051.log` |
| `functions.toml` no consumido | `neverdead_manifest.toml:10-16`, `generated/neverdead_default/codegen.d` |
| 3.420 vtables / 19.982 functions | `grep -c '^\[functions\.' config/*.toml` |
| Unresolved codegen | `build_clang/codegen_gapchunks.log` |
| VEH repara div-0 | `src/crash_diag.cpp:47-69` |
| VEH repara AVs | `src/crash_diag.cpp:73-124` |
| `nd_main_loop.h` no se incluye | `grep -rn "nd_main_loop" src/ generated/` → 0 |
| Menú 3D | `build_clang/shots/04_after_enter_6s.png` (17:25:07) |

## Anexo B — Referencias

- ReXGlue SDK: https://github.com/rexglue/rexglue-sdk
- Function Overrides (wiki): https://github.com/rexglue/rexglue-sdk/wiki/Function-Overrides
- Generated Code Structure: https://github.com/rexglue/rexglue-sdk/wiki/Generated-Code-Structure
- Config de funciones: https://mintlify.wiki/rexglue/rexglue-sdk/config/functions
- Config de codegen: https://mintlify.wiki/rexglue/rexglue-sdk/config/codegen-config
- Lista de recompilaciones: https://github.com/abduznik/awesome-x360-recomps
- Patrones transferibles: `zolaware/reblue`, `florinp93/hells-gate-recomp`
- Documentos internos: `docs/AUDIT_BOOT_AND_3D.md`, `docs/BUG_RAIZ_OFFSET_DECODIFICACION.md`,
  `docs/REVIEW_INFORME_2026-10-24.md`, `.workbuddy-ai/memory/2026-10-23.md`, `2026-10-24.md`

## Anexo C — Glosario mínimo

- **XEX / XEX2**: ejecutable firmado de Xbox 360; el runtime carga `assets/default.xex`.
- **`.pdata`**: tabla de funciones (begin/length) del binario PPC; fuente de verdad de
  fronteras.
- **`__imp__X`**: implementación fuerte que emite ReXGlue para un import o función; el
  código generado la llama directamente.
- **Chunk**: región de código declarada con `parent`; la doc dice que no es entry point.
- **Funclets SEH**: trozos de código que leen registros vivos del padre; en ReXGlue se
  marcan con `share_registers`.
- **PM4**: formato de paquetes del mando de GPU de Xbox 360; el backend xenos los traduce
  a D3D12.
- **VEH**: Vectored Exception Handler de Windows; `crash_diag.cpp` lo usa para reparar
  fallos en caliente.
