# HANDOFF para Gemini Argon — NeverDead X360→x64

> Documento de traspaso. Está escrito para que **no necesites esta conversación**:
> todo lo que hace falta para continuar está aquí. Léelo entero antes de tocar
> nada.
>
> Fecha: 2026-10-24. Estado verificado con `build_clang/logs/neverdead_015.log`.

---

## 1. Qué es este proyecto

Recompilación **estática** del juego *NeverDead* (Xbox 360, Konami/Rebellion,
Asura Engine 2012) a x64 con **ReXGlue 0.10.0.24-dev + Clang 23.1 + Ninja**.

| | |
|---|---|
| Raíz | `C:\Users\katze\Downloads\_Proyectos_y_Backups\neverdead_project` |
| Binario | `build_clang/neverdead.exe` |
| Árbol generado (el que se compila) | `generated/neverdead_default/neverdead_recomp.*.cpp` (123 TU) |
| Tabla de dispatch | `generated/neverdead_default/neverdead_init.cpp` |
| Config del seed | `neverdead_manifest.toml` + `config/functions.toml` |
| Logs de ejecución | `build_clang/logs/neverdead_*.log` |
| Binario invitado | `NeverDead_base.exe` |
| Oráculo de referencia (trace Xenia, mismo título/versión) | `xenia_oracle.log` |
| XEX que carga el runtime | `assets/default.xex` |
| XEX que usó el codegen | `../NeverDead_uncompressed.xex` |

**Objetivo:** llegar al menú (`GUIMenu\loadingInitial.guiBE`) y a la generación
3D (`VdInitializeEngines` → ring buffer → PM4 → `VdSwap`).

---

## 2. Cómo construir y medir (léelo, te ahorra 20 minutos)

### 2.1 El script de build normal **no funciona** en sandbox

`build_neverdead.ps1` llama a `vcvarsall.bat`, que invoca `reg.exe`, y el sandbox
lo bloquea. Usa esto:

```bash
cd "C:/Users/katze/Downloads/_Proyectos_y_Backups/neverdead_project"

export MSVC="C:\\Program Files (x86)\\Microsoft Visual Studio\\2022\\BuildTools\\VC\\Tools\\MSVC\\14.44.35207"
export SDK="C:\\Program Files (x86)\\Windows Kits\\10"
export SDKV="10.0.26100.0"
export INCLUDE="$MSVC\\include;$SDK\\Include\\$SDKV\\ucrt;$SDK\\Include\\$SDKV\\shared;$SDK\\Include\\$SDKV\\um;$SDK\\Include\\$SDKV\\winrt;$SDK\\Include\\$SDKV\\cppwinrt"
export LIB="$MSVC\\lib\\x64;$SDK\\Lib\\$SDKV\\ucrt\\x64;$SDK\\Lib\\$SDKV\\um\\x64"
export PATH="/c/Program Files/LLVM/bin:/c/Users/katze/AppData/Local/Programs/Python/Python312/Scripts:$PATH"

cmake --build build_clang --config Release     # ~4-7 min
python run_game_watchdog.py 20                 # ejecuta 20 s y guarda log
```

### 2.2 Python

- **Python con capstone** (imprescindible para desensamblar):
  `C:/Users/katze/AppData/Local/Programs/Python/Python312/python.exe`
- El python gestionado de WorkBuddy **NO tiene capstone**. No lo uses para
  desensamblar.

### 2.3 Greps de medición

```bash
L=build_clang/logs/neverdead_015.log          # el más reciente = número más alto

grep -c "TRAP-OOB" "$L"                        # estabilidad
grep -c "Unhandled guest access violation" "$L" # AVs
grep -c "VdInitializeEngines\|VdInitializeRingBuffer\|VdSwap" "$L"   # gráficos
grep -c "asrBE\|loadingInitial" "$L"           # menú/assets
grep -c "Entering Asura main loop" "$L"        # main loop
```

---

## 3. EL PROBLEMA CENTRAL — léelo dos veces

### 3.1 `NeverDead_base.exe` no es un volcado fiel

Su contenido está **desplazado** respecto a la imagen guest del XEX, y el
desplazamiento es **dependiente de región** (0 / +0x1000 / +0x2000). Medido con
anclas de contenido:

| Zona | RVA de ancla | Desplazamiento |
|---|---|---|
| `.data` | `0x7DBFA8`, `0x7EA280`, `0x7EA2B0` | **0** (3/3) |
| `.rdata` baja | `0x04AFC0`, `0x04D350`, `0x04D360` | **0** (3/3) |
| `.rdata` media | `0x144930` | **0x1000** (5/5 palabras) |
| `.rdata` alta | `0x49EF98`, `0x49EFA8` | **0x2000** (2/2) |

**No existe un offset fijo por sección que sea correcto.** Cualquier herramienta
que use `file_offset = guest_va - 0x82000000` (incluido `MASTER_CONTEXT.md`, que
lo documenta como "Oracle 3") está **equivocada**.

### 3.2 El bug raíz: el árbol mezcla dos espacios de direcciones

**La etiqueta de la función es la guest correcta (coincide con `.pdata`), pero el
cuerpo se decodifica desde el offset plano** (`rva = guest - 0x82000000`), que
está desplazado.

Caso probado, `sub_8271E508` (entrada `.pdata` real, cabecera de la ruta D3D):

```
Cuerpo correcto (offset 0x71D508, shift +0x1000):
  mflr r12 ; bl 0x8278cd3c(__savegprlr_25) ; stwu r1,-0xb0
  lwz r11,0x28(r3) ... cmplui cr6,r7,0x400 ... bl 0x826ae938
  addi r1,r1,0xb0 ; b 0x8278cd8c(__restgprlr_25)

Lo que tiene el árbol:
  bge cr6,0x8271e510 ; mr r25,r11 ; lwz r11,196(r1) ; cmplw cr6,r11,r26
```

Ese `bge/mr/lwz` es el contenido del offset plano `0x71E508` = guest
`0x8271F508` = **el cuerpo de otra función**.

Barrido de las 15 323 entradas `.pdata` con desplazamiento determinado:

| Offset | Entradas |
|---|---|
| plano (lo que usa el árbol) | **0** |
| sólo el corregido (`rva - shift`) | **13 989** |
| ambos (son las de shift 0) | 1 328 |
| ninguno | 6 |

**Ninguna función del árbol está decodificada desde su offset correcto.**

Esto explica de un golpe: las 32 222 ramas con destino desajustado, los 473
chunks `share_registers` sin prólogo, y que la ruta D3D exista pero nadie la
invoque. También explica cómo el motor avanzaba: el árbol es *internamente*
coherente — ejecuta un programa coherente pero desplazado, que no es el del
invitado.

---

## 4. Herramientas que ya existen (úsalas, no las reescribas)

| Fichero | Para qué |
|---|---|
| `tools/derive_shift_map.py` | **La más importante.** Deriva el mapa de desplazamientos desde `.pdata`. `--stats`, `--resolve <flat>`, `--branch <sitio> <destino>` |
| `tools/audit_share_registers.py` | Cuantifica chunks `share_registers` emitidos como funciones (473 rotos) |
| `docs/BUG_RAIZ_OFFSET_DECODIFICACION.md` | El bug raíz con todas las pruebas |
| `docs/CRT_HELPERS.md` | Qué es qué de la familia `0x8278BDxx` (reescrito tras refutación) |
| `docs/AUDIT_BOOT_AND_3D.md` | Auditoría de arranque/render + apéndice de correcciones |
| `docs/PLAN_MENU_Y_3D.md` | Plan por fases |
| `.workbuddy-ai/memory/2026-10-23.md` | Memoria de la sesión, con todos los hallazgos |

Uso típico:

```bash
python tools/derive_shift_map.py --stats
python tools/derive_shift_map.py --resolve 0x8278E508
python tools/derive_shift_map.py --branch 0x822CB914 0x821E1098
```

**Fórmula clave:** `destino_guest_real = destino_plano + shift(sitio)`, porque el
desplazamiento de rama es invariante. Un branch está mal etiquetado si
`shift(sitio) != shift(destino)`.

---

## 5. Parches aplicados (y su naturaleza)

> **Aviso:** todos salvo el de `.rdata` son **compensaciones sintomáticas**. Si se
> confirma el bug raíz y se regenera el árbol, varios se vuelven innecesarios.

| Parche | Fichero / target CMake | Naturaleza |
|---|---|---|
| Escalera CRT `0x8278BDxx` | `apply_crt_gpr_helpers.py` / `neverdead_crt_gpr_helpers` | Compensación en espacio plano. El ladder REAL está en guest `0x8278CD10` (`.pdata`: extensión exacta `0x50`) |
| `MapBaseExeSections` → sólo huecos | `src/neverdead_app.h` | **Arreglo real.** Antes copiaba el `.exe` 1:1 y destruía `.rdata`. Ahora: `.data` se rellena con shift 0 (verificado); `.rdata` y `.post` **no** se inyectan (`ND_FILL_RDATA=1` / `ND_FILL_POST=1` para A/B) |
| Destino de rama `0x822CB914` | `apply_821e2098_lookup.py` / `neverdead_821e2098_lookup` | Arreglo real verificado: objetivo real `0x821E2098` (búsqueda en árbol), no `0x821E1098` |
| Cola del main loop | `src/nd_main_loop.h` | Correcto (marco `0xB0`, salva `r21..r31`). **Riesgo abierto:** empieza en `0x823C5DAC`, 0x2C bytes dentro de la función real, saltándose su prólogo |
| Seed `ctx.r29` | `apply_entrypoint_bridge.py` | Retirado (era no-op); sólo era necesario por el parche CRT |

---

## 6. Estado medido (log 015)

| Hito | Estado |
|---|---|
| M0-M3 (WINMAIN, VFS, D3D12 host, init objetos) | ✅ |
| Carga de recursos | 🟡 entra y falla |
| M4 `VdInitializeEngines` … M9 menú | ❌ 0 hitos |
| Líneas de log | 29 940 |
| AV | 1 |
| Bucle dominante | `0x821E8E38` × 29 844, dentro de `sub_821E8DF0` |

**Bloqueante actual:** `sub_821E8DF0` hace `lwz r10,0(r28); lwz r9,4(r10); bctrl`
con target 0 (vtable con slot nulo). `r28` es el `r3` de entrada.

Secuencia del log justo antes:

```
unregistered indirect call 0x824DDF90 lr=0x824D9EAC (handled r3=1)
unregistered indirect call 0x824DDF90 lr=0x822CA174 (handled r3=1)
ALLOC 20 lr=0x822CB220
sub_82673178 intercepted (r3=0x8C, r4=0xBC23A736), returning r3=0 (resource not found)
unregistered indirect call 0x824F6DB8 lr=0x823B2B10 (stack-safe epilogue)
```

`0x824DDF90` y `0x824F6DB8` **no son entradas `.pdata`**: son fragmentos de
epílogo compartido apuntados desde `.rdata`. El runtime los absorbe con stubs.

---

## 7. Parches sintomáticos ajenos que ocultan causa

No los quites sin entenderlos, pero sepas que tapan:

| Sitio | Fichero | Qué hace |
|---|---|---|
| `sub_82673178` | `src/nd_import_diag.h:620` | Stub que devuelve `r3=0`. Comentario original: *"Prevents corrupted stream dereference … breaks 30M-iteration loop"* |
| `0x824F6DB8` | `src/nd_indirect_trap.h` | Handler de "stack-safe epilogue" escrito a mano |
| `0x824DDF90` | `src/nd_indirect_trap.h` | Stub `r3=1` |
| `0x8242D02C` | `src/nd_indirect_trap.h` | `r30 = r29` |

---

## 8. Próximos pasos, en orden

### Paso 1 — Confirmar el alcance del bug raíz en el árbol

El caso `sub_8271E508` está confirmado. Falta saber si el codegen acierta en
**alguna** función o si es sistemático. Comprobación: para una muestra de
funciones del árbol, comparar su primera instrucción real con lo que hay en el
offset corregido. Si es sistemático, el paso 2 es la regeneración, no parches.

### Paso 2 — Regenerar el camino caliente con el offset correcto

`sub_8271E508` → `sub_826AF8A8` → `sub_826BC390` (`VdInitializeEngines`) y todo lo
que el log muestre que se ejecuta. Decodificar desde `offset = rva - shift(rva)`.

### Paso 3 — Medir

Si el log salta a `VdInitializeEngines CALLED!`, hipótesis confirmada.

### Paso 4 — Regeneración completa del seed

Con el mapa de desplazamientos. Eso haría innecesarios los parches de
compensación de la tabla del punto 5.

---

## 9. Trampas conocidas (no las vuelvas a pisar)

1. **`MASTER_CONTEXT.md` está desactualizado.** Documenta
   `file_offset = guest_va - 0x82000000`, que es falso. No lo uses como referencia.
2. **`.pdata` manda** sobre cualquier otra fuente para saber dónde empieza una
   función y cuánto mide. Se lee big-endian, RVA `0x16F800`, entradas de 8 bytes,
   y hay que filtrar al rango de código `[0x821B2200, 0x827C05E4)` (17 689 válidas
   de 18 202 crudas).
3. **Los helpers CRT no llevan `mflr`.** Empiezan con `std rN,-off(r1)`. Un
   detector de prólogos que sólo acepte `mflr` los marca todos como "sin
   determinar".
4. **El ladder CRT real está en guest `0x8278CD10`, no en `0x8278BD10`.**
5. **Los `lis/addi` NO sufren shift** (son bytes idénticos); lo que sufre es la
   etiqueta.
6. **No construyas el build con `build_neverdead.ps1`** en sandbox.
7. **Un build son 4-7 minutos.** Agrupa cambios; no compiles para cada cosa.
8. **Un `grep -c` que no encuentra nada devuelve exit code 1** y puede marcar la
   tarea como fallida aunque el build haya ido bien.

---

## 10. Ficheros que no existían y ahora sí

```
tools/derive_shift_map.py          mapa de desplazamientos desde .pdata
tools/audit_share_registers.py     auditoría de chunks share_registers
apply_crt_gpr_helpers.py           helpers CRT en espacio plano
apply_821e2098_lookup.py           destino de rama real 0x821E2098
docs/BUG_RAIZ_OFFSET_DECODIFICACION.md
docs/CRT_HELPERS.md                (reescrito, sustituye a la versión falsa)
docs/AUDIT_BOOT_AND_3D.md
docs/PLAN_MENU_Y_3D.md
```

Modificados: `CMakeLists.txt` (3 targets nuevos + dependencias explícitas),
`src/neverdead_app.h` (`fill_holes`), `src/nd_main_loop.h`, `src/nd_indirect_trap.h`
(sondas `CI-PROBE`/`pre-map`), `apply_entrypoint_bridge.py` (seed off).

---

## 11. Resumen en una frase

El árbol está decodificado desde el offset equivocado en el 92 % de las
funciones; hasta que se regenere con el mapa de desplazamientos correcto
(`tools/derive_shift_map.py`), cualquier parche es una compensación sobre un
programa que no es el del invitado.
