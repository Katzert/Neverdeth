# Auditoría v11 — Predicated Tiling, Resolves y Desfase 30/38

> **Corrección importante.** La primera pasada midió con el protocolo de 45 s
> fijos y las ventanas cayeron en el menú/carga (50 draws, 19 resolves). Con
> detección de escena 3D por luminancia (`tools/bench_ring.py`) los números
> reales de gameplay son **14× mayores**. Este documento usa solo las ventanas
> verificadas: 9 ventanas `[RING]` posteriores a la confirmación visual, con
> captura de prueba en `docs/assets/ring_gameplay_proof.png` (pasillo del
> hospital, personaje, HUD, god rays).

---

## 1. Qué ejecuta `rexgpu-xenos` por frame en gameplay 3D

La sonda lee el *primary buffer* del driver D3D del invitado. Anclaje:
`sub_826B5548` es el asignador del ring —

```c
r11 = [dev+48];                          // puntero de escritura
r10 = [dev+52];                          // fin del buffer
if (r11 + n*4 > r10) sub_826B6B38(dev);  // ring lleno -> volcar/crecer
return [dev+48];
```

y `sub_826B8620` (Present) avanza `[dev+48]` **después** del `VdSwap`
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
| `0x50`/`0x51` | `SET_BIN_MASK` / `SET_BIN_SELECT` | **0** |

**Conclusiones T1:**

1. **No hay Predicated Tiling.** Cero paquetes `SET_BIN_MASK`/`SET_BIN_SELECT`
   en 540 frames de gameplay. Si lo hubiera, los draws se multiplicarían por
   el número de tiles y habría una máscara antes de cada tile.
2. **No hay 2x MSAA global.** `msaa_samples = 0` (1x) al final de cada frame;
   el `1` (2x) aparece solo de forma transitoria en superficies auxiliares.
3. **El coste real es de CPU, no de GPU por tiles:** ~155 k escrituras de
   registro + 433 cargas de microcódigo de shader + 535 cargas de constantes
   **por frame**. A 38 fps eso son ~5,9 M escrituras/s y ~16 k subidas de
   shader/s.
4. **29 resolves por frame** (no 19–20): el volumen de resolves sí es alto,
   pero es consecuencia de 725 draws con render targets intermedios, no de
   tiles.

---

## 2. `D3DDevice_BeginTiling` / `EndTiling` en `generated/`

### Lo que dice la estática

- **No existe como import:** 0 referencias a `__imp__D3DDevice_*` en todo
  `generated/` (el driver D3D va enlazado estáticamente).
- **No está en la tabla de exports** de ReXGlue (`export_table.inc`).
- **No hay cabeceras PM4 de tiling literales:** `tools/scan_tiling.py` recorre
  las 3.100+ funciones y encuentra **0** coincidencias de
  `0xC00050xx`/`0xC00051xx`/`0xC00060xx`–`0xC00063xx`.
- Los índices de registro llegan **por tabla en `.rdata`**, no como inmediatos.

### Direcciones identificadas en el rango XDK D3D (125 funciones)

| Dirección | Qué es | Evidencia |
|---|---|---|
| `sub_826B8620` | `D3DDevice_Swap` / Present | llama `__imp__VdSwap`, `VdPersistDisplay`; avanza `[dev+48]` |
| `sub_826B5548` | asignador del primary buffer | `[dev+48]`/`[dev+52]`, llama a `sub_826B6B38` si no cabe |
| `sub_826B6B38` | handler de ring lleno | vuelca/crece el buffer y resetea `[dev+48]` |
| `sub_826B75C0` | contador de anillo `(x+1) % 10` | vía `sub_826B75A8` |
| `sub_826B7F28` | empaquetador de comandos | escribe words con tags en `r1+96` |

**Conclusión T2:** el juego **no llama a `BeginTiling`/`EndTiling` por frame**.
La prueba es empírica: si hubiera tiling, el ring tendría `SET_BIN_MASK` /
`SET_BIN_SELECT` antes de cada tile. No hay ni uno en 540 frames.

---

## 3. Directivas `*_as_local` (estado actual)

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

Las cinco están **desactivadas** (y `non_argument_as_local` también). No están
en ningún TOML: el recompilador las lee como claves **de primer nivel**
(`src/codegen/config.cpp:105-112`, merge "last wins"). Para activarlas:

```toml
# config/codegen.toml  (añadir AL FINAL de includes en el manifest)
cr_as_local = true
ctr_as_local = true
xer_as_local = true
reserved_as_local = true
non_volatile_as_local = true
```

Requiere **re-ejecutar el codegen completo** y rebuild de las 123 TUs.

---

## 4. Desfase 38.0 FPS (invitado) vs 30.0 (presentador)

### Por qué ocurre

El único limitador es `NdApplyFramePacer()`, que corre en `NdPresentHook`
(hilo del presentador). El invitado llama a `NdVdSwapHook()` y sigue: **no hay
realimentación**. En gameplay su frame cuesta 26,3 ms → 38,0 fps, pero solo se
presentan 30: **8 de cada 38 frames se sobrescriben** (21 % de trabajo tirado).

### Qué se probó y qué midió

| Configuración | Invitado | Presentador |
|---|---|---|
| Sin pacing del invitado (baseline) | **38,0 FPS** (26,3 ms) | **30,0 FPS** |
| Pacing **antes** del `bl VdSwap` (contadores) | 16,8 | 11,0 ❌ |
| Pacing **después** del `bl VdSwap`, 30,0 exacto | **30,0** (33,33 ms) | 22,0 ❌ |
| Pacing después, +5 % headroom | 31,5 | 22,6 ❌ |

- Frenar **antes** del swap crea una **dependencia circular** con el presentador
  (Present se dispara desde el swap del invitado): solo lo rompe el timeout de
  150 ms → 89 picos de 100 ms por ventana. Es el mismo patrón del cuelgue de
  `[SWAP] #3` de v4.
- Frenar **después** sí clava el invitado en 33,33 ms (30,0 FPS, 0 picos), pero
  el presentador cae a 22: el host presenta a su propio ritmo y, al no haber ya
  frames de sobra, se salta despertares.

### Estado en el que se entrega

`NdVdSwapPostHook` (hook mid-asm en `0x826B89A4`, la instrucción siguiente al
`bl VdSwap`) implementa el pacing por tiempo, **opt-in**:

```bat
set ND_GUEST_THROTTLE=1     # invitado a 30.0 FPS (coste: present 22)
set ND_GUEST_HEADROOM_PCT=5 # margen sobre el objetivo
```

Por defecto **está desactivado**: con los datos en la mano, pagar 30→22 de
presentación para ahorrar 8 fps de invitado no compensa. `0x82B31FBC` sigue
siempre a `0.0f`, así que el cuelgue de `[SWAP] #3` no puede reproducirse.

---

## 5. Dónde mirar ahora (lo que dicen los números)

Ordenado por impacto medido, no por hipótesis:

1. **154.861 escrituras de registro por frame.** El `WriteRegister` del host
   hace dispatch por índice en cada una. Es el mayor coste de CPU del frame y
   nadie lo había medido.
2. **433 `PM4_IM_LOAD` por frame** (microcódigo de shader re-subido). Cada uno
   pasa por `LoadShader` → hash → búsqueda en caché.
3. **535 `PM4_LOAD_ALU_CONSTANT`** por frame.
4. **29 resolves** por frame: el segundo coste de GPU real, tras 725 draws.

Nada de esto se arregla con Predicated Tiling ni con CVars de GPU.

---

## 6. Reproducir

```bat
python tools\bench_ring.py     :: deteccion de gameplay + captura + ventanas RING
python tools\scan_tiling.py    :: firmas de tiling en generated/
```
