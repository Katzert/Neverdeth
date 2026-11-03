# Bug raíz: el árbol decodifica los cuerpos desde el offset equivocado

Fecha: 2026-10-24. Verificación solicitada tras la revisión cruzada con Case Zero,
Dante's Inferno y Fable II.

---

## El hallazgo

El árbol generado **mezcla dos espacios de direcciones**:

- La **etiqueta** de la función es la dirección guest correcta (coincide con
  `.pdata`).
- El **cuerpo** se decodifica desde el offset plano `rva = guest - 0x82000000`,
  que está desplazado `+0x1000` o `+0x2000` respecto al contenido real.

### Prueba 1 — el caso concreto de la ruta D3D

`sub_8271E508` es una función real: `.pdata` tiene entrada ahí. Su cuerpo
correcto está en offset `0x71D508` (shift `+0x1000`):

```
0x8271E508  mflr  r12
0x8271E50C  bl    0x8278cd3c        ← __savegprlr_25
0x8271E510  stwu  r1, -0xb0(r1)     ← marco 0xB0
0x8271E514  lwz   r11, 0x28(r3)
...
0x8271E5BC  cmplui cr6, r7, 0x400   ← cálculo de formato (1024)
...
0x8271E768  bl    0x826ae938
0x8271E76C  stw   r3, 0(r31)
...
0x8271E804  addi  r1, r1, 0xb0      ← pop
0x8271E808  b     0x8278cd8c        ← __restgprlr_25
```

El árbol, en cambio, tiene para `sub_8271E508`:

```
bge  cr6, 0x8271e510
mr   r25, r11
lwz  r11, 196(r1)
cmplw cr6, r11, r26
```

Eso es exactamente el contenido del offset plano `0x71E508`, que corresponde al
guest `0x8271F508` — **otra función**. No es un fragmento: es el cuerpo de otra
función, desplazado 0x1000.

### Prueba 2 — el barrido completo

Para las 15 323 entradas `.pdata` con desplazamiento determinado, comprobando si
el offset plano o el corregido (`rva - shift`) contiene un prólogo válido
(`mflr` / `stwu r1,-N` / `std rN,-off(r1)`):

| Offset | Entradas |
|---|---|
| **plano** (`rva`, lo que usa el árbol) | **0** |
| sólo el **corregido** (`rva - shift`) | **13 989** |
| ambos (son las de shift 0) | 1 328 |
| ninguno | 6 |

**Ninguna** función del árbol está decodificada desde su offset correcto. El
92 % lo está desde uno desplazado.

---

## Por qué el motor avanzaba a pesar de esto

El árbol es **internamente coherente**: etiquetas, cuerpos y destinos de rama
salen todos del mismo mapeo plano. Ejecuta un programa *coherente pero
desplazado*, que no es el programa del invitado. Por eso el arranque llega
lejos y a la vez es imposible que llegue al final: las funciones que el invitado
referencia por su dirección guest real (desde `.rdata`, desde `lis/addi`, desde
tablas de estado) apuntan en el árbol a cuerpos de otras funciones.

Eso explica, de una vez:

- las **32 222 ramas** con destino desajustado,
- los **473 chunks** `share_registers` sin prólogo,
- que `sub_8271E508` → `sub_826AF8A8` → `sub_826BC390` (`VdInitializeEngines`)
  existan y **nadie las invoque**,
- que `sub_821E1098` fuera el fragmento equivocado.

---

## Respuesta a las tres verificaciones pedidas

### 1. ¿Es `0x824DDF90` el desvío hacia `sub_8271D590`?

**No.** Son regiones distintas (`0x824Dxxxx` vs `0x8271xxxx`, a ~0x24000 bytes).
`0x824DDF90` no es entrada `.pdata`: es un **fragmento de epílogo compartido**
dentro de la función guest `0x824DDED8`. Su contenido real (offset `0x24CF90`,
shift `+0x1000`) es:

```
addi r3, r5, 0x64
cmplui cr6, r11, 0
bne  cr6, ...
addi r3, r5, 0x84
...
addi r1, r1, 0x80        ← pop de marco 0x80
lwz  r12, -8(r1)
mtlr r12
blr
```

Es un destino de `bctrl` almacenado en `.rdata` (slot `0x82144930+16`, ahora
correcto tras el arreglo de `fill_holes`). Lo mismo con `0x824F6DB8` (offset
0xF0 dentro de la función guest `0x824F7CC8`).

**Pero la intuición de fondo era correcta**: sí hay un bug de shift que impide
entrar en la ruta D3D. No es ese puntero: es que el cuerpo de `sub_8271E508`
está desplazado 0x1000.

### 2. ¿Los `lis/addi` sufren el shift?

**Los literarios no.** Un `lis/addi` codifica un absoluto en el flujo de bytes;
si el `.exe` es el invitado reubicado, los bytes son idénticos y el literal es
idéntico. **Lo que sufre el shift es la etiqueta con la que el codegen nombra la
función destino**, y de ahí el desajuste de 32 222 ramas.

Pero el problema es más grave y estaba un nivel más abajo: **el codegen decodifica
el cuerpo desde el offset plano aunque la etiqueta sea la guest**. Eso sí corrompe
todas las funciones.

### 3. `VdQueryVideoMode`

No verificable: el log no muestra ninguna llamada a `Vd*`. Es un
`ND_IMPORT_REAL` que delega en el kernel; no hay hook que valide el modo. Queda
pendiente hasta que el arranque llegue.

---

## Consecuencia para el plan

`docs/PLAN_MENU_Y_3D.md` sigue siendo válido en sus fases C y D, pero **la Fase B
cambia de naturaleza**: ya no se trata de reescribir 32 222 destinos de rama
sueltos, sino de **regenerar los cuerpos desde el offset correcto**
(`offset = rva - shift(rva)`).

Con el mapa que ya tenemos (`tools/derive_shift_map.py`, 15 323 entradas
determinadas) eso es mecánico:

1. Para cada entrada `.pdata` `B` con shift `S`, el cuerpo correcto está en
   offset `B - 0x82000000 - S`.
2. Verificar que el offset contiene un prólogo válido (0 excepciones salvo 6).
3. Regenerar el árbol con ese mapeo, o parcharlo función a función para las del
   camino caliente.

Las 6 entradas sin prólogo en ningún offset y las 2 257 ambiguas necesitan
criterio adicional (extensión coherente con la siguiente entrada, o anclas de
contenido).

---

## Orden recomendado ahora

1. **Verificar el alcance en el árbol**: cuántas funciones del árbol tienen su
   primera instrucción en el offset plano vs el corregido. El caso
   `sub_8271E508` es uno; hay que confirmar si es sistemático o si el codegen
   acierta en algunas.
2. **Regenerar el camino caliente** con el offset correcto: `sub_8271E508`,
   `sub_826AF8A8`, `sub_826BC390` y todo lo que el log muestre que se ejecuta.
3. **Medir.** Si el arranque salta a `VdInitializeEngines`, la hipótesis queda
   confirmada y el paso 4 es la regeneración completa.
4. Regeneración completa del seed con el mapa de desplazamientos.
