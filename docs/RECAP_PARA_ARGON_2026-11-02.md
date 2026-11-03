# INFORME PARA ARGON — NeverDead PC Recompilation
**Fecha:** 3 de noviembre de 2026 · **Versión:** v11 (sustituye a v10; autocontenido)
**Binario:** `build_clang/neverdead.exe` (09:35, 38.843.392 B) · **Commit:** `c07aa8c`
**Grafo:** 710 nodos · 883 aristas · 34 comunidades · `docs/GRAFO_ARGON.txt` (34 KB, 290 líneas)

> **Lo esencial de este turno es una corrección de método.** La medición
> anterior del ring se hizo con esperas fijas y cayó en el menú, no en el juego.
> Aquí están los números de gameplay 3D verificado y lo que implican.

---

## 0. Resumen ejecutivo

| Pregunta | Respuesta medida |
|---|---|
| ¿NeverDead usa Predicated Tiling a 720p? | **No.** 0 paquetes `SET_BIN_MASK`/`SET_BIN_SELECT` en 540 frames de gameplay. |
| ¿Usa 2x MSAA? | **No** en la superficie principal: `msaa_samples = 0` (1x). El 2x aparece solo transitorio en superficies auxiliares. |
| ¿Cuántos draws/resolves por frame? | **725 draws** y **29 resolves**. |
| ¿Dónde está el coste entonces? | **En CPU**: 154.861 escrituras de registro + 433 subidas de microcódigo de shader + 535 cargas de constantes por frame. |
| ¿El desfase 30 vs invitado? | En gameplay el invitado va a **38,0 FPS**; se tiran **8 de cada 38 frames (21 %)**. |
| ¿Se puede frenar al invitado? | Sí, y se implementó. Pero cuesta presentación (30→22), así que va **opt-in**. |

---

## 1. La corrección: qué se midió mal y por qué

`tools/bench_v6.py` usa el protocolo heredado de v4: lanzar, esperar 45 s fijos,
asumir que el juego ya está en el hospital. En la práctica el auto-avance no es
determinista: en la primera pasada de este turno las ventanas `[RING]` cayeron
en el menú/carga y dieron **50 draws / 19 resolves**. Esos números eran
plausibles, así que pasaron el filtro. No lo eran.

**Arreglo:** `tools/bench_ring.py` muestrea la ventana cada 2 s y calcula la
fracción de píxeles no negros:

| Escena | No-negro |
|---|---|
| Menú / carga | 0 – 72 % (muy variable) |
| Gameplay 3D | **> 93 %** |

Exige **3 muestras seguidas > 80 %** antes de contar nada, guarda una captura de
prueba y solo analiza las ventanas `[RING]` posteriores a esa marca. La captura
de la corrida válida está en `docs/assets/ring_gameplay_proof.png`: pasillo del
hospital, personaje, HUD de munición, god rays, profundidad de geometría.

> **Regla para el siguiente turno:** ningún bench de este proyecto debe volver a
> usar esperas fijas. `bench_ring.py` es el patrón a reutilizar.

---

## 2. Qué ejecuta `rexgpu-xenos` por frame (gameplay real)

Sonda: `NdRingProbe()` en `src/nd_hooks.cpp`, activa con `ND_RING=1`. Lee el
*primary buffer* del driver D3D del invitado.

Anclaje (verificado en el código recompilado):

```c
// sub_826B5548 — asignador del primary buffer
r11 = [dev+48];                          // puntero de escritura
r10 = [dev+52];                          // fin del buffer
if (r11 + n*4 > r10) sub_826B6B38(dev);  // ring lleno -> volcar/crecer
return [dev+48];
```

`sub_826B8620` (Present) avanza `[dev+48]` **después** del `VdSwap`
(`stw r11,48(r31)` con `r11 = r30+256`). Por eso, en el hook de `VdSwap`,
`[dev+48]` marca el final exacto de los comandos del frame.

### 9 ventanas en gameplay confirmado (swaps 1081–1561)

| Métrica | Media | Rango |
|---|---|---|
| `draws` / frame | **724,8** | 422 – 1005 |
| `resolves` / frame | **28,9** | 16 – 44 |
| `bin_mask` (0x50) / frame | **0** | — |
| `bin_select` (0x51) / frame | **0** | — |
| `pred` (type-3 con bit 0) | **1.610,7** | 799 – 2.356 |
| `regs` escritos / frame | **154.861** | 134 k – 185 k |
| `dwords` / frame | **243.156** (950 KB) | ~243 k |
| `msaa` (RB_SURFACE_INFO[17:16]) | **0 (=1x)** | — |
| `pitch` | **1120 px** (0x460) | no 1280 |

### Opcodes type-3 por frame (histograma de 60 frames / 60)

| Opcode | Nombre | Por frame |
|---|---|---|
| `0x22` | `PM4_DRAW_INDX` | **~502** |
| `0x2F` | `PM4_LOAD_ALU_CONSTANT` | **~535** |
| `0x27` | `PM4_IM_LOAD` (microcódigo de shader) | **~433** |
| `0x2B` | — | ~33 |
| `0x50` / `0x51` | `SET_BIN_MASK` / `SET_BIN_SELECT` | **0** |

### Lectura

- **Tiling descartado con prueba de runtime**, no por inferencia. Si hubiera
  Predicated Tiling habría una máscara de bins antes de cada tile y el número de
  draws se multiplicaría por el número de tiles. No hay ni un paquete en 540
  frames.
- **MSAA descartado** como causa: la superficie principal va a 1x.
- **El cuello es de CPU**: 155 k `WriteRegister` por frame (≈5,9 M/s a 38 fps),
  433 re-subidas de microcódigo de shader y 535 cargas de constantes. Con 725
  draws, el driver reemite estado masivamente.
- Los **29 resolves** sí son un coste de GPU real, pero son consecuencia de 725
  draws con render targets intermedios, no de tiles.

---

## 3. `D3DDevice_BeginTiling` / `EndTiling` en `generated/`

### Estática

- **0 imports** `__imp__D3DDevice_*` en todo `generated/`: el driver D3D va
  enlazado estáticamente.
- **No está en `export_table.inc`** de ReXGlue.
- `tools/scan_tiling.py` recorre las 3.100+ funciones y encuentra **0**
  cabeceras PM4 de tiling (`0xC00050xx`–`0xC00063xx`).
- Los índices de registro llegan **por tabla en `.rdata`**, no como inmediatos:
  por eso el escaneo estático no puede decidir solo y hace falta el runtime.

### Direcciones identificadas (rango XDK D3D, 125 funciones)

| Dirección | Qué es | Evidencia |
|---|---|---|
| `sub_826B8620` | `D3DDevice_Swap` / Present | llama `__imp__VdSwap`, `VdPersistDisplay`; avanza `[dev+48]` |
| `sub_826B5548` | asignador del primary buffer | `[dev+48]`/`[dev+52]`, llama a `sub_826B6B38` si no cabe |
| `sub_826B6B38` | handler de ring lleno | vuelca/crece el buffer y resetea `[dev+48]` |
| `sub_826B75C0` | contador de anillo `(x+1) % 10` | vía `sub_826B75A8` |
| `sub_826B7F28` | empaquetador de comandos | escribe words con tags en `r1+96` |

---

## 4. Directivas `*_as_local` (técnica de `pgr4-native`)

`generated/neverdead_default/neverdead_init.cpp:16-25`:

```cpp
.codegen_flags = {
    .skip_lr = false,
    .ctr_as_local = false,
    .xer_as_local = false,
    .reserved_as_local = false,
    .skip_msr = false,
    .cr_as_local = false,
    .non_argument_as_local = false,
    .non_volatile_as_local = false,
},
```

| Directiva | Estado |
|---|---|
| `cr_as_local` | **false** |
| `ctr_as_local` | **false** |
| `xer_as_local` | **false** |
| `non_volatile_as_local` | **false** |
| `reserved_as_local` | **false** |

No están en `neverdead.toml` ni en el manifest: el recompilador las lee como
claves **de primer nivel** del TOML (`src/codegen/config.cpp:105-112`, merge
"last wins"). Para probar el 17,9 % que reporta `pgr4-native`:

```toml
# config/codegen.toml — añadir AL FINAL de includes del manifest
cr_as_local = true
ctr_as_local = true
xer_as_local = true
reserved_as_local = true
non_volatile_as_local = true
```

Requiere **codegen completo** (3.100+ funciones) + rebuild de las 123 TUs.
No se hizo en este turno: primero conviene tener el bench de CPU del §7.

---

## 5. Desfase invitado vs presentador

### Por qué ocurre

El único limitador es `NdApplyFramePacer()`, que corre en `NdPresentHook` (hilo
del presentador). El invitado llama a `NdVdSwapHook()` y sigue: **no hay
realimentación**. En gameplay su frame cuesta 26,3 ms → 38,0 fps, pero solo se
presentan 30: **8 de cada 38 frames se sobrescriben**.

### Los tres diseños probados

| Configuración | Invitado | Presentador |
|---|---|---|
| Sin pacing del invitado (baseline) | **38,0 FPS** (26,3 ms) | **30,0 FPS** |
| Pacing **antes** del `bl VdSwap` (contadores) | 16,8 | 11,0 ❌ |
| Pacing **después** del `bl VdSwap`, 30,0 exacto | **30,0** (33,33 ms) | 22,0 ❌ |
| Pacing después, +5 % headroom | 31,5 | 22,6 ❌ |

- Frenar **antes** del swap crea una **dependencia circular** con el presentador
  (Present se dispara desde el swap del invitado): solo lo rompe el timeout de
  150 ms → 89 picos de 100 ms por ventana. **Es el mismo patrón del cuelgue de
  `[SWAP] #3` de v4**, ahora explicado.
- Frenar **después** sí clava el invitado en 33,33 ms (30,0 FPS, 0 picos), pero
  el presentador cae a 22: el host presenta a su propio ritmo y, al no haber ya
  frames de sobra, se salta despertares.

### Estado entregado

`NdVdSwapPostHook` (hook mid-asm en `0x826B89A4`, la instrucción siguiente al
`bl VdSwap`) implementa pacing por tiempo, **opt-in**:

```bat
set ND_GUEST_THROTTLE=1     :: invitado a 30.0 FPS (coste: present 22)
set ND_GUEST_HEADROOM_PCT=5 :: margen sobre el objetivo
```

Por defecto **desactivado**. `0x82B31FBC` sigue siempre a `0.0f`, así que el
cuelgue de `[SWAP] #3` no puede reproducirse.

---

## 6. Grafo actualizado

| | v10 | v11 |
|---|---|---|
| Nodos | 703 | **710** |
| Aristas | 867 | **883** |
| Comunidades | 34 | **34** |
| `GRAFO_ARGON.txt` | 289 líneas | **290 líneas** (34 KB) |

Recoge los símbolos nuevos: `NdRingProbe()`, `NdPaceGuestFrame()`,
`NdVdSwapPostHook()`.

**Lo que el grafo dice que sigue abierto** (comunidades `[5]` y `[8]`):

- *PSO blocking compile stutter (Wait with infinite timeout in EndSubmission)*
- *ROV overdraw saturation at 95-99 % GPU*
- *Cuello de Botella CPU en Combate ReXGlue (80 % CPU / 48 FPS)*

La tercera es exactamente lo que confirman los números del §2, y ahora tiene
métrica: **155 k registros + 433 shaders por frame**.

---

## 7. Riesgos y siguiente paso

| Riesgo | Estado |
|---|---|
| Bench midiendo menú otra vez | **Mitigado**: `bench_ring.py` valida escena por luminancia |
| `ND_RING=1` en producción | Inocuo (solo lectura) pero cuesta CPU; no dejar activo |
| Pacing del invitado por defecto | **No** activado: cuesta 30→22 de presentación |
| `*_as_local` sin medir | No tocar hasta tener bench de CPU por frame |

**Siguiente paso con más retorno**, por orden:

1. **Instrumentar el coste de CPU por frame** (no solo FPS): tiempo en
   `WriteRegister` vs `LoadShader` vs resto. Sin eso, optimizar es adivinar.
2. **Atacar los 433 `PM4_IM_LOAD`**: si el juego re-sube el mismo microcódigo,
   un caché por hash en el host lo elimina sin tocar el invitado.
3. **`config/codegen.toml` con las 5 `*_as_local`** y A/B contra el bench nuevo.

---

## 8. Evidencia

| Archivo | Qué contiene |
|---|---|
| `docs/AUDITORIA_TILING_v11.md` | Informe técnico completo de las 4 tareas |
| `docs/assets/ring_gameplay_proof.png` | Captura del gameplay donde se midió |
| `docs/GRAFO_ARGON.txt` | Grafo en texto (710 nodos, 290 líneas) |
| `tools/bench_ring.py` | Arnés con detección de escena 3D |
| `tools/scan_tiling.py` | Escáner de firmas PM4 de tiling |
| `src/nd_hooks.cpp` | `NdRingProbe`, `NdVdSwapPostHook`, `NdPaceGuestFrame` |
| `config/mid_asm_hooks.toml` | Hooks en `0x826B89A0` (pre) y `0x826B89A4` (post) |

Commit: `c07aa8c` · 209 archivos versionados · `git status` limpio.
