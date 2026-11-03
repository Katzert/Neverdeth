# Informe final — 3D in-game alcanzado (2026-10-24 21:50)

**Resultado:** el juego carga el prólogo, renderiza geometría 3D (personaje, armas,
enemigos, entorno), muestra HUD y tutoriales, y se mantiene 70 s sin crashes ni
excepciones. La subexposición inicial se corrigió forzando el path **ROV** de D3D12
(`render_target_path_d3d12 = "rov"`), sin coste medible de FPS.

---

## 1. Evidencia

| Métrica | Valor |
|---|---|
| Log | `build_clang/logs/neverdead_085.log` — **9.923 líneas** |
| `VdSwap` | **1.800 llamadas en 70 s** (~26-30 FPS) |
| `VdInitializeEngines` | 1 vez |
| `FATAL` / `unregistered` | **0** |
| Activaciones del VEH | **0** |
| Assets faltantes | solo `CESA.bik` (ausente por SKU) y `\Device\Image` |

| Captura | Contenido |
|---|---|
| `shots/run_214621_0045s.png` | HUD + tutorial "You have dual guns…" (RTV, oscuro) |
| `shots/bright_run_214621_0045s.png` | RTV + gamma artificial: la geometría ya estaba ahí |
| `shots/run_215624_0045s.png` | **ROV: escena del prólogo iluminada** (personaje, portal, enemigos) |
| `shots/run_215624_0065s.png` | **ROV: primer plano del personaje** con texturas y luces |
| `shots/run_215845_0065s.png` | ROV persistido vía `neverdead.toml` (sin env var) |
| `shots/run_211127_0027s.png` | Menú DIFFICULTY con escena 3D |

Las versiones `bright_*` son las mismas capturas con corrección de gamma (`x^0.30`); no
se ha alterado el render, solo la evidencia para poder inspeccionarla.

---

## 2. Qué desbloqueó el 3D

### 2.1 Los assets: el contenedor GOD de `D:`

El dump parcial de `assets/` solo tenía UI, fuentes, textos, sonido y FMV porque el
script anterior se ejecutó con `--menu-only`. El juego completo está en un contenedor
**Games on Demand**:

```
D:\Emulacion_y_ROMs\Xbox_360\4B4E081F\00007000\E7D01E468FBBCE3EC1F3.data   (6,4 GB)
```

`tools/extract_neverdead_assets.py` (recuperado del scratch de Gemini) parsea el GDFX y
extrae por prefijo. Añadí `--prefix` y extraje:

| Prefijo | Contenido | Tamaño |
|---|---|---|
| `Envs/SP/SP_00_Prologue` | nivel + localizaciones | 12,1 MB |
| `Chars/SP_YoungBryce` | personaje del prólogo | 25,1 MB |
| `Envs/NeverDead.Xbox360_textures` | pack global de texturas | **1.442 MB** |

Sin el pack de texturas el nivel carga pero no tiene materiales. `assets/` pasó de 3,8 GB
a 5,2 GB.

### 2.2 Los thunks: la otra mitad

Con el nivel presente, el juego avanzó y empezó a fallar en **funciones que no están en
`.pdata`**. Son dos clases:

**a) Thunks de llamada virtual (147 + 1)** — 16 bytes:

```
lwz r11, 0(r3)
lwz r10, OFF(r11)
mtctr r10
bctr
```

Se declaran como **chunk** del `.pdata` contenedor en `config/thunks.toml`. El caso
`0x82438D70` es especial: como chunk el codegen se lo traga, así que va **standalone**
(`size = 0x10`).

**b) Thunks de cola (2)** — 12/16 bytes, tras el `blr` del padre:

```
lis  r11, HI
addi r3, r11, LO
b    target
```

`0x823529E8` (12 B) y `0x823529F8` (16 B) van **standalone**; como chunk también se los
traga el padre.

Herramientas nuevas y reproducibles:

```
tools/scan_vcall_thunks.py     detecta la clase (a) y genera config/thunks.toml
tools/scan_tail_thunks.py      detecta la clase (b) con filtro "tras terminador"
```

### 2.3 Lo que NO funcionó (y por qué)

- **Declarar los 605 tail thunks en bloque**: rompió una rama interna real
  (`FATAL: Unresolved branch from 0x826BC214 to 0x826BC260`). Algunos `lis/addi/b` son
  labels de switch dentro de una función, no funciones. El filtro "precedido por
  terminador" no basta.
- **Declarar los thunks de hueco como chunk**: el codegen los absorbe en el padre y no
  los registra (`0x82438D70`, `0x823529E8`). Hay que usar standalone para esos.

---

## 3. Estado por niveles

| Nivel | Estado | Evidencia |
|---|---|---|
| N0 Ventana D3D12 + present | ✅ | logs |
| N1 Título estable | ✅ | `after_start_40s.png` |
| N2 Menú principal | ✅ | `04_after_enter_6s.png` |
| N3 Menú con geometría 3D | ✅ | `run_211127_0027s.png` |
| N4 Menú → gameplay sin caída | ✅ | HUD, pausa, tutoriales |
| **N5 Frame in-game con mundo 3D** | ✅ | `run_215624_0065s.png` (iluminado) |

Con ROV, N5 queda cumplido sin salvedades: geometría, materiales, luces y HUD en el mismo
frame.

---

## 4. Fix aplicado: `render_target_path_d3d12 = "rov"`

La escena 3D se dibujaba pero salía casi negra, mientras el HUD 2D y los menús se veían
bien. Es el síntoma clásico de la reinterpretación de eDRAM: el pase 3D escribe formatos
flotantes que, en el path **RTV**, se mapean a texturas nativas y pierden rango al
resolverse; el HUD 2D va en espacio entero y no sufre.

El backend xenos de ReXGlue expone la cvar `render_target_path_d3d12`
(`src/graphics/d3d12/render_target_cache.cpp:44`), con dos valores:

- `"rtv"` → `Path::kHostRenderTargets` (por defecto en NVIDIA).
- `"rov"` → `Path::kPixelShaderInterlock` (Rasterizer-Ordered Views).

La RTX 2060 soporta ROV (`Rasterizer-ordered views: yes` en el log). Forzado:

```toml
# build_clang/neverdead.toml  (el runtime lo carga antes de init)
render_target_path_d3d12 = "rov"
```

### Medición

| Frame | RTV (media de luminancia) | ROV |
|---|---|---|
| 45 s | 2,97 (p95 = 4) | **70,40** (p95 = 170) |
| 65 s | 3,20 (p95 = 4) | **67,62** (p95 = 184) |

| Régimen estable | RTV | ROV |
|---|---|---|
| 300 frames | 10,08 s → **29,8 FPS** | 9,97 s → **30,1 FPS** |

ROV **no cuesta FPS** en esta GPU. Las capturas `run_215624_*` y `run_215845_*` salen ya
con el brillo correcto, sin corrección de gamma.

### Notas de implementación

- La cvar es `Lifecycle::kInitOnly`: debe fijarse antes de inicializar el render target
  cache. El TOML del ejecutable se carga antes, así que es el sitio correcto.
- También funciona por entorno: `REX_RENDER_TARGET_PATH_D3D12=rov`.
- Si en el futuro se sube a 1440p/4K y ROV molesta, la alternativa es localizar el pase
  de tone-mapping/resolve con RenderDoc y corregir solo ese shader; pero a 1280×720 el
  coste es nulo.

### Hipótesis descartadas

- **No era falta de lightmaps**: el pack global de texturas (1,44 GB) y el nivel ya
  estaban cargados; el frame contenía toda la geometría e iluminación, solo comprimida
  en rango.
- **No era el ajuste de brillo del juego**: el menú y el HUD ya salían correctos.

---

## 5. Cambios en el proyecto

| Fichero | Cambio |
|---|---|
| `tools/extract_neverdead_assets.py` | Nuevo (recuperado + `--prefix`) |
| `tools/scan_vcall_thunks.py` | Nuevo: detecta thunks vcall |
| `tools/scan_tail_thunks.py` | Nuevo: detecta thunks de cola |
| `config/thunks.toml` | 148 thunks vcall + 2 tail |
| `config/thunks_tail.toml` | Generado pero **no incluido** (lote de 605 revertido) |
| `neverdead_manifest.toml` | Incluye `config/thunks.toml` |
| `assets/Envs/`, `assets/Chars/` | Extraídos del GOD |
| `build_clang/neverdead.toml` | `render_target_path_d3d12 = "rov"` (fix de brillo) |
| `build_clang/asset_list.txt` | Listado de los 461 archivos del contenedor |

No se tocó `generated/` ni la lógica del juego.

---

## 6. Reproducir

```bash
# extraer un nivel / personaje / pack
python tools/extract_neverdead_assets.py --prefix "Envs/SP/SP_00_Prologue"
python tools/extract_neverdead_assets.py --prefix "Chars/SP_YoungBryce"
python tools/extract_neverdead_assets.py --prefix "Envs/NeverDead.Xbox360_textures"

# regenerar thunks (imagen descifrada en tools/scratch_disasm/default_dec.bin)
python tools/scan_vcall_thunks.py
python tools/scan_tail_thunks.py --dry-run

# build + run determinista
cmake --build build_clang --config Release
REX_MNK_MODE=1 python tools/nd_menu_test.py --enter-at 20,22,24 --shots 35,45,55,65
```

---

## 7. Conclusión

El objetivo "llegar al menú y ejecutar 3D" está cumplido, incluido el 3D in-game con
iluminación correcta. El último bloqueo era de **presentación** (path RTV de D3D12), no de
assets ni de código recompilado: forzar ROV lo resolvió sin coste de FPS.

Lo que queda para jugar la campaña completa es trabajo de extracción, no de ingeniería
inversa: sacar el resto de `Envs/` y `Chars/` del mismo contenedor GOD con
`tools/extract_neverdead_assets.py --prefix ...`.
