# Auditoría v11 — Predicated Tiling, Resolves y Desfase 30/35.7

> Medido sobre `build_clang/neverdead.exe` (38.8 MB) con la sonda de ring PM4
> (`ND_RING=1`) y el pacer del host. Escena: hospital, auto-avance a los 45 s.

---

## 1. Qué ejecuta `rexgpu-xenos` por frame (medición real)

La sonda lee el *primary buffer* del driver D3D del invitado. Punto de anclaje:
`sub_826B5548` es el asignador del ring —

```c
r11 = [dev+48];                       // puntero de escritura
r10 = [dev+52];                       // fin del buffer
if (r11 + n*4 > r10) sub_826B6B38(dev);  // ring lleno -> volcar/crecer
return [dev+48];
```

y `sub_826B8620` (Present) avanza `[dev+48]` **después** del `VdSwap`
(`stw r11,48(r31)` con `r11 = r30+256`). Por eso, en el hook de `VdSwap`,
`[dev+48]` marca el final exacto de los comandos del frame.

### Ventanas de gameplay (swaps 901–1081, 60 frames cada una)

| Métrica | Valor | Lectura |
|---|---|---|
| `draws` / frame | **50–52** | ~1 draw por cada 3 paquetes `0x22`; el resto son state sets |
| `resolves` / frame | **19–20** | escrituras de `RB_MODECONTROL` con `edram_mode = 6 (kCopy)` |
| `bin_mask` (0x50) / frame | **0** | **no hay SET_BIN_MASK** |
| `bin_select` (0x51) / frame | **0** | **no hay SET_BIN_SELECT** |
| `pred` (type-3 con bit 0) | 32–34 | predicación por bins (oclusión), no tiling |
| `msaa` (RB_SURFACE_INFO[17:16]) | **0 (=1x)** al final del frame; `1 (=2x)` transitorio en algunas superficies |
| `pitch` | **1120 px** (0x460) | no 1280 |
| `regs` / frame | **19.000–30.500** | el coste real es de CPU: escrituras de registro masivas |
| `dwords` / frame | 42.880 (171 KB) | ~4 dwords por registro escrito |

**Conclusión T1:** a 1280×720 el juego **no** activa Predicated Tiling
(0 paquetes de bin mask/select en 180 frames medidos) y la superficie
principal va a **1x**. Lo que sí hace es **~20 resolves por frame** y
~19–30 k escrituras de registro. El sobrecoste no es "varios tiles + 2x MSAA";
es **volumen de resolves + spam de registros**.

---

## 2. `D3DDevice_BeginTiling` / `EndTiling` en `generated/`

### Lo que dice la estática

- **No existe como import:** 0 referencias a `__imp__D3DDevice_*` en todo
  `generated/` (el driver D3D va enlazado estáticamente).
- **No está en la tabla de exports** de ReXGlue (`export_table.inc`): no hay
  `D3DDevice_BeginTiling` ni `EndTiling`.
- **No hay cabeceras PM4 de tiling literales:** `tools/scan_tiling.py` recorre
  las 3.100+ funciones y encuentra **0** coincidencias de
  `0xC00050xx`/`0xC00051xx`/`0xC00060xx`–`0xC00063xx`.
- Los índices de registro llegan **por tabla en `.rdata`**, no como inmediatos:
  `RB_SURFACE_INFO` (0x2000) y `RB_MODECONTROL` (0x2208) no aparecen como
  literales en el código del driver.

### Direcciones identificadas en el rango XDK D3D (125 funciones)

| Dirección | Qué es | Evidencia |
|---|---|---|
| `sub_826B8620` | `D3DDevice_Swap` / Present | llama `__imp__VdSwap`, `VdPersistDisplay`; avanza `[dev+48]` |
| `sub_826B5548` | asignador del primary buffer | `[dev+48]`/`[dev+52]`, llama a `sub_826B6B38` si no cabe |
| `sub_826B6B38` | handler de ring lleno | vuelca/crece el buffer y resetea `[dev+48]` |
| `sub_826B75C0` | contador de anillo `(x+1) % 10` | vía `sub_826B75A8` |
| `sub_826B7F28` | empaquetador de comandos | escribe words con tags en `r1+96` |

**Conclusión T2:** el juego **no llama a `BeginTiling`/`EndTiling` por frame**.
La prueba no es estática (el codegen no permite verlo: los headers se construyen
en runtime desde tablas) sino **empírica**: si hubiera tiling, el ring tendría
`SET_BIN_MASK`/`SET_BIN_SELECT` antes de cada tile y el número de draws se
multiplicaría por el número de tiles. No hay ni uno.

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
en `neverdead.toml` ni en `neverdead_manifest.toml`: el recompilador las lee
como claves **de primer nivel** del TOML (`src/codegen/config.cpp:105-112`,
merge "last wins"). Para activarlas sin tocar el resto:

```toml
# config/codegen.toml  (añadir AL FINAL de includes en el manifest)
cr_as_local = true
ctr_as_local = true
xer_as_local = true
reserved_as_local = true
non_volatile_as_local = true
```

Requiere **re-ejecutar el codegen completo** (3.100+ funciones) y rebuild de las
123 TUs. No se hizo en este turno.

---

## 4. Desfase 35.7 FPS (invitado) vs 30.0 (presentador)

### Por qué ocurre

El único limitador es `NdApplyFramePacer()`, que corre en `NdPresentHook`
(hilo del presentador). El invitado llama a `NdVdSwapHook()` y sigue: **no hay
realimentación**. Si el frame del invitado cuesta 28 ms, produce 35.7 fps
aunque solo se presenten 30 — 1 de cada 6 frames se sobrescribe.

### Qué se probó y qué midió

| Configuración | Invitado | Presentador |
|---|---|---|
| Sin pacing del invitado (baseline) | **34.2 FPS** (29.2 ms) | **30.0 FPS** |
| Pacing **antes** del `bl VdSwap` (contadores) | 16.8 FPS | 11.0 FPS ❌ |
| Pacing **después** del `bl VdSwap`, 30.0 exacto | **30.0 FPS** (33.33 ms) | 22.0 FPS ❌ |
| Pacing después, +5 % headroom | 31.5 FPS | 22.6 FPS ❌ |

- Frenar **antes** del swap crea una dependencia circular con el presentador
  (Present se dispara desde el swap del invitado): solo lo rompe el timeout de
  150 ms → 89 picos de 100 ms por ventana. Es el mismo patrón que el cuelgue
  de `[SWAP] #3` de v4.
- Frenar **después** sí clava el invitado en 33.33 ms (30.0 FPS, 0 picos),
  pero el presentador cae a 22: el host presenta a su propio ritmo y, al no
  haber ya frames de sobra, se salta despertares.

### Estado en el que se entrega

`NdVdSwapPostHook` (hook mid-asm en `0x826B89A4`, la instrucción siguiente al
`bl VdSwap`) implementa el pacing por tiempo, **opt-in**:

```bat
set ND_GUEST_THROTTLE=1     # invitado a 30.0 FPS (coste: present 22)
set ND_GUEST_HEADROOM_PCT=5 # margen sobre el objetivo
```

Por defecto **está desactivado**: con los datos en la mano, pagar 30→22 de
presentación para ahorrar 4 fps de invitado no compensa. `0x82B31FBC` sigue
siempre a `0.0f`, así que el cuelgue de `[SWAP] #3` no puede reproducirse.

---

## 5. Reproducir

```bat
set ND_RING=1
python tools\bench_v6.py A
python tools\scan_tiling.py
```
