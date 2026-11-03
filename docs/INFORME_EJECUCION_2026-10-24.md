# Informe de ejecución — Fases 1 y 2 (2026-10-24 21:20)

> **Actualización 21:50:** este informe cubre la fase de instrumentación y el bloqueo
> por asset. Ese bloqueo se resolvió extrayendo el contenedor GOD de `D:` y declarando
> los thunks ausentes; el 3D in-game ya renderiza. Ver
> `docs/INFORME_3D_INGAME_2026-10-24.md`.

**Objetivo:** ejecutar el plan de Gemini Argon (observabilidad + reproducibilidad del
menú 3D) sin whack-a-mole y usando los mecanismos nativos de ReXGlue.

**Resultado en una frase:** la telemetría de GPU ya es real y el menú 3D es alcanzable,
pero el 3D in-game está bloqueado por un **asset que falta** (`envs/sp/sp_00_prologue.xbox360`),
no por el recompilador.

---

## 1. Resumen ejecutivo

| Pregunta | Respuesta verificada |
|---|---|
| ¿Se llamó `VdInitializeEngines`? | **Sí**, 1 vez (`neverdead_071.log`, 21:11:28.680) |
| ¿Se presenta por `VdSwap`? | **Sí**, 1200 llamadas en 42 s (~30 FPS sostenidos) |
| ¿El VEH estaba ocultando fallos? | **No en este camino**: 0 activaciones en todas las corridas de hoy |
| ¿Hay llamadas indirectas sin registrar? | **0** en los logs 071-076 |
| ¿El menú 3D es reproducible? | **Sí, con matices de timing**: se capturó el menú (17:25) y la pantalla DIFFICULTY con el personaje 3D (21:11) |
| ¿Se entra a gameplay? | **Sí**: HUD (arma, munición, crosshair) y menú de pausa |
| ¿Se ve el mundo 3D in-game? | **No**: pantalla negra porque falta el nivel |

**La conclusión importante:** el motor ya renderiza 3D (menú y personaje). El único
bloqueo para el 3D in-game es que `assets/` no contiene los niveles: el juego pide
`GAME:\envs\sp\sp_00_prologue.xbox360` y recibe `0xC000000F` (no encontrado).

---

## 2. Cambios aplicados

| Fichero | Cambio | Por qué |
|---|---|---|
| `src/nd_hooks.h` / `.cpp` | Hooks `NdVdInitHook()` / `NdVdSwapHook()` con contadores atómicos y log rate-limited | Instrumentar sin tocar `generated/` |
| `config/mid_asm_hooks.toml` | 2 `[[midasm_hook]]` en `0x826BC3FC` y `0x826B89A0` | Son los dos únicos `bl` a los stubs de Vd* (verificado con capstone) |
| `neverdead_manifest.toml` | Incluye `config/mid_asm_hooks.toml` | Que el codegen los inyecte |
| `CMakeLists.txt` | `src/nd_hooks.cpp` + `-include src/nd_hooks.h` | El codegen emite el prototipo, pero `-include` es red de seguridad |
| `config/gap_chunks.toml` | `0x8224CFF8`: `end` 0x8224D048 → **0x8224D060** | El bloque del `beq` llega hasta 0x8224D05C; el siguiente `.pdata` empieza en 0x8224D060 |
| `src/crash_diag.cpp` | Log `[VEH-LOUD]` en cada activación + `ND_VEH_REPAIR=0` | Medir sin enmascarar; el default sigue reparando |
| `config/functions.toml` | Movido a `archive/config/functions.toml` | Semilla huérfana: el codegen no la consume |
| `tools/nd_menu_test.py` | Arnés: lanza el juego, enfoca, pulsa Enter, captura | Prueba determinista y repetible |

No se tocó `generated/`, ni ningún asset, ni la lógica del juego.

---

## 3. Verificación

### 3.1 Build

- Incremental: **31 s** (solo se regeneraron 2 TU + link).
- Binario: `build_clang/neverdead.exe`, **40.033.280 B**, 21:04:39.
- Codegen emitió las llamadas en el sitio correcto:

```cpp
// neverdead_recomp.7.cpp:22745
// bl 0x827c0084
NdVdInitHook();
__imp__VdInitializeEngines(ctx, base);

// neverdead_recomp.68.cpp:30945
// bl 0x827bffd4
NdVdSwapHook();
__imp__VdSwap(ctx, base);
```

- El branch que estaba roto ya tiene label: `loc_8224D048` existe en
  `neverdead_recomp.73.cpp:2463` y el `goto` en `:2425`.

### 3.2 Telemetría real (log 071)

```
21:11:28.680  [MIDASM] VdInitializeEngines call #1 (site 0x826BC3FC)
21:11:29.381  [MIDASM] VdSwap call #1 (site 0x826B89A0)
21:11:39.049  [MIDASM] VdSwap call #300
21:11:49.083  [MIDASM] VdSwap call #600
21:11:59.077  [MIDASM] VdSwap call #900
21:12:09.097  [MIDASM] VdSwap call #1200
```

300 llamadas cada ~10 s = **~30 FPS**. Los 200 FPS del overlay en la captura del menú
del 17:25 eran la métrica del overlay, no el ritmo real de `VdSwap`.

### 3.3 VEH y estabilidad

- `grep -c VEH-LOUD crash_diag.log` → **0** en todas las corridas de hoy.
- `grep -c unregistered` en logs 071-076 → **0**.
- No hay AVs, ni div-0, ni `bctrl` a target 0 en el camino título → menú → prólogo.

Esto significa que el VEH **no** estaba tapando la causa de nada en este camino. Sigue
siendo una muleta para otros caminos, pero no es el problema actual.

### 3.4 Capturas

| Fichero | Qué muestra |
|---|---|
| `build_clang/shots/04_after_enter_6s.png` (17:25) | **MAIN MENU 3D**: personaje en la butaca, luces, sombras |
| `build_clang/shots/run_211127_0027s.png` | Pantalla **DIFFICULTY** con la escena 3D del personaje |
| `build_clang/shots/run_211243_0024s.png` | **HUD in-game** (Handgun 10/180, crosshair) sobre mundo negro |
| `build_clang/shots/run_211836_0026s.png` | **Menú de pausa** (Resume / Return to Checkpoint / Options / Quit) |

---

## 4. Hallazgo principal: falta el nivel, no es un bug del recomp

El log muestra el intento de carga y el error:

```
21:13:02.630 [warning] [krnl] [NtCreateFile] FAILED:
  path='GAME:\envs\sp\sp_00_prologue.xbox360' -> 0xc000000f
```

`assets/` contiene solo:

```
FMV/  Fonts/  GUIMenu/  Text/  misc/  sounds/  default.xex  default.xex.bak
```

No hay `envs/`, ni personajes, ni armas. Búsqueda en `C:/Users/katze/Downloads`:
**0** coincidencias de `sp_00_prologue*` y **0** directorios `envs/`.

Por eso el HUD (2D) se dibuja y el mundo (3D) es negro: el juego entra al prólogo, pero
no tiene geometría que cargar. **Ningún cambio en el recomp puede arreglar esto.**

---

## 5. Correcciones al plan de Gemini Argon

| Propuesta del plan | Veredicto | Evidencia |
|---|---|---|
| "Redefinir `__imp__VdSwap` en código de usuario" | **No enlaza** | `__imp__VdSwap` es símbolo **fuerte** en `rexruntime.lib` (`llvm-nm`: `T __imp__VdSwap`) → LNK2005 |
| "Actualizar las direcciones de `nd_import_diag.h` a 0x827C0084 / 0x827BFFD4" | **No sirve** | El generado llama a `__imp__*`, no al símbolo de dirección; `grep 827C0084 generated/` solo aparece en la tabla de dispatch |
| "Declarar los 7 helpers CRT en `gap_chunks.toml`" | **Incorrecto** | Son labels interiores de extents existentes (`loc_8278C3A8` ya se emite). El unresolved viene de 15 chunks de `vtables.toml` que empiezan dentro del padre |
| "Extender el `end` de `0x8224CFF8`" | **Correcto** | Verificado: bloque hasta 0x8224D05C, siguiente función en 0x8224D060 |
| "VEH ruidoso antes de reparar" | **Correcto y aplicado** | `[VEH-LOUD]` + `ND_VEH_REPAIR=0` |
| "Archivar `functions.toml`" | **Correcto y aplicado** | `archive/config/functions.toml` |

### Sobre los 7 unresolved restantes

Los 15 chunks conflictivos están **registrados en la tabla de dispatch**
(`neverdead_register.cpp:22357` y siguientes), así que borrarlos para "llegar a 0
unresolved" rompería llamadas indirectas a esos slots. Es un trade-off real, no un
bug de configuración. Propuesta: experimento separado (un build) borrando solo los 15,
midiendo si aparecen `unregistered indirect call`; no mezclarlo con el objetivo del menú.

---

## 6. Estado por niveles

| Nivel | Estado | Evidencia |
|---|---|---|
| N0 Ventana D3D12 + present | ✅ | logs + capturas |
| N1 Título estable | ✅ | 40 s, `after_start_40s.png` |
| N2 Menú principal | ✅ | `04_after_enter_6s.png` |
| N3 Menú con geometría 3D | ✅ | personaje, luces, sombras |
| N4 Menú → gameplay sin caída | ✅ | HUD + pausa (logs 071-076) |
| N5 Frame in-game con mundo 3D | ❌ **asset faltante** | `envs/sp/sp_00_prologue.xbox360` 0xC000000F |

---

## 7. Próximos pasos

1. **Conseguir el dump completo del juego** (`envs/`, personajes, armas) y colocarlo en
   `assets/` conservando la estructura `GAME:\...`. Sin eso, N5 es imposible.
2. **Preflight de assets**: al arrancar, comprobar que existen los niveles referenciados
   y abortar con un mensaje claro en vez de renderizar un mundo negro.
3. **Reproducir el menú con ventana de timing**: el título auto-carga el prólogo a los
   ~18-19 s; el START debe caer antes. Alternativa: usar el mando real en vez de MnK.
4. **Experimento de los 15 chunks** (build separado) para decidir si se borran o se
   modelan de otra forma; medir `unregistered` antes/después.
5. **Archivar build + config** de este hito (N4) para no repetir la pérdida del build
   de las 17:25.

---

## 8. Cómo reproducir

```bash
# build
export MSVC=".../MSVC/14.44.35207"; export SDK=".../Windows Kits/10"; export SDKV="10.0.26100.0"
export INCLUDE="$MSVC\\include;$SDK\\Include\\$SDKV\\ucrt;..."; export LIB="..."
cmake --build build_clang --config Release

# run determinista (START a los 20 s, capturas a 24/26/28/32)
REX_MNK_MODE=1 python tools/nd_menu_test.py --enter-at 20,22 --shots 24,26,28,32

# diagnostico VEH sin reparacion
ND_VEH_REPAIR=0 build_clang/neverdead.exe --game_data_root assets
```

**Nota:** `REX_MNK_MODE=1` activa la emulación teclado→mando del SDK; sin ella, Enter no
llega a XInput y el título nunca responde. `keybind_start` por defecto es `"X,Return"`.

---

## 9. Ficheros tocados

```
src/nd_hooks.h                     nuevo
src/nd_hooks.cpp                   nuevo
config/mid_asm_hooks.toml          nuevo
neverdead_manifest.toml            + include mid_asm_hooks
CMakeLists.txt                     + nd_hooks.cpp, -include nd_hooks.h
config/gap_chunks.toml             end 0x8224D060
src/crash_diag.cpp                 VEH-LOUD + ND_VEH_REPAIR
archive/config/functions.toml      movido desde config/
tools/nd_menu_test.py              nuevo
```

**No modificado:** `generated/neverdead_default/`, `assets/`, lógica del juego.
