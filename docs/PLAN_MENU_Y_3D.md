# Plan para llegar al menú y a la generación 3D

Fecha: 2026-10-24. Basado en el estado medido de `build_clang/logs/neverdead_015.log`.

---

## 0. Dónde estamos hoy (datos, no impresiones)

| Hito | Estado | Evidencia |
|---|---|---|
| M0 Entrada del XEX / WINMAIN | ✅ | `WINMAIN 0 470162432 4278583040` |
| M1 Heap / imagen / VFS | ✅ | `VFS game: mapped`, `VFS fallback aliases registered` |
| M2 Dispositivo D3D12 del host | ✅ | `Direct3D 12 device … ROV: yes, tiled resources: tier 4` a los 0,7 s |
| M3 Init de objetos del motor | ✅ | `ALLOC 52`, `ALLOC 3146752`, `sub_8269BA98`, `DEC8`, `sub_823D1588` |
| — Carga de recursos | 🟡 **entra y falla** | `sub_82673178 intercepted … returning r3=0 (resource not found)` |
| M4 `VdInitializeEngines` | ❌ | 0 ocurrencias |
| M5 `VdInitializeRingBuffer` | ❌ | 0 ocurrencias |
| M6 Paquetes PM4 | ❌ | 0 |
| M7 `VdSwap` #1 | ❌ | 0 |
| M8 `Translated N shaders` | ❌ | `Translated 0 shaders` |
| M9 Menú (`loadingInitial.guiBE`) | ❌ | 0 |

Avance medido en esta sesión: el motor pasó de morir en la inicialización de
objetos (6 535 líneas de log) a entrar en la carga de recursos (29 940 líneas).

**Bloqueante actual:** bucle de 29 844 iteraciones en `0x821E8E38`, dentro de
`sub_821E8DF0`, con `bctrl` a target 0. El sitio hace:

```
lwz  r10, 0(r28)      ; r10 = [objeto]     <- vtable
lwz  r9,  4(r10)      ; r9  = [vtable+4]   <- metodo virtual
mtctr r9 ; bctrl      ; target 0  => [vtable+4] == 0
```

`r28 = r3` de entrada, es decir el objeto que el llamante pasó. La vtable existe
pero su segundo slot está a cero, o el objeto es un bloque de ceros.

---

## 1. Cadena objetivo (oráculo Xenia, mismo título/versión)

```
WINMAIN
  └─ init de objetos del motor            ← ✅ ya pasamos
       └─ carga de recursos / archivos    ← 🟡 AQUÍ ESTAMOS
            └─ VdInitializeEngines        ← ❌ puerta del 3D
                 └─ VdSetGraphicsInterruptCallback
                      └─ VdInitializeRingBuffer(phys, log2)   ← sin esto no hay PM4
                           └─ PM4 (comandos de render)
                                └─ VdSwap #1                   ← primer frame
                                     └─ loadingInitial.guiBE   ← MENÚ
```

Orden importante que deduce el oráculo: **el dispositivo gráfico se inicializa
ANTES de montar `common.asrBE`** (`VdInitializeEngines` en la línea 1622 del
oráculo, `common.asrBE` en la 5442). Nosotros entramos por la carga de recursos
primero, así que hay un tramo de init gráfica que aún no hemos recorrido.

Ruta de código hacia M4, ya localizada en el árbol:

```
(indirecto) → sub_8271D590 → sub_826AD938 → sub_826BA390 → sub_827BE084
                                                              = VdInitializeEngines
```

`sub_826BA390` es una función real (prólogo `mflr r12; bl 0x8278AD4C`). La ruta
existe y está bien formada: **nunca se entra en ella**.

---

## 2. Fases

### Fase A — Desbloquear la carga de recursos  ·  desatascador inmediato

**A1. Entender el `bctrl` a 0 de `sub_821E8DF0`.**
Instrumentar una vez (como el `CI-PROBE`): volcar `r3`, `r4`, `[r3]`, `[r3]+4`,
`[r29]`, `[r29]+4`, `r30` en la primera iteración. Con eso se sabe si el objeto
es nulo, si es un bloque de ceros, o si la vtable es real y el slot está a 0.
Herramienta: misma técnica que `src/nd_indirect_trap.h` (rama acotada por `lr`).

**A2. Revisar el hook `sub_82673178`.**
Está en `src/nd_import_diag.h:620` y es un **stub del proyecto** que devuelve
`r3 = 0` con el comentario *"Prevents corrupted stream dereference … breaks
30M-iteration loop"*. Fue un parche sintomático. Hay que determinar:
- ¿el recurso que pide existe en `assets/`? Si sí, el hook sobra y debe delegar
  a la implementación real.
- ¿el hook es la causa de que el objeto llegue sin vtable?

**A3. Cerrar `0x824DDF90`.**
Aparece dos veces como `unregistered indirect call … (handled r3=1)`. No está en
`neverdead_init.cpp` ni en `config/functions.toml`. Es un hueco real del seed: el
runtime lo absorbe con un stub. Hay que registrarlo/implementarlo; si el motor
depende de su resultado, el stub `r3=1` puede estar envenenando el flujo.

**Aceptación Fase A:** el log muestra montaje de archivos
(`common.asrBE`, `Text/English.asrBE`, `Fonts/buttons.asrBE`) y **0 TRAP-OOB**
en el camino de arranque.

---

### Fase B — Cerrar los desajustes de rama del camino caliente

`tools/derive_shift_map.py` mide **32 222 de 90 901 ramas** con
`shift(sitio) != shift(destino)`. No se arreglan a mano: se priorizan.

**B1. Lista priorizada.** Cruzar el escaneo de desajustes con las direcciones que
el log demuestra que se ejecutan (las que aparecen en `lr=` de los TRAP-OOB y en
los `ctx.lr` del camino). Eso reduce 32 222 a las decenas que importan.

**B2. Arreglo mecánico por lotes.** Para cada rama desajustada, reescribir el
destino plano a `destino + shift(sitio)`, igual que se hizo con
`apply_821e2098_lookup.py`. Un script, idempotente, con ledger.

**B3. Build-gate.** Añadir a CMake un check que falle si una rama apunta a un
destino cuyo `shift` difiere del del sitio (es el build-gate que propone Case
Zero en el plan original).

**Aceptación Fase B:** 0 TRAP-OOB en 20 s de arranque.

---

### Fase C — Alcanzar los hitos `Vd*` (la puerta del 3D)

La instrumentación **ya existe** en `src/nd_import_diag.h`: hooks de
`VdInitializeEngines`, `VdInitializeRingBuffer`, `VdSetGraphicsInterruptCallback`,
`VdSetDisplayMode`, `VdSwap`. No hace falta añadir nada: basta contar líneas.

**C1. M4** — que el log muestre `VdInitializeEngines CALLED!`.
**C2. M5** — `VdInitializeRingBuffer phys_base=0x… log2_size=…`. Crítico: sin ring
buffer no hay PM4. Verificar que `phys_base != 0` (el hook ya tiene fallback
`& 0x1FFFFFFF` para `MmGetPhysicalAddress`).
**C3. M5b** — `VdGetSystemCommandBuffer` y `VdInitializeScalerCommandBuffer`: el
oráculo los marca `kStub`/`kSketchy` en Xenia, pero si el juego valida los
marcadores `0xBEEF0000/1` hay que implementarlos de verdad.
**C4. M6** — contador de paquetes PM4 escritos en el ring.
**C5. M7** — `VdSwap #1 frontBuffer=0x…`.
**C6. M8** — `Translated N shaders` con N > 0.
**C7. M9** — `GAME:\GUIMenu\loadingInitial.guiBE` → **menú**.

**Aceptación Fase C:** aparecen M4, M5 con `phys != 0`, y M7.

---

### Fase D — Render y paridad

**D1.** `gpu_plugin = xenos` + D3D12 ROV (perfil conservador de FM4/Tekken 6),
`native_2x_msaa=false`, `render_target_path_d3d12` por defecto. Ya está
configurado; **no tocar nada de GPU hasta que exista M5**.
**D2.** Cache de shaders offline (estilo Burnout/Xerenge) para no depender del
primer arranque.
**D3.** Vulkan después, con `fsi` (el gotcha de Asura's Wrath en NVIDIA).

**Aceptación Fase D:** primer frame presentado y menú visible.

---

## 3. Orden y dependencias

```
Fase A (desbloquear recursos)  ← desatascador, sin esto nada avanza
   └─ Fase B (desajustes de rama del camino caliente)
        └─ Fase C (Vd* → PM4 → Swap → menú)
             └─ Fase D (render/paridad)
```

A y B se solapan: cada branch desajustado que se arregle puede destrabar otro
punto de A. Iterar A→B→A→B midiendo siempre con el mismo watchdog de 20 s.

---

## 4. Deuda de fondo que condiciona todo

`NeverDead_base.exe` no es un volcado fiel: su contenido está desplazado respecto
a la imagen guest de forma **dependiente de región** (0 / +0x1000 / +0x2000).
Eso es el origen de:

- 32 222 ramas con destino mal etiquetado,
- los 473 chunks `share_registers` emitidos como funciones,
- las "transcripciones literales" que fabricaron cuerpos inválidos.

**Arreglo de raíz:** reconstruir el seed con el mapa correcto derivado de `.pdata`
(ya tenemos la herramienta). Eso haría innecesarios los parches de compensación.
Es una decisión tuya porque implica regenerar el árbol.

---

## 5. Reglas de trabajo (para no repetir la espiral)

1. **Una variable por iteración**, con la misma semilla de log y el mismo
   watchdog de 20 s.
2. **Nada de parches a ciegas.** Cada parche cita la línea de log que lo
   justifica y se anota en el ledger.
3. **`.pdata` manda** sobre cualquier otra fuente para saber dónde empieza una
   función y cuánto mide.
4. **`tools/derive_shift_map.py` antes de tocar un destino de rama.**
5. **No stubear lo que no se entiende.** El hook `sub_82673178` y el handler
   `0x824F6DB8` son parches sintomáticos que ocultan la causa; cada uno hay que
   convertirlo en la implementación real o retirarlo.
6. **Verificar el XEX/TU antes de culpar al codegen.**

---

## 6. Cómo medir cada fase

```bash
cmake --build build_clang --config Release
python run_game_watchdog.py 20

# desajustes de rama
python tools/derive_shift_map.py --stats
python tools/derive_shift_map.py --branch <sitio> <destino>

# chunks share_registers
python tools/audit_share_registers.py --list 20

# hitos (uno por fase)
grep -c "VdInitializeEngines\|VdInitializeRingBuffer\|VdSwap" build_clang/logs/neverdead_*.log
grep -c "asrBE\|loadingInitial" build_clang/logs/neverdead_*.log
grep -c "Translated [1-9]" build_clang/logs/neverdead_*.log
grep -c "TRAP-OOB" build_clang/logs/neverdead_*.log
```
