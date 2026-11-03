# Auditoría: por qué no llegamos al menú ni se renderiza 3D

Fecha: 2026-10-23. Evidencia: `build_clang/logs/neverdead_004` … `neverdead_012`.
Oráculo de referencia: `xenia_oracle.log` (misma revisión de título: media id
`0x4B4E081F`, version `0x00000009`, base_version `0x5D5CA48B`).

---

## 1. Resumen ejecutivo

No llegamos al menú por **dos causas independientes y ambas probadas**, no por
"falta de GPU" ni por el backend D3D12:

1. **`MapBaseExeSections` destruía la imagen del invitado.** El XEX carga
   `.rdata`/`.data` correctamente; después, ese método copiaba encima
   `NeverDead_base.exe` 1:1 por RVA. Ese `.exe` **no es un volcado fiel** (es una
   imagen compactada, con bloques repetidos eliminados), así que sobrescribía
   punteros de función válidos con texto. Consecuencia: el sistema de objetos del
   motor leía basura y el arranque moría en `WINMAIN`. **Corregido y verificado.**

2. **Chunks `share_registers` emitidos como funciones.** `config/functions.toml`
   declara 897 bloques con `share_registers = true`; el árbol v1 los emite como
   `DEFINE_REX_FUNC` independientes. Un `bl` a uno de ellos ejecuta código que
   espera el banco de registros de su `parent`, y recibe el del llamante. Es la
   clase F2 del plan. **Medido con sonda: 473 candidatos.**

El 3D no se renderiza porque **el invitado nunca llama a `VdInitializeEngines`**:
cero hitos `Vd*` en 12 ejecuciones. El host D3D12 está listo desde el segundo 0,7
(RTX 2060, ROV, tiled resources tier 4) pero no recibe ni un paquete porque no
hay ring buffer.

---

## 2. La cadena que debería ocurrir (oráculo Xenia)

Todo en el hilo `F8000008` (hilo principal del XEX):

| # | Hito | Evidencia en `xenia_oracle.log` |
|---|---|---|
| M0 | Carga del módulo | `Loading module GAME:\default.xex` (l.409) |
| M1 | Hilos de sistema | `GPU Commands`, `GPU Frame limiter`, `XMA Decoder`, `Audio Worker` (l.392-403) |
| M2 | **`VdInitializeEngines`** | `VdInitializeEngines(51870000, 826BC2A0, 0, 8205BD18, 82059918)` (l.1622) |
| M3 | Callback de interrupción | `VdSetGraphicsInterruptCallback(826B6FB0, 40218080)` (l.1625) |
| M4 | **`VdInitializeRingBuffer`** | `VdInitializeRingBuffer(1FC9E000, 12)` → 4 KiB de ring (l.1638) |
| M5 | Write-back del RPTR | `VdEnableRingBufferRPtrWriteBack(1FCA703C, 6)` (l.1640) |
| M6 | Modo de vídeo | `VdSetDisplayMode(40000000)`, `VdQueryVideoMode`, `VdPersistDisplay` (l.4849-4867) |
| M7 | **GUI del menú** | `GAME:\GUIMenu\loadingInitial.guiBE` (l.3117) |
| M8 | Audio de GUI | `GAME:\GUIMenu\GUIAudio.asrBE` (l.4614) |
| M9 | **Archivos del juego** | `GAME:\misc\common.asrBE` (l.5442) |

Nota importante de orden: **el dispositivo gráfico se inicializa (M2-M5) ANTES
de montar `common.asrBE` (M9)**. Nuestro árbol hace lo contrario: entra en el
iterador de chunks (que es post-montaje) sin haber pasado por M2.

Ruta de código hacia M2 (en nuestro árbol):

```
(indirecto) → sub_8271D590 → sub_826AD938 → sub_826BA390 → sub_827BE084
                                                             = VdInitializeEngines
```

`sub_826BA390` es una función real (prólogo `mflr r12; bl 0x8278AD4C`), no un
chunk. La ruta existe y está bien formada; simplemente **nunca se entra en ella**.

---

## 3. Estado actual medido (`neverdead_012.log`)

| Métrica | Valor |
|---|---|
| `VdInitializeEngines` / `VdInitializeRingBuffer` / `VdSwap` | **0** |
| `Entering Asura main loop` | **0** |
| Montajes `*.asrBE` | **0** |
| Última línea sana | `WINMAIN 0 0 0` + `sub_8269BA98` + 2 allocs |
| Bucle infinito | `lr=0x821E0FBC` → `bctrl` con target `0x00000000` (6.406 en 15 s) |

El motor no pasa de la inicialización temprana. No hay VFS, no hay GUI, no hay
render.

---

## 4. Causa raíz #1 — `MapBaseExeSections` destruía la imagen (PROBADA)

Sonda añadida en `OnPostLoadXexImage`, antes y después del mapeo:

```
pre-map  obj@82144930 [+0]=822CE8F0 [+16]=824DDF90 [+32]=824DDFF0 ...
post-map obj=0x82144930 slot=0x82144940 value=0x006C0065
```

- **Antes** del mapeo: `[+16] = 0x824DDF90`, un puntero a función válido (y
  `[+0] = 0x822CE8F0`, vtable registrada en el árbol).
- **Después**: `0x006C0065` = bytes UTF-16 `"le"`, parte de la cadena
  `"...AllCollectables"` que en el `.exe` ocupa el RVA `0x144940`.

Es decir: **la imagen del XEX era correcta y el `.exe` la corrompía.** El `.exe`
está compactado: la escalera CRT aparece en él en `0x8278AD10` y en la imagen del
XEX en `0x8278CD10` (+0x2000), mientras `sub_82686058` coincide a desplazamiento
0. El desplazamiento no es constante, así que **ninguna copia 1:1 es válida**.

Efecto en cadena (log 009, antes del arreglo):

```
lr=0x824D9EAC  target=0x006C0065   ← el motor llamaba a la cadena "le" como función
```

**Arreglo aplicado:** `MapBaseExeSections` ahora **sólo rellena huecos** (palabras
que el loader del XEX dejó a cero). Cualquier dato no nulo de la imagen del XEX
es autoritativo. Resultado medido:

```
MapBaseExeSections holes-only completed (.rdata=10619, .pdata=0, .data=10174, .post=2337 palabras)
post-load probe obj=0x82144930 slot=0x82144940 value=0x824DDF90   ← correcto
```

y el error `target=0x006C0065` **desaparece por completo**.

> Efecto colateral detectado: `0x824DDF90` **no está en la tabla de funciones**
> (`neverdead_init.cpp`) ni en `functions.toml`. El runtime lo absorbe con un
> stub `r3=1`. Es un hueco real del seed que hay que cerrar.

---

## 5. Causa raíz #2 — chunks `share_registers` como funciones (PROBADA)

`config/functions.toml` declara `0x821E1098` con `parent = 0x821E0F70` y
`share_registers = true`. El árbol lo emite como función independiente, y
`sub_822CB8C0` lo invoca con `bl` en `0x822CB914`.

Sonda `CI-PROBE` en el `bctrl` del bucle (`lr=0x821E0FBC`):

```
#1 r24=1 r26=0x82145548 r27=0x8287C424 r28=0x8287C438 r29=0x18 [r27+20]=0x556E7061 target=0
#2 r24=2 r28=0x8287C43C ...
#3 r24=3 r28=0x8287C440 ...
```

Lectura:

- `r24` incrementa 1,2,3 y `r28` incrementa de 4 en 4 → **el bucle en sí funciona**.
- `[r27+20] = 0x556E7061` = bytes `"Unpa"` → **texto**, no un offset. El objeto al
  que apunta `r27` no es el contenedor que el bucle espera.
- Como consecuencia, `lwzx r3, r28, r11` calcula `0x8287C438 + 0x556E7061` →
  fuera de rango → `r3 = 0` → vtable nula → `bctrl 0` → bucle infinito.

El `parent` (`sub_821E0F70`) sí se llama correctamente desde `sub_821E0520`
(`r3=r30, r4=r31, r5=r29`), y es él quien fija `r27 = r3`. El camino roto es el
otro: `sub_822CB8C0` (función con prólogo propio, marco `0x110`) entra en el
chunk con **su** `r27`, que es otro objeto.

Alcance de la clase (`tools/audit_share_registers.py`):

```
chunks share_registers en config: 897
presentes en el arbol:            603
sin prologo propio:               588
invocados desde otra funcion:     486
ROTOS (sin prologo + de fuera):   473
```

---

## 6. Por qué eso implica exactamente cero 3D

```
chunk roto → el motor no completa la inicialización de objetos
           → no monta common.asrBE / Text / Fonts
           → no entra en la ruta sub_8271D590 → sub_826BA390
           → NO llama VdInitializeEngines        (M2 ✗)
           → NO llama VdInitializeRingBuffer     (M4 ✗)  ← sin ring no hay PM4
           → NO emite ningún paquete PM4         (M6 ✗)
           → NO llama VdSwap                     (M7 ✗)  ← sin swap no hay frame
           → NO carga loadingInitial.guiBE       (M7 ✗)  ← sin GUI no hay menú
```

El backend D3D12 del host no es el problema: se crea a los 0,7 s y reporta ROV y
tiled resources tier 4. El problema es que **el invitado nunca le habla**.

---

## 7. Lo que ya está corregido en esta sesión

| Cambio | Fichero | Efecto medido |
|---|---|---|
| Escalera CRT `0x8278BD10..BDB0` restaurada a `__savegprlr_*`/`__restgprlr_*` | `apply_crt_gpr_helpers.py` (target `neverdead_crt_gpr_helpers`) | elimina el drift `r1 += 0x70` por llamada (849 sitios), `lr = 0xBEBEBEBE` y el salto a `"Fall"` |
| `MapBaseExeSections` → sólo rellena huecos | `src/neverdead_app.h` | `slot=0x824DDF90` correcto; desaparece `target=0x006C0065` |
| Cola del main loop: `b 0x8278BD7C` = `__restgprlr_21` | `src/nd_main_loop.h` | la cola ya no encadena cuatro "fragmentos" inexistentes |
| Seed `ctx.r29` retirado | `apply_entrypoint_bridge.py` | ya no envenena `r29` de los llamantes |
| Auditoría de la clase F2 | `tools/audit_share_registers.py` | 473 candidatos cuantificados |

Comparativa de errores por ejecución:

| Log | AV | TRAP-OOB | Primer error |
|---|---|---|---|
| 004 (antes) | sí, `0x70190050` | 27.034 | `lr=0x824D9EAC target=0x006C0065` |
| 009 | 21 | 7.338 | `0x006C0065` + iterador |
| 012 (actual) | 21 | 6.406 | **solo** el iterador (`0x821E0FBC`) |

---

## 8. Plan ordenado por impacto

### P0 — Cerrar los huecos del seed (barato, alto impacto)

1. `0x824DDF90` no existe en la tabla. Es el primer puntero que el motor invoca
   tras el arreglo del `.rdata`. Hay que registrarlo/implementarlo, no dejar el
   stub `r3=1`.
2. Repetir el barrido: cualquier puntero de función que la imagen del XEX
   contiene y que **no** esté en `neverdead_init.cpp` es un hueco. Se puede
   derivar de `.rdata` (valores en `[0x821A0000, 0x827C05E4)` que no estén
   registrados).

### P1 — Resolver la clase `share_registers` de una vez

No parchear los 473 uno a uno. Dos vías, en este orden:

1. **Regenerar con `config/functions.toml`** (árbol v2, ya generado en
   `generated/neverdead_default_v2`). El v2 ya contiene `0x821E0FA0` como
   etiqueta interna de `sub_821E0F70` (en v1 hubo que puentearlo con
   `apply_821e0fa0_fix.py`). Comparar logs v1 vs v2 con la misma semilla.
2. Si el v2 no basta: emitir los chunks `share_registers` como **etiquetas dentro
   del cuerpo del `parent`** (un solo `DEFINE_REX_FUNC`) y convertir los `bl`
   externos en errores de build. Eso es exactamente el build-gate que propone
   Case Zero en el plan (F2).

### P2 — Medir M2-M9 con la instrumentación que ya existe

Los hooks `VdInitializeRingBuffer`, `VdSwap`, `VdSetGraphicsInterruptCallback` y
`VdInitializeEngines` **ya están escritos** en `src/nd_import_diag.h`. En cuanto
el arranque pase de la inicialización, basta contar sus líneas para saber en qué
hito estamos. No hace falta instrumentación nueva.

### P3 — Sólo después: renderer

`gpu_plugin = xenos` + D3D12 ROV ya está configurado y el dispositivo se crea.
No tocar nada de GPU hasta que aparezca `VdInitializeRingBuffer` en el log.

---

## 9. Cómo reproducir y verificar

```bash
# build (el .ps1 falla en sandbox: vcvarsall invoca reg.exe)
export INCLUDE="<MSVC>/include;<SDK>/Include/<v>/ucrt;...shared;...um;...winrt;...cppwinrt"
export LIB="<MSVC>/lib/x64;<SDK>/Lib/<v>/ucrt/x64;<SDK>/Lib/<v>/um/x64"
cmake --build build_clang --config Release

python run_game_watchdog.py 20
grep -E "pre-map|post-load probe|holes-only|CI-PROBE" build_clang/logs/neverdead_*.log | tail
python tools/audit_share_registers.py --list 20
```

Invariantes que deben cumplirse siempre:

1. `pre-map [+16]` debe ser igual a `post-load probe value` (si difieren, algo
   está sobrescribiendo la imagen).
2. `post-load probe value` debe ser un puntero en `[0x821A0000, 0x827C05E4)`.
3. Un helper CRT nunca modifica `r1`.
4. `CI-PROBE [r27+20]` debe ser un offset pequeño (< 0x100000), nunca texto.

---

## 10. Apéndice — correcciones tras auditoría adversarial (2026-10-23)

Un revisor independiente intentó falsar las conclusiones de este documento.
Encontró dos errores reales, ya verificados y corregidos.

### 10.1 El ladder CRT no está donde dije

Afirmé que `0x8278BD10..0x8278BDB0` era la escalera `__savegprlr_*`. **Es falso.**
`.pdata` —la tabla de funciones, prueba independiente del árbol— tiene entrada en
`0x8278CD10` con extensión exacta `0x50` y **ninguna** en `0x8278AD10` ni
`0x8278BD10`. El ladder real está en `0x8278CD10..0x8278CDB0`.

Mi "prueba" (2930 pares diagonales) era un artefacto del mapeo plano del árbol.
Detalle y consecuencias en `docs/CRT_HELPERS.md`.

### 10.2 `fill_holes` usaba el offset de fichero equivocado

Usaba `fseek(f, rva)`. Medido con anclas de contenido reales, el desplazamiento
entre el `.exe` y la imagen guest es **dependiente de región**:

| Zona | RVA | Desplazamiento |
|---|---|---|
| `.data` | `0x7DBFA8`, `0x7EA280`, `0x7EA2B0` | `0x0000` |
| `.rdata` baja | `0x04AFC0`, `0x04D350`, `0x04D360` | `0x0000` |
| `.rdata` media | `0x144930` | `0x1000` |
| `.rdata` alta | `0x49EF98`, `0x49EFA8` | `0x2000` |

No existe offset fijo por sección. La política ahora es conservadora:

- `.data` → se rellena con `offset = rva` (verificado 3/3 anclas).
- `.rdata` → **no se inyecta**: el loader del XEX la carga correctamente (lo
  demuestra la sonda `pre-map`: los punteros ya eran válidos antes de tocar nada)
  y su offset correcto no es conocible. Reactivable con `ND_FILL_RDATA=1`.
- `.pdata` → no se inyecta.
- `.post` → **no se inyecta**: el rango cae más allá de la última sección del
  `.exe`. Reactivable con `ND_FILL_POST=1`.

### 10.3 Otros defectos menores corregidos

- `neverdead_recomp` ahora depende **explícitamente** de `neverdead_crt_gpr_helpers`
  en `CMakeLists.txt` (antes el orden sólo se sostenía por transitividad).
- Docstrings y documentos corregidos para decir la verdad sobre el alcance.
- `neverdead_main_loop` empieza en `0x823C5DAC`, **0x2C bytes dentro** de la
  función real (`0x823C6D80`), saltándose su prólogo (`stwu r1,-0xB0`) pero
  conservando su epílogo. Pendiente de verificar en runtime: el boot muere antes
  de llegar. **Riesgo abierto.**

### 10.4 Lo que se mantiene

- La corrupción de `.rdata` por copia 1:1 era real y su eliminación es correcta:
  los `TRAP-OOB target=0x006C00xx` (texto UTF-16 ejecutado como código)
  desaparecen.
- La cola del main loop es semánticamente correcta (marco `0xB0`, salva
  `r21..r31`), verificado contra la imagen real.
- El diagnóstico del iterador de chunks como "objetivo de `bl` mal etiquetado"
  es más preciso que "banco de registros": el `bl 0x821e1098` de `sub_822CB8C0`
  está en región `+0x1000`, su objetivo verdadero sería `0x821E2098`.
