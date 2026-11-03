# Revisión adversarial del informe "NeverDead Reverse Engineering Report"

**Revisor:** sesión de revisión independiente
**Fecha:** 2026-10-24 20:00 (GMT-4)
**Objeto revisado:** informe pegado por el usuario, atribuido a otra IA, con estado "MENU & 3D ACHIEVED".
**Método:** contraste de cada afirmación contra el árbol del proyecto, `build_clang/logs/neverdead_*.log`, `generated/neverdead_default/`, capturas en `build_clang/` y `build_clang/shots/`, y los documentos `docs/AUDIT_BOOT_AND_3D.md`, `docs/BUG_RAIZ_OFFSET_DECODIFICACION.md`, `PLAN_REXGLUE_MENU_3D.md`.

---

## Veredicto

**La conclusión de fondo es cierta; el informe que la sostiene no lo es.**

- El título y el **menú principal 3D** se alcanzaron de verdad. La prueba está en `build_clang/shots/04_after_enter_6s.png` (17:25:07): "MAIN MENU", Single Player / Multiplayer Challenges / Options, personaje sentado en una butaca con iluminación y sombras, 200 FPS en el overlay. **Esa captura no aparece citada en el informe.**
- La evidencia que el informe sí cita (`after_start_*.png`) es la **pantalla de título**, que es 2D: fondo estático + grano animado + "Press START" pulsando. No demuestra 3D.
- El informe se escribió **antes** de que existiera la prueba del menú: cita la captura de 40 s (17:17:03) y las 26 pipelines del log de 17:15, mientras que el menú se capturó a las 17:25:07. La afirmación "main menu was accessible" era, en el momento de escribirla, una extrapolación.
- Fiabilidad: **cualitativa 6/10, cuantitativa 3/10**. De 13 cifras comprobables, **6 son incorrectas o están desactualizadas** (tamaño del target, MB recompilados, tamaño de `neverdead_init.cpp`, pipelines, cadencia/tamaño de capturas, heap post-imagen).
- **Tres de sus "high priority" están mal planteadas** y una de ellas (reactivar el mapeo de `.rdata`) reintroduce un bug ya corregido y medido.

---

## 1. Lo que el informe acierta

| Afirmación | Estado | Prueba |
|---|---|---|
| GPU `xenos`, D3D12, RTX 2060 (0x10DE/0x1F08) | ✅ | `logs/neverdead_067.log:2-11` |
| 36 shaders traducidos en 1 ms | ✅ | `neverdead_067.log:75` |
| Rango del módulo de tabla de funciones `821A0000-827C05E4`, imagen `82000000-82C10000` | ✅ | `neverdead_067.log:15` |
| 11 alias VFS (`dvd:`, `cdrom0:`, `assets:`, `FMV:`, `GUIMenu:`, `Fonts:`, `Text:`, `misc:`, `sounds:`, `D:`, `d:`) | ✅ | `neverdead_067.log:73` |
| `FMV\CESA.bik` falla con `0xc000000f` | ✅ | `neverdead_067.log` (NtCreateFile) |
| `gamecontrollerdb.txt` ausente (sólo warning) | ✅ | `neverdead_067.log` |
| `NtQueryInformationFile(XFileXctdCompressionInformation)` no implementado | ✅ | 148 ocurrencias en el log 067 |
| 121 unidades `neverdead_recomp.*.cpp` + 121 `neverdead_funcs.*.h` | ✅ | `ls generated/neverdead_default/` |
| `neverdead_funcs.h` = 826.634 bytes | ✅ | `ls -l` |
| `neverdead_pch.h` = 18.522 bytes | ✅ | `ls -l` |
| `nd_chain_decls.h` = 849 bytes | ✅ | `ls -l` |
| Mapa de memoria: 512 MB, mid/high 1 GB, stacks 240 MB, pre-image 32 MB | ✅ | `neverdead_067.log:103-140` |
| Ventana viva ≥40 s con frames presentados | ✅ | `after_start_40s.png` (17:17:03) |

---

## 2. Errores factuales

| El informe dice | Realidad | Prueba |
|---|---|---|
| "Target: `NeverDead_base.exe` (40.031.744 bytes)" | `NeverDead_base.exe` pesa **12.648.448** bytes. 40.031.744 es el tamaño del **binario anfitrión construido** (`build_clang/neverdead.exe`, 17:50:25). | `ls -l NeverDead_base.exe build_clang/neverdead.exe` |
| "Entry point: 0x821A0000 - 0x827C05E4" | Ese es el **rango del módulo de la tabla de funciones**, no un entry point. El propio log lo etiqueta así. | `neverdead_067.log:15` |
| "121 unidades, ~8,6 MB de C++ recompilado" | **108.379.028 bytes ≈ 103,4 MiB**. Ningún árbol del proyecto pesa 8,6 MB. | `cat generated/neverdead_default/neverdead_recomp.*.cpp \| wc -c` |
| "`neverdead_init.cpp` (7.431 bytes)" | **802.186 bytes**. Ningún árbol tiene 7.431. | `ls -l generated/*/neverdead_init.cpp` |
| "26 graphics pipelines" | **31** desde el log 053 (17:50). 26 era correcto a las 17:15, no al cerrar el informe. | `neverdead_067.log:76` |
| "8 capturas a intervalos de 1 segundo, cada una de 1,3 MB" | Son **8 capturas a 1, 3, 6, 10, 15, 22, 30 y 40 s**. La de 1 s pesa **11.960 bytes** (frame negro); el resto 1,32-1,34 MB. | `ls -l --time-style=+%H:%M:%S build_clang/after_start_*.png` |
| "Post-image kernel heap ~224 MB" | `0xF3F0000` = **244 MiB**. | `neverdead_067.log` |
| "`find_bink_traps.py` en `tools/`" | Está en la **raíz** del proyecto, no en `tools/`. | `find . -name "find_bink*"` |
| "Logs muestran ejecución continua sin crash 15+ s" | Los logs **cortan a media línea de log**, sin shutdown limpio (el 067 va de 19:37:13 a 19:37:28 y termina en un error de `NtQueryInformationFile`). No prueban "sin crash"; lo prueban las capturas. | `tail neverdead_067.log` |
| "`neverdead_prev.exe` es el ejecutable reconstruido a probar" | Es la **build anterior** (17:48:33), no la actual (17:50:25). | `ls -l build_clang/neverdead_prev.exe` |

---

## 3. La conclusión es correcta, pero la evidencia citada no la sostiene

### 3.1 Título ≠ menú ≠ 3D

Las capturas citadas muestran la pantalla de título. Medido con Pillow sobre las 7 capturas de 1,3 MB:

- Entre el 63 % y el 69 % de los píxeles cambian de un frame a otro.
- Al recortar la esquina superior izquierda y comparar 03 s vs 40 s, **la estructura del fondo es idéntica**: lo que cambia es el grano y el pulso de "Press START".
- Es decir: **hay presentación real y un shader de grano animado, pero no geometría 3D en esas imágenes**.

Además, "26 pipelines creadas" y "36 shaders traducidos" son trabajo de **arranque** de ReXGlue a partir del shader storage. No son evidencia de que el juego esté dibujando geometría 3D por frame.

### 3.2 La prueba real existe y no se cita

`build_clang/shots/04_after_enter_6s.png` (17:25:07) sí muestra el menú principal con un personaje 3D, sombras e iluminación, y el overlay marca 200 FPS. La última actividad registrada de Xenia es de las 16:44 (`_Juegos_y_Emulacion/Xenia/recent.toml`), por lo que **es muy probable** que la captura sea de la recompilación y no del oráculo; no es concluyente, pero es la hipótesis con más apoyo.

Pero **no hay log de ese momento**: `neverdead_051.log` termina a las 17:25:04, tres segundos antes de la captura. La evidencia visual es buena; la trazabilidad, inexistente.

### 3.3 Punto ciego de observabilidad

En ninguna corrida aparece una sola línea `IMPORT-REAL`, pese a que `src/nd_import_diag.h:433` registra `VdInitializeEngines CALLED!` y el archivo se modificó a las 13:23, antes de las corridas de 17:15. Conclusión: **los hooks no se disparan** (probablemente el código generado llama al stub directo y no pasa por la tabla de hooks). Por tanto, la ausencia de `VdInitializeEngines`/`VdSwap` en los logs **no prueba que no se llamen**, y el informe no puede afirmar ni negar el estado del pipeline 3D desde el log. Es un hueco de instrumentación, no un dato.

---

## 4. Recomendaciones que NO hay que seguir

### 4.1 "Fix `.rdata` mapping / investigar `game_data_root/XEX`" — peligroso

`src/neverdead_app.h:203` define `kExpectVtable = 0x8249EF98` y avisa cuando el slot contiene `0x824DDF90`. Ese "valor esperado" se calculó con desplazamiento 0, pero el propio auditor midió que `.rdata` media necesita **shift +0x1000** y `.rdata` alta **+0x2000** (`docs/AUDIT_BOOT_AND_3D.md:283-293`). El warning es un **falso positivo**.

Peor: reactivar la inyección de `.rdata` con copia 1:1 es exactamente el bug que se corrigió el 23/10 — sobrescribía `0x82144930[+16]` con `0x006C0065` (texto UTF-16 ejecutado como código). `ND_FILL_RDATA=1` existe sólo para A/B, no es una corrección.

**Acción correcta:** borrar/corregir la expectativa `0x8249EF98`; no tocar el mapeo.

### 4.2 "Localizar o extraer `CESA.bik` de los archivos del juego" — imposible

`assets/FMV/` tiene **27 `.bik`** y ninguno es `CESA.bik`. `PLAN_REXGLUE_MENU_3D.md:108` documenta que ese vídeo sólo existe en la edición japonesa. El motor ya continúa hasta el título sin él, así que no es un bloqueante: es el comportamiento esperado de este SKU.

**Acción correcta:** garantizar el *fallthrough* limpio (CESA → `Front_End.bik` → título) y no perseguir un archivo que no está.

### 4.3 "Vtable reconciliation: 0x824DDF90 vs expected 0x8249EF98" — el marco es incorrecto

`0x824DDF90` es un puntero válido que el loader del XEX deja puesto (`pre-map` y `post-load` coinciden). El problema real, documentado en `docs/AUDIT_BOOT_AND_3D.md:205`, es que **no está registrado en la tabla de funciones** y el runtime lo absorbe con un stub `r3=1` (`src/nd_indirect_trap.h:160-161`).

**Acción correcta:** registrar `0x824DDF90` en `config/functions.toml` y en `vtables.toml`. No "reconciliar" hacia un valor que es el equivocado.

### 4.4 "Instalar `gamecontrollerdb.txt`" — innecesario

El log muestra el mando Xbox 360 detectado con **mapeo completo** (`start:b7, a:b0, b:b1, …`) usando la base interna de SDL. El archivo sólo aporta mapeos para mandos exóticos.

---

## 5. Omisiones graves

1. **No menciona el bug raíz** (`docs/BUG_RAIZ_OFFSET_DECODIFICACION.md`): el árbol anterior decodificaba los cuerpos desde el offset plano, con +0x1000/+0x2000 de desfase; **ninguna** de las 15.323 entradas `.pdata` tenía prólogo en el offset plano.
2. **No menciona el cambio de pipeline**: `neverdead_manifest.toml:8` genera ahora desde `assets/default.xex` (shift 0), no desde `NeverDead_base.exe`. El informe sigue describiendo el árbol derivado del `.exe` como "the current best effort".
3. **No menciona la purga**: `CMakeLists.txt` pasó de 29.984 bytes (`CMakeLists.txt.bak_whackamole`) a 2.221, y se eliminaron los stubs sintomáticos. El informe, en cambio, recomienda seguir con la reconstrucción antigua.
4. **No menciona el censo real de configuración**: 3.420 entradas en `config/vtables.toml` y 19.982 en `config/functions.toml`, que son la base declarativa del árbol actual.
5. **No cita la captura del menú 3D** ni el estado real de 31 pipelines.

En conjunto: el informe describe un proyecto anterior al que existe hoy. Sirve como foto de las 17:15; no como estado del arte.

---

## 6. Estado real medido (20:00)

| Elemento | Estado | Evidencia |
|---|---|---|
| Pantalla de título | ✅ renderizada, 2D + grano | `after_start_40s.png` (17:17) |
| Menú principal | ✅ con escena 3D y personaje | `shots/04_after_enter_6s.png` (17:25) |
| Input | ✅ mando Xbox 360 en slot 0, mapeo completo; START llevó al menú | log 051 + captura 04 |
| Pipeline gráfico | ✅ 31 pipelines, 36 shaders | `neverdead_067.log:75-76` |
| Build actual | `neverdead.exe` 40.031.744 B, 17:50:25 | `ls -l` |
| Log más reciente | 067, 239 líneas, 15 s, **sin shutdown limpio** | `wc -l`, `tail` |
| FMV | ⚠️ `CESA.bik` ausente por SKU; el motor sigue | log 067 |
| `.rdata` | ✅ no inyectar (decisión correcta); warning es falso positivo | `AUDIT_BOOT_AND_3D.md:293` |
| `0x824DDF90` | ❌ no registrado; absorbido con `r3=1` | `nd_indirect_trap.h:160` |
| `XFileXctdCompressionInformation` | ❌ 148 errores por corrida | log 067 |
| Instrumentación `IMPORT-REAL` | ❌ 0 líneas: hooks ciegos | logs 050/051/067 |

---

## 7. Próximos pasos que yo ejecutaría

1. **Re-capturar con la build de las 17:50 y un solo run instrumentado**: log + vídeo + secuencia de input (título → START → menú → Single Player). Hoy la evidencia mezcla builds (26 vs 31 pipelines) y la captura del menú no tiene log.
2. **Arreglar la observabilidad antes que el código**: hacer que los hooks `Vd*` se disparen (o instrumentar el stub `__imp__` que realmente llama el código generado). Sin esto, cualquier afirmación sobre 3D es fe.
3. **Registrar `0x824DDF90`** en `functions.toml`/`vtables.toml` y retirar el stub `r3=1`.
4. **Corregir `kExpectVtable`** en `src/neverdead_app.h:203` (o eliminar el warning). No reactivar `ND_FILL_RDATA`.
5. **Implementar el fallthrough de FMV** CESA → `Front_End.bik` → título, y verificar que no quede ningún hilo bloqueado.
6. **Implementar o justificar `XFileXctdCompressionInformation`**: 148 errores por corrida es ruido que oculta señales reales.
7. **Medir 3D de verdad**: draw calls, depth buffer, geometría por frame; capturar una escena in-game (Single Player), no sólo el menú.
8. **Archivar el informe** como `SNAPSHOT_17-15` o corregirlo; dejarlo como "estado actual" induce a error al siguiente agente.

---

## Anexo: comandos de verificación usados

```bash
ls -l NeverDead_base.exe build_clang/neverdead.exe
wc -c generated/neverdead_default/neverdead_recomp.*.cpp
grep -n "graphics pipelines\|Translated" build_clang/logs/neverdead_067.log
grep -c "IMPORT-REAL" build_clang/logs/neverdead_0{50,51,67}.log
md5sum build_clang/after_start_*.png
# Pillow: diff de píxeles entre frames + recorte 03s vs 40s
grep -rn "8249EF98\|824DDF90" src/
sed -n '280,300p' docs/AUDIT_BOOT_AND_3D.md
```
