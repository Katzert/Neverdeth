# La familia `0x8278BDxx` y el ladder CRT — qué es qué de verdad

Fecha: 2026-10-23. Este documento sustituye a la versión anterior, que contenía
una conclusión **falsa** sobre la localización del ladder. Corregido tras
auditoría adversarial independiente.

---

## 1. El hallazgo duro: el ladder real está en `0x8278CD10`

`.pdata` es la tabla de funciones del binario y es la única prueba
independiente del árbol generado. Resultado de leerla (`NeverDead_base.exe`,
sección `.pdata`, RVA `0x16F800`, entradas big-endian de 8 bytes):

| Dirección | ¿Entrada en `.pdata`? | Extensión |
|---|---|---|
| `0x8278AD10` | **NO** | — |
| `0x8278BD10` | **NO** | — |
| `0x8278CD10` | **SÍ** | **exactamente `0x50`** |
| `0x8278CD60` | siguiente entrada | — |
| `0x8278DD78` | **SÍ** | `0x40` |

`0x50` bytes = 20 instrucciones = 18 × `std rN,-off(r1)` (r14…r31) + `stw r12,-8(r1)`
+ `blr`. Es decir: **`0x8278CD10` es `__savegprlr_14` y `0x8278CD60` es
`__restgprlr_14`**, y la escalera completa ocupa `0x8278CD10..0x8278CDB0`.

Confirmación adicional: las 17 funciones `.pdata` de la zona
`0x8278A000..0x8278C000` empiezan **todas** por `mflr r0` (`7D8802A6`) cuando se
leen con desplazamiento `+0x2000`, y por nada reconocible con `+0x1000`.

## 2. Qué es entonces `0x8278BD10..0x8278BDB0`

Código real de otras funciones. En particular `0x8278DD78` (que en el espacio
plano del árbol aparece como `0x8278BD78`) **tiene su propia entrada `.pdata`**
con extensión `0x40`: es una función independiente, no un fragmento.

## 3. Por qué el árbol "cree" que hay un ladder en `0x8278BDxx`

Porque **`NeverDead_base.exe` no es un volcado fiel de la imagen guest**. Su
contenido está desplazado respecto a ella de forma **dependiente de región**.
Mapa medido con anclas de contenido (volcados `dump` del runtime comparados
contra el `.exe`):

| Zona | RVA de ancla | Desplazamiento correcto | Anclas |
|---|---|---|---|
| `.data` | `0x7DBFA8`, `0x7EA280`, `0x7EA2B0` | **`0x0000`** | 3/3 |
| `.rdata` baja | `0x04AFC0`, `0x04D350`, `0x04D360` | **`0x0000`** | 3/3 |
| `.rdata` media | `0x144930` | **`0x1000`** | 5/5 palabras |
| `.rdata` alta | `0x49EF98`, `0x49EFA8` | **`0x2000`** | 2/2 |
| `.text` (varias) | `0x82686058` | **`0x1000`** | firma de 4 instr. |

Dentro de la **misma** sección `.rdata` el desplazamiento toma tres valores
distintos. No existe un offset fijo por sección que sea correcto.

Consecuencia: el espacio de direcciones del árbol (plano) y el de la imagen
guest no coinciden, y una dirección como `0x8278BD4C` **sólo tiene sentido
dentro del árbol**.

## 4. Qué hace `apply_crt_gpr_helpers.py` y por qué sigue haciendo falta

~3021 sitios de llamada del árbol apuntan a `0x8278BDxx`:

- `bl` desde prólogos: 849 / 655 / 467 / 326 / 185 / 121 / 91 / 72 / 55 / 36 /
  30 / 25 / 25 / 25 / 19 / 9 … (monótono decreciente, coherente con un ladder)
- `b` desde epílogos: 2723 / 2218 / 1726 / 1110 / 619 / 430 / 372 / … 
- **0** `bl` hacia `BD60..BD9C` (sólo se alcanzan por `b`)

El emparejamiento prólogo↔epílogo con desfase constante `0x50` es real **en el
espacio plano del árbol**, pero es una consecuencia del mapeo plano incorrecto:
no dice nada sobre la imagen guest. (Mi conteo independiente da 3021 `bl`, no
2930.)

El 93.6% de esos sitios vive en regiones cuyo desplazamiento real es `+0x1000`,
es decir que su objetivo **verdadero sí es el ladder**. Para ellos, el cuerpo
canónico de helper es lo correcto.

Antes del parche, esas entradas contenían la "transcripción literal" de
`apply_bd_family_faithful.py`, que incluía

```
addi r1, r1, 0x70
```

dentro de un helper de **prólogo**: empujaba el puntero de pila del llamante
`+0x70` en cada invocación. Ese era el `r1` que se alejaba de `0x70190000` hasta
`0x70190590`, el `lr` que acababa en `0xBEBEBEBE`, y el `bctrl` que saltaba a la
cadena `"Fall"` (`0x8205970C`).

**El parche es de consistencia interna del árbol, no una descripción fiel de la
imagen guest.** Efecto medido: TRAP-OOB 27034 → 6406, y desaparecen los 27026
saltos a `0x8205970C`.

## 5. Límite conocido del parche

~6% de los sitios (106 en regiones de desplazamiento 0 y 87 en regiones de
`+0x2000`) tienen por objetivo verdadero otra cosa. Para esos, el cuerpo
canónico es una aproximación, no la verdad.

**Arreglo de raíz:** reconstruir el seed con el mapa de desplazamientos correcto,
derivable mecánicamente de `.pdata` (exigir `mflr r0` en cada entrada y
coherencia de extensiones). Eso corregiría de golpe el ladder, los 473 chunks
`share_registers` y los objetivos de `bl` mal etiquetados.

## 6. Reglas que sí son sólidas

1. Un helper del CRT **nunca** modifica `r1`. Si un cuerpo etiquetado como
   helper lo hace, está mal.
2. `slot_off(r) = 8 * (33 - r)`: r31 → −16, r30 → −24, r29 → −32, r14 → −152.
3. `.pdata` manda sobre cualquier otra fuente para saber **dónde empieza una
   función y cuánto mide**.
4. `NeverDead_base.exe` no sirve como oráculo de posiciones. Sólo como fuente
   de bytes, y **sabiendo el desplazamiento de la región**.
