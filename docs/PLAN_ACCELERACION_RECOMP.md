# Plan de aceleración — NeverDead (ReXGlue 0.10.0.24-dev)

Extraído de `https://github.com/abduznik/awesome-x360-recomps` (≈70 proyectos) + wiki de
ReXGlue + Xenia + código de este repo. Fecha: 2026-10-23.

---

## 0. Reencuadre: la meta estaba mal medida

Dato del último run (`build_clang/logs/neverdead_004.log`, 27 187 líneas):

```
[14:11:58.030] neverdead: configured gpu_plugin = 'xenos' for D3D12 presentation
[14:11:58.296] DXGI adapter: NVIDIA GeForce RTX 2060
[14:11:58.724] Direct3D 12 device and OS features: ... ROV: yes, tiled resources: tier 4
```

**El dispositivo D3D12 del host ya se crea a los 0,7 s.** Por tanto "llegar a
`VdInitializeEngines`" no es el hito de D3D12: es un hito *invitado*. Según Xenia
(`src/xenia/kernel/xboxkrnl/xboxkrnl_video.cc`), `VdInitializeEngines` es `kStub` y
devuelve 1 sin tocar la GPU. Lo que sí es real:

| Export | Qué hace en Xenia | Consecuencia para nosotros |
|---|---|---|
| `VdInitializeEngines` | stub → `return 1` | No esperar que "arranque D3D". Puede devolver 1 y el juego seguir. |
| `VdInitializeRingBuffer` | `GraphicsSystem::InitializeRingBuffer(phys, log2_size)` | **Hito real**: sin esto no hay PM4. |
| `VdSwap` | escribe fetch-constant + paquete `XE_SWAP` | **Hito real**: primer frame. |
| `VdSetDisplayMode` | stub → `return 0` | El juego puede validarlo; no reconfigura nada. |
| `VdGetSystemCommandBuffer` | ceros + marcadores `0xBEEF0000/1` (`kStub`, "sketchy") | Candidato a fallo silencioso si el juego valida el contenido. |
| `VdInitializeScalerCommandBuffer` | rellena `0x80000000` (NOP) (`kSketchy`) | Idem. |

Y el mapa de referencia que publica Dante's Inferno
([`docs/NATIVE_D3D_MAP.md`](https://github.com/florinp93/hells-gate-recomp/blob/main/docs/NATIVE_D3D_MAP.md))
etiqueta exactamente:

- `Direct3D_CreateDevice` ≡ ruta de `VdInitializeEngines`
- `D3DDevice_Swap` ≡ `VdSwap`

Es decir: la secuencia invitado es **crear dispositivo D3D del invitado →
`VdSetGraphicsInterruptCallback` → `VdInitializeRingBuffer(phys, log2)` → PM4 → `VdSwap`**.
Nuestro `sub_826BA390` (`VdInitializeEngines`) es solo el primero de cinco pasos, y encima
es el único que es stub.

### Hitos a medir (sustituyen a "¿llegó a VdInitializeEngines?")

| # | Hito | Cómo se mide |
|---|---|---|
| M0 | Entrada del XEX / WINMAIN | ya logueado |
| M1 | Heap/imagen/VFS listos | ya logueado |
| M2 | Dispositivo D3D12 host | ya logueado (0,7 s) |
| M3 | `Direct3D_CreateDevice` del invitado | hook en el import/thunk |
| M4 | `VdSetGraphicsInterruptCallback` | hook |
| M5 | `VdInitializeRingBuffer` (con `phys` ≠ 0) | hook ya existe (`sub_827BDFA4`) |
| M6 | Primer lote PM4 emitido (ring write) | contador en `GraphicsSystem` |
| M7 | `VdSwap` #1 | hook ya declarado (`__imp__VdSwap`) |
| M8 | `Translated N shaders`, N > 0 | log del plugin xenos |
| M9 | Primer frame presentado | log `swap`/present |

Hoy: M0 ✅, M1 ✅, M2 ✅, M3–M9 ❌.

---

## 1. Diagnóstico del bloqueo actual (evidencia, no hipótesis)

### 1.1 La cadena de fallo, en orden

```
neverdead: WINMAIN 0 ... lr=0x00000000
neverdead: BDFAM-P entry sub_8278BD4C seq=1 r1=0x70190000 lr=0x824D9E00
neverdead: BDFAM-P entry sub_8278BD60 seq=1 r1=0x70190000 lr=0x824D9E00
[error] Unhandled guest access violation: read of guest 0x70190050
neverdead: BDFAM-P exit sub_8278BD60 seq=1 r1=0x70190070   <-- +0x70 sin haber restado
...
neverdead: BD98 epilogue return r3=0xBEBEBEBE lr=0x822032FC
...
neverdead: BDFAM-P entry sub_8278BD9C seq=4 r1=0x70190590 lr=0xBEBEBEBE
neverdead: TRAP-OOB caller lr=0x821E8E38 target=0x8205970C (x2.7e9 en 18 s)
```

Dos hechos duros:

1. **`r1` sube**: entra a `0x70190000` y sale a `0x70190070`; diez llamadas después ya
   está en `0x70190590`. La pila se aleja del frame al que pertenece.
2. **`lr` llega a `0xBEBEBEBE`** (patrón de relleno de MSVC), o sea: alguien restauró el
   LR desde una posición que nadie escribió.

### 1.2 Por qué: es una cola compartida plegada por el linker

Desensamblado real del invitado (`tools/bd_truth.py` + Capstone, imagen `NeverDead_base.exe`):

```
0x8278BD38  bl   0x82788A40        ; free/delete helper (el que tiene nuestro guard)
0x8278BD3C  li   r31, 0
0x8278BD40  b    0x8278BD60
0x8278BD44  li   r4, 0
0x8278BD48  bl   0x827BD8F4        ; thunk de import xboxkrnl
0x8278BD4C  b    0x8278BD60
0x8278BD50  addi r11, r31, -1
0x8278BD54  subfic r11, r11, 0
0x8278BD58  subfe r11, r11, r11
0x8278BD5C  and  r31, r11, r31
0x8278BD60  mr   r3, r29
0x8278BD64  bl   0x826D0798       ; destructor
0x8278BD68  mr   r3, r31
0x8278BD6C  addi r1, r1, 0x70     ; <-- pop de un frame que NO creó esta región
0x8278BD70  b    0x8278AD9C       ; __restgprlr_29
0x8278BD98  bl   0x8278BBE8       ; destructor
            mr r3,r31 ; addi r1,r1,0x60 ; lwz r12,-8(r1) ; mtlr r12 ; ld r31,-0x10(r1) ; blr
```

No son funciones. Son **fragmentos de cola plegados** (COMDAT folding) que se ejecutan
*en el frame de quien los llama*. `0x8278BD38/BD44/BD50` son los tres brazos de un
`if/else` de un destructor vectorial; todos caen en `0x8278BD60`, que hace el pop y salta
al `__restgprlr_29` del CRT. `0x821FFC00` es el mismo caso: **su primera instrucción es
`bctrl`**, o sea que asume `r27/r30/r31/r1` de un padre.

Regla que se deriva: **este código solo es correcto si `r1`, `r29`, `r31` son los del
llamante**. Nuestro árbol v1 los emite con `REX_FUNC_PROLOGUE()` como funciones normales;
peor aún, `apply_bd_family_faithful.py` sustituye `0x8278BD98` por un "retorno limpio" que
**se salta el destructor y el pop de `0x60`**. Eso es mentirle al invitado, y explica el
`lr` basura y el AV en `0x70190050`.

### 1.3 El modelo correcto ya existe en el repo… pero no se usa

`config/functions.toml` (1,9 MB, 33 961 entradas) ya declara 15 457 chunks con `parent`,
incluidos:

```toml
[functions."0x8278BD3C"]  parent = 0x8278BD38  end = 0x8278BD48
[functions."0x8278BD4C"]  parent = 0x8278BD38  ...
[functions."0x8278BD60"]  parent = 0x8278BD38  share_registers = true
[functions."0x8278BD98"]  parent = <padre>     share_registers = true
```

y el manifiesto v2 (`neverdead_manifest_v2.toml`) ya incluye las 5 capas
(`functions.toml`, `vtables.toml`, `crt.toml`, `analysis.toml`, `invalid_instructions.toml`).
**Pero estamos compilando el árbol v1** (`generated/neverdead_default`), que viene del seed
viejo (1 894 tamaños en `nd_seed_overrides_v2.toml`) parcheado a mano con 30+ `apply_*.py`.

Matiz importante verificado: el codegen de 0.10 emite `REX_FUNC_PROLOGUE()` también para
los chunks de `parent`, así que `parent` **no** cambia la convención de llamada; lo que
cambia es el grafo (no parte la función, no inventa límites, conserva `share_registers`).
La corrección final del frame sigue dependiendo de que el *llamante* tenga el frame de
0x70/0x60 — y eso es lo que hay que verificar.

---

## 2. Qué copiar de cada recomp (extracción de la lista)

### 2.1 Los 10 hallazgos que más aceleran *este* port

| # | Fuente | Qué copiar | Por qué |
|---|---|---|---|
| 1 | [Dante's Inferno](https://github.com/florinp93/hells-gate-recomp) `docs/NATIVE_D3D_MAP.md` | El mapa `Direct3D_CreateDevice ↔ VdInitializeEngines`, `D3DDevice_Swap ↔ VdSwap`, y su diseño "se reemplaza la D3D estática del invitado, el juego sigue corriendo igual" | Nos da los hitos reales en vez de "llegar a una función". |
| 2 | [Dead Rising 2 Case Zero](https://github.com/wivi514/Dead_Rising_2_Case_Zero_Xenon_Recomp) | `tools/find_dropped_branches.py`, `fix_switch_function_bounds.py`, `coverage_to_function_overrides.py` + `docs/xenonrecomp-upstream-bugs.md` | Es *exactamente* nuestra clase de bug: ramas que caen fuera de la función inferida, `bctr` que corta el extent, colas compartidas. |
| 3 | [XenonRecomp issue #117](https://github.com/hedge-dev/XenonRecomp/issues/117) | "declarar límites de función" cuando dos ramas incondicionales contiguas se parten en funciones que no resuelven | Mismo síntoma que `0x821FFC00` y `0x8278BD38`. |
| 4 | [ReXGlue wiki — CLI Configuration](https://github.com/rexglue/rexglue-sdk/wiki/rexglue-CLI-Configuration-File) | `[functions."0x..." ]` con `size`/`end`/`parent`, `[[switch_tables]]` (`bctr`, registro índice, labels), entry points manuales | Es el mecanismo soportado para arreglar el grafo sin tocar C++ generado. |
| 5 | [Fable II (Oery) runbook](https://github.com/Oery/fable-ii-recomp/blob/main/docs/re/iteration-loop.md) | Disciplina: solo registrar un target no registrado que se haya **observado en runtime**, probar su frontera, registrar evidencia; nunca editar código generado a ciegas | Frena la espiral de `apply_*.py` que nos metió aquí. |
| 6 | [Perfect Dark Zero](https://github.com/nikolaygorb/PerfectDarkZeroRecomp) | `dev_debug_runtime = true`: stub automático de funciones desconocidas + `stub_sweep.txt` / `missed_functions.txt` | Convierte un cuelgue en una lista de trabajo. |
| 7 | [Perfect Dark Remaster](https://github.com/nikolaygorb/PerfectDarkRemasterRecomp) | Hook de cola de audio vacía que devuelve 1 ms en vez de girar tomando el lock | Patrón general: "espera activa sobre estado que nunca llega" — el mismo patrón que nuestro bucle de `TRAP-OOB`. |
| 8 | [Naughty Bear](https://github.com/MaxDeadBear/NaughtyBear_ReStuff) (CMake) | Su "GPU life-support": claim de MMIO, shadow regs, mini-PM4, bomba de interrupciones, hooks a la D3D del invitado | Es la lista de piezas que hacen falta **después** de M3, ya inventariada. |
| 9 | [Skate 3](https://github.com/mchughalex/skate3recomp) / [Crackdown 2](https://github.com/matty45/Crackdown2-Recomp) | Verificar **TU y revisión del XEX** antes de perseguir bugs de código (TU3 / TU5, `--max_jump_table_entries 519`) | Un XEX/TU distinto produce fallos idénticos a un bug de codegen. |
| 10 | [Fable II (himdo)](https://github.com/himdo/Fable-2-Recomp) | Parches a la **imagen del invitado antes de lanzar el módulo** + hooks a mitad de ensamblado + tracing de funciones | Alternativa limpia a parchear el C++ generado. |

### 2.2 Sección Rexglue (mismo SDK que nosotros)

| Proyecto | Repo | Qué tomar |
|---|---|---|
| Skate 3 | `mchughalex/skate3recomp` | Fork propio del SDK, D3D12+Vulkan+fallback emulado; exige TU3 staged antes de bootear. |
| Naughty Bear | `MaxDeadBear/NaughtyBear_ReStuff` | 0.10.0 + parches locales; CMake con "GPU life-support"; `HANDOFF-*.md`. |
| Lollipop Chainsaw | `MaxDeadBear/Re-Cherry` | Renderer nativo D3D12/Vulkan; flujo codegen limpio (sin bridge de entrypoint). |
| Banjo-Kazooie N&B | `masterspike52/reNut` | Renderer propio "Plume" enganchando la D3D del juego. |
| Viva Piñata TiP | `SolarCookies/TiP-Recomp` | Renderer nativo desde 1.13.0 (backend no publicado). |
| GoldenEye 007 | `SunJaycy/GoldenEye-Recomp` (+ fork `-rexglue`) | Parches de sync/RT/EDRAM en el fork; gotcha: AMD puede no bootear. |
| Blue Dragon | `zolaware/reblue` | D3D12/Vulkan/Metal; multi-XEX bootstrapper (lo usa PGR4). |
| Lost Odyssey | `freefrank/LostOdysseyRecomp` | Precompilado de shaders en el primer arranque + menú de debug. |
| Ninja Gaiden II | (no verificado) | — |
| Ace Combat 6 | `sal063/AC6_recomp` | `docs/RENDERER_ARCHITECTURE.txt`: backend Xenia hasta tener nativo. |
| Crackdown | `Unowa001/crackdown-recomp` (404 al leer) / `SkiddyToast/Crackdown` | `docs/playbook.md`, `docs/re-notes.md` (no verificables). |
| Crackdown 2 | `matty45/Crackdown2-Recomp` | 0.5.0, TU5, `--max_jump_table_entries`. |
| **Dante's Inferno** | `florinp93/hells-gate-recomp` | **El más valioso**: 0.10.0, mapa D3D→Vd*, `apply_generated_patches.py`, DiligentCore/Vulkan con Xenos/D3D12 de fallback. |
| Castlevania SotN | `birabittoh/NocturneRecomp` | Extracción STFS → `assets/default.xex`. |
| Fable II | `himdo/Fable-2-Recomp`, `Oery/fable-ii-recomp` | 0.10.0 D3D12; job de vocabulario de funciones; runbook de iteración. |
| PGR4 | `beatrixzy/PGR4-Recomp` | Necesita el multi-XEX bootstrapper de re:Blue. |
| DoDonPachi SDOJ | `eandis/SDOJ-Recomp` | Reordenar el batch de render alrededor del swap (latencia de input). |
| **Silent Hill Downpour** | `LittleBitUA/DownpourRecomp` (+ fork `rexglue-sdk-dpour`) | Detección de fin de occlusion-query: un `end` perdido deja el hilo de render girando. Mismo patrón que nuestro bucle. |
| Daytona USA | `Subarasheese/daytona-xbla-recomp` | Symbol map completo + `stubs.cpp`; `--game_data_root` obligatorio. |
| Condemned 2 | `psxrestore/Condemned2Recomp` | Build limpio; sin workarounds documentados. |
| Halo 3 (cache) | `twist84/halo3_cache_release_recomp` | Solo referencia histórica. |
| LEGO Dimensions | `NeverCookFirst/DimensionsRecomp` | Fork con fix de codegen; verificación de que el TU23 se aplicó. |
| **Deadly Premonition** | `LittleBitUA/DPRecomp` | Renderer D3D12 "preview" con path emulado por defecto → mismo esquema que queremos. |
| Forza Horizon | `arcanite24/pinyon-shift` | Fork "ShiftGlue" + renderer propio Vulkan. |
| Spider-Man EoT | `goliathret/EdgeOfTimeRecomp` | Gotcha clave: **sin el renderer nativo en el repo, compila y no dibuja**; sin shader cache, no dibuja. |
| King Kong | `TekRantGaming/king-kong-recompiled` | "boots to first level", fix del freeze "VENTURE". |
| DBZ Budokai 3 HD | `novapowers0/DBZ-Budokai-3-HD-Collection` | Launcher que valida XEX/datos y logs por sesión. |
| Burnout Revenge | `shipa-2/Xerenge` | **Traducción de shaders offline** (no depender de correr el juego para poblar la cache). |
| Midnight Club LA | `holdmysocks/mcla-recomp` | 0.10.0; errores de codegen ⇒ casi siempre XEX/TU equivocado. |
| WWE SvR 2011 | `KaikoClanworth1/wwe-svr2011-pc` | Renderer nativo Vulkan experimental + comparación con emulado. |
| Forza Motorsport 4 | `Alexbeav/fm4-recomp` | Perfil conservador validado: D3D12 ROV, resolve readback, vblank por defecto. |
| Ridge Racer 6 | `Sirhalo23/RidgeRacer6-Recompilation` | Fix de "pista negra" en el fork gráfico. |
| GTA IV iOS | `KoreanSeats1/Theft4` | Renderer Metal directo en vez de MoltenVK. |
| Army of Two | `OpenPortroid/ArmyOfTwoRecompilation` | Renderer D3D12 propio + `aot/docs`. |
| WET | `nikolaygorb/WetRecomp` | `settings/hardware.toml` con `gpu_plugin=xenos` y `graphics_backend=d3d12|vulkan`, `*_gpu_wait_mode`. |
| Forza Horizon 2 Android | `Gabrick75/fh2-recompiled` | Fix de busy-wait del event loop SDL. |
| DBZ Burst Limit | `iExplosiveRage/DBZ-Burst-Limit-Recompiled` | Renderer nativo por defecto + switch a emulado. |
| NFS Carbon Android | `codepdbh/NFS-CARBON-360-ANDROID-RECOMP` | D3D12 debug layer + DRED + `-DumpImage` para recuperar funciones. |
| Kameo | `MaxDeadBear/KameoRePowered` | `scripts/run.py` con game/update roots. |
| Skate 2 | `christianortel/skate2-recompile-pc` | Launcher + troubleshooting. |
| Asura's Wrath | `PauloUbirajara/asuras-wrath-recomp` | `--gpu_backend=d3d12|vulkan`, `--render_target_path_vulkan=fsi|fbo`. |
| Rockstar Table Tennis | `davidptrssvea/Table-Tennis-Rockstar-PC` | Overlay que tapaba el prompt de guardado = "no responde". |
| GoldenEye Remastered | `CaiusCodes/GoldenEye-007-XBLA-PC-Recomp` | 0.10.0 + parche; D3D12 en NVIDIA, Vulkan en AMD. |
| Geometry Wars 2 | `saulob/geometry-wars-retro-evolved-2-recompiled` | Reinstalar desde paquete preservando saves. |
| **Perfect Dark Zero** | `nikolaygorb/PerfectDarkZeroRecomp` | `dev_debug_runtime` → stub sweep. Config TOML plana (secciones ignoradas en silencio). |
| **Perfect Dark Remaster** | `nikolaygorb/PerfectDarkRemasterRecomp` | Fix de spin con lock de audio; `pdr_skip_live_prompt`. |
| 50 Cent BotS | `nlwstein/skullburglar` | 0.10.0, renderer Vulkan propio + precompilado de shaders con pantalla de progreso. |
| Gears of War 2 | `santiquiroz/gears-of-war-2-pc-port` | Primer arranque = minutos compilando shaders, **no** cuelgue. |
| Metal Slug XX | `FluffyQuack/rexglue-MetalSlugXX` | Fork del SDK con build/codegen documentados. |
| Tekken 6 | `mahmoud-mess/tekken6-recomp` | Matriz de backends: D3D12 ROV (más compatible), Vulkan FSI, FBO. |
| Minecraft X360 iOS | `DevZer0D4Y/MCX360-EDITION` | Checklist de pantalla negra: ruta del `default.xex` + title/media ID. |
| Crash MoM | `Maxilure/MindOver-Recomp` | F9 alterna emulado/nativo (comparación A/B). |

### 2.3 Sección XenonRecomp / XenosRecomp

| Proyecto | Repo | Qué tomar |
|---|---|---|
| Sonic Unleashed | `hedge-dev/UnleashedRecomp` | Renderer propio con swapchain *waitable* en D3D12 y Vulkan; pipelines compiladas durante carga de assets. |
| Sonic '06 | `sonicnext-dev/MarathonRecomp` | Capa gráfica "plume"; Win/Linux/macOS. |
| The Simpsons Game | `YesterMester/TheSimpsonsGameRecomp` | Runtime derivado de Xenia; Vulkan/D3D12/D3D11 experimental. |
| **Dead Rising 2: Case Zero** | `wivi514/Dead_Rising_2_Case_Zero_Xenon_Recomp` | **Ledger de parches** (14 XenonRecomp + 23 XenosRecomp), `docs/final-state.md`, `CLAUDE.md` con uso de Xenia como ground truth. |
| Test Drive Unlimited | `testdriveupgrade/TDURecomp` | Fork de XenonRecomp por motor; DX12+SDL3 primero. |
| NFS Most Wanted | `SpeedBreakerProject/speedbreaker` | Runtime propio + Vulkan + MoltenVK. |
| Dead Rising 2: Case West | `wivi514/...Case_West` | Vulkan + co-op. |
| The Darkness | `portingpete` (no verificado) | — |
| CoD 3 | `FrankHUN88453` (no verificado) | Host runtime + notas de estado. |
| Tekken Tag 2 | `shihabal3amri` (no verificado) | — |
| The Simpsons Arcade | `sp00nznet` (+ guía `360tools`) | `docs/runtime-debugging.md`: "bootea pero crashea" → stack, addr de invitado, stubs faltantes, fronteras de función. |

> No verificado = el researcher no encontró repo público legible. No se inventan URLs.

---

## 3. Plan por fases

Regla de oro adoptada de Fable II (Oery) y Case Zero: **una variable por iteración, con
evidencia de runtime antes de tocar nada**. Nada de parches a ciegas nuevos.

### F0 — Instrumentación de hitos (½ día) · sin riesgo
1. Añadir en `src/nd_import_diag.h` un contador único `VD-MILESTONE` con M3…M7
   (los hooks de `Vd*` ya existen: `sub_827BDFA4` para ring buffer, etc.). Al salir, una
   sola línea: `VD: create=0 cb=0 ring=0 swap=0`.
2. Loguear `r1` en cada iteración del main loop (`nd_main_loop.h`) y **abortar la corrida**
   si `r1` difiere del inicial → convierte el drift de pila en fallo inmediato y localizable.
3. Guardar en el log: SHA-256 del XEX, `gpu_plugin`, backend, y `Translated N shaders`.

**Aceptación:** una corrida de 20 s produce la línea `VD-MILESTONE` y (si hay drift) el
frame exacto donde empieza.

### F1 — Erradicar el drift de pila (1-2 días) · el bug real
1. Revertir el "retorno limpio" de `0x8278BD98` en `apply_bd_family_faithful.py`: el cuerpo
   fiel (`bl 0x8278BBE8; mr r3,r31; addi r1,+0x60; lwz r12,-8(r1); mtlr r12; ld r31,-0x10(r1); blr`)
   es obligatorio; el stub se salta el destructor **y** el pop.
2. Verificar con `tools/bd_truth.py` que cada miembro de la familia (`BD38/BD44/BD48/BD4C/BD50/BD60/BD98`)
   tiene el cuerpo **literal** del invitado (el script ya lo comprueba: `is_faithful`).
3. Instrumentar, una sola vez, `r1`/`r29`/`r31` **a la entrada del llamante** de cada
   miembro (`lr` del log ya nos dice quién es: `0x824D9E00`, `0x822032B8`, `0x82686060`).
   Comprobar que ese llamante reservó 0x70/0x60 — si no, la frontera de *ese* llamante
   está mal, no la cola.
4. No volver a tocar `BD*` hasta que el log demuestre `r1` constante.

**Aceptación:** 1 000 frames sin `r1` drift, sin `lr=0xBEBEBEBE` en un retorno real, sin
AV en `0x70190050`.

### F2 — Auditoría de fronteras a escala (2-3 días) · convierte 30 parches en 1 script
Portar de Case Zero, adaptados a `config/functions.toml`:
- `find_dropped_branches.py`: ramas cuyo destino cae fuera del `end` de la función.
- `coverage_to_function_overrides.py`: convierte huecos de cobertura en overrides.
- `fix_switch_function_bounds.py`: `bctr` que corta el extent.
Añadir un chequeo propio barato y decisivo: **toda "función" cuya primera instrucción no
sea un prólogo (`stwu`/`mflr`/`blr`/`lis`/`b`) y que no esté precedida por un prólogo es
candidata a chunk**. Hoy eso marca `0x821FFC00` (empieza en `bctrl`) y `0x8278BD48`
(empieza en `bl`) sin discusión.

**Aceptación:** lista cerrada de candidatos (<100) clasificados en {chunk con `parent`,
entry real, dato}, y `rexglue codegen` sin warnings de branch no resuelto.

### F3 — Decidir v1 vs v2 (½ día de decisión, 1 día de ejecución)
Hoy compilamos v1 (seed viejo + 30 parches). v2 ya tiene `.pdata` real, 15 457 chunks con
`parent`, 897 `share_registers`, vtables y regiones inválidas. **Propuesta: regenerar v2 y
comparar logs**, no seguir parchando v1. Requiere tu OK porque implica volver a habilitar
`rexglue codegen` (hoy bloqueado en `generated/rexglue.cmake`).

**Aceptación:** dos corridas (v1 vs v2) con la misma semilla de log; gana la que pase F1.

### F4 — Ruta de arranque real vs. main loop manual (1-2 días)
`src/nd_main_loop.h` **se salta** el WinMain del invitado y fuerza `r3=1`. Si la creación
del dispositivo D3D del invitado vive en la ruta que el bridge cortó, M3 nunca ocurrirá.
Pasos: (a) trazar si `Direct3D_CreateDevice`-equivalente es alcanzable desde el loop manual;
(b) si no, restaurar la entrada real (`apply_entrypoint_bridge.py`) y dejar que el invitado
llame a su propio loop; (c) solo entonces medir M3.

**Aceptación:** M3 aparece, o queda demostrado con log que la ruta manual lo ejecuta.

### F5 — GPU bring-up invitado (2-3 días)
1. `MmGetPhysicalAddress` para el ring buffer (ya hay hook con fallback `& 0x1FFFFFFF`).
2. `VdGetSystemCommandBuffer` / `VdInitializeScalerCommandBuffer`: si el juego valida los
   marcadores `0xBEEF0000/1` o los NOPs, implementarlos de verdad (Xenia los marca
   `kStub`/`kSketchy`).
3. `VdSetDisplayMode` (stub `0`): registrar quién lo llama y con qué.
4. `VdSetGraphicsInterruptCallback`: confirmar registro y que la bomba de vblank dispara.

**Aceptación:** M5 + M6 + M7 (`VdSwap` #1) + M8 (`Translated N>0`).

### F6 — Renderer y paridad (continuo)
Empezar por **D3D12 ROV** (perfil conservador de FM4/Tekken 6), `native_2x_msaa=false`,
`render_target_path_d3d12` por defecto. Vulkan después, con `fsi` (el gotcha de Asura's
Wrath en NVIDIA). Shaders: cache offline estilo Burnout/Xerenge, no depender del primer
run.

---

## 4. Reglas de proceso (para no repetir la espiral)

1. **Nada de `apply_*.py` nuevo sin evidencia de runtime** (Fable II runbook). Cada parche
   debe citar la línea de log que lo justifica y quedar en un ledger
   (`docs/patch_ledger.md`) con: dirección, byte de invitado, síntoma, log antes/después.
2. **El build falla si hay `Unresolved branch`, `Unrecognized instruction` o target de
   switch fuera de función** (Case Zero lo usa como build gate). Hoy se ignoran y luego se
   descubren como cuelgue.
3. **Un cambio por corrida**, con el mismo watchdog y las mismas 20 s.
4. **Nunca fabricar epílogos.** Si un fragmento asume el frame del llamante, se modela como
   chunk (`parent` + `share_registers`) o se corrige el llamante; no se "stubea".
5. **Verificar el XEX/TU antes de culpar al codegen** (Skate 3, Crackdown 2, Midnight Club).

---

## 5. Orden de ejecución recomendado

| Orden | Fase | Coste | Riesgo | Ganancia |
|---|---|---|---|---|
| 1 | F0 hitos + guard de `r1` | ½ día | nulo | visibilidad total |
| 2 | F1 cola `BD*` fiel | 1-2 días | bajo | quita el cuelgue raíz |
| 3 | F2 auditoría de fronteras | 2-3 días | bajo | cierra la clase de bug |
| 4 | F4 ruta de arranque | 1-2 días | medio | desbloquea M3 |
| 5 | F3 v1 vs v2 | 1 día | medio (codegen) | deuda técnica |
| 6 | F5/F6 GPU | 3-5 días | alto | primer frame |

**Si solo hay tiempo para una cosa:** F0 + F1. El `r1` que sube 0x70 por llamada no es un
síntoma periférico; es la razón por la que `lr` acaba en `0xBEBEBEBE` y por la que el
`bctrl` de `0x821FFC00` lee un vtable de basura (`0x5`, `0x0`, `0x8205970C` = la cadena
`"Fall"`). Arreglar los hitos sin arreglar eso solo produce logs más bonitos del mismo
cuelgue.
