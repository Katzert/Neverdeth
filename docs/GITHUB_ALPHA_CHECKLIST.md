# Checklist — Publicación Alpha en GitHub

Estado: **sin repo git todavía** (verificado: no existen `.git` ni `.gitignore`).
Regla maestra: **la raíz del proyecto conserva exactamente los 7 archivos canónicos**
(`CMakeLists.txt`, `CMakePresets.json`, `JUGAR_NEVERDEAD.bat`, `NeverDead_base.bin`,
`build_ninja.bat`, `neverdead.toml`, `neverdead_manifest.toml`). El `.gitignore` es el único
archivo nuevo que la regla toleraría en raíz (git lo exige ahí); si prefieres ni eso,
usa `git -c core.excludesFile=` con un exclude global. La recomendación es crearlo: git no
funciona en la práctica sin él.

---

## 1. Plantilla exacta de `.gitignore` (colocar en la raíz)

```gitignore
# ============================================================
# NeverDead PC Recompilation (ReXGlue) — .gitignore Alpha
# ============================================================

# --- Binario del juego y assets con copyright (NUNCA publicar) ---
NeverDead_base.bin
*.xex
assets/
FMV
Fonts
GUIMenu
Text
misc
sounds

# --- Junctions/symlinks (duplican assets/, rotos fuera de esta maquina) ---

# --- Build y caches ---
build_clang/
cache/
graphify-out/
*.obj
*.lib
*.exe
*.dll
*.pdb
*.map
neverdead.map

# --- Logs, capturas y dumps de diagnostico ---
*.log
*.bmp
*.png
!docs/arranque_ok_titulo.png
asura_cvars_dump.txt
crash_diag.log
rexglue_engine.log

# --- Herramientas locales / agentes ---
.agents/
.freebuff/
.tgrep-index/
.workbuddy-ai/
__pycache__/
*.pyc

# --- Editor / SO ---
.vs/
.vscode/
.idea/
Thumbs.db
desktop.ini
```

Notas:
- Los junctions `FMV`/`Fonts`/`GUIMenu`/`Text`/`misc`/`sounds` apuntan a
  `C:\Users\katze\.gemini\...\assets\*` — fuera de esta máquina son enlaces muertos.
- `!docs/arranque_ok_titulo.png` re-admite la única captura con valor documental
  (arranque verificado). Borra esa excepción si no la quieres publicar.
- `graphify-out/` es índice local de análisis (29 MB) — regenerable.

## 2. Qué entra en el repo (y qué no)

| Publicar | Razón |
|---|---|
| `src/` (11 archivos) | la capa de ports/hooks — es la aportación real |
| `config/` (8 toml) | hooks midasm, thunks, CRT — parte de la build |
| `CMakeLists.txt`, `CMakePresets.json`, `build_ninja.bat` | build reproducible |
| `neverdead.toml`, `neverdead_manifest.toml` | config canónica de runtime |
| `docs/` | informes, checklist, render_flags_scan — la "memoria" del proyecto |
| `tools/` (solo scripts: `extract_rex_cvars.py`, `bench_v6.py`, `BENCH_NEVERDEAD.bat`, `asura_extractor/` sin `scratch/`) | reproducibilidad del análisis |

| NO publicar | Razón |
|---|---|
| `NeverDead_base.bin`, `assets/` (6.4 GB) | **copyright de Konami** — contenido del juego |
| `build_clang/` | artefactos (el `rexruntime.dll`/`rexgpu-xenos.dll` del SDK se descargan con el SDK de ReXGlue, no se redistribuyen) |
| `generated/` (134 MB) | **decisión pendiente** — ver §3 |

## 3. Decisión pendiente: `generated/` (134 MB, 123 cpp + 485 h)

`generated/neverdead_default/` es la recompilación del código del juego, derivada de
`NeverDead_base.bin`. Dos posturas:

1. **Incluirlo** (repo ~140 MB): cualquiera compila sin poseer el juego hasta el paso
   de ejecutar (que sí necesita `NeverDead_base.bin` + `assets/`). Riesgo legal:
   es obra derivada del binario original.
2. **Excluirlo** (repo < 5 MB): el README documenta que hay que ejecutar el codegen de
   ReXGlue sobre el `NeverDead_base.bin` propio (comando en §4). Repositorio mínimo,
   el peso legal queda en el usuario final. **Recomendado para Alpha.**

Si se incluye y GitHub se queja por tamaño: Git LFS (`git lfs track "generated/**/*.cpp"`).

## 4. Plantilla de `README.md` (colocar en la raíz o en `docs/` y enlazar)

```markdown
# NeverDead PC Recompilation (ReXGlue Alpha)

Recompilación estática del Xbox 360 original a x64 nativo con el SDK ReXGlue
(runtime + plugin GPU Xenos→D3D12 con ROV). Proyecto de ingeniería inversa
con fines de preservación y estudio.

## Estado Alpha
- Arranque, logos, menús y gameplay 3D estables.
- Menú F1 en vivo: presets "PC Bajo Recursos (Max FPS)" / "Calidad Original",
  10 toggles de pases del renderer, límite de FPS, modo de ventana.
- F8 alterna 60/30 FPS en vivo.

## Requisitos
- Windows 10/11 x64, GPU con D3D12 (ROV: Tier 1+; tiled resources para memoria compartida).
- Clang (LLVM) + Ninja + CMake ≥ 3.25 y las Build Tools de MSVC 2022 (headers/libs).
- SDK ReXGlue 0.10.0 (`REXSDK_DIR` o instalado en `CMAKE_PREFIX_PATH`).
- **Tu propia copia del juego**: `NeverDead_base.bin` y `assets/` NO se distribuyen
  en este reposatorio. Colócalos en la raíz del proyecto.

## Compilar
    build_ninja.bat          (o: cmake --preset clang && ninja -C build_clang neverdead)

## Jugar
    JUGAR_NEVERDEAD.bat
    Diagnóstico opcional:  set ND_DIAG=1  (telemetría [FRAME]/[SWAP] en build_clang\logs)

## Mapa del proyecto
    src/         capa de ports (imports xboxkrnl, present gate, menú F1, pacer)
    config/      hooks midasm / thunks / CRT mapping del codegen
    generated/   [no incluido] árbol recompilado (ver docs/)
    docs/        informes de ingeniería e inventarios (CVars del runtime, flags del renderer)

## Legal
NeverDead © Konami. Este repositorio no contiene ni distribuye ningún asset, binario
ni dato original del juego: solo la capa de recompilación y configuración. Úsalo solo
con una copia legítima que poseas.
```

## 5. Pasos exactos de publicación

```bat
cd /d C:\Users\katze\Downloads\_Proyectos_y_Backups\neverdead_project
git init -b main
:: crear .gitignore (plantilla §1) y README.md (plantilla §4)
git add .gitignore README.md CMakeLists.txt CMakePresets.json build_ninja.bat ^
        JUGAR_NEVERDEAD.bat neverdead.toml neverdead_manifest.toml src config docs tools
git status      :: REVISAR: nada de assets/, NeverDead_base.bin, build_clang/, generated/
git commit -m "Alpha v0.1.0: NeverDead PC recomp (ReXGlue) - fpscr fix, presets, telemetria ND_DIAG"
git remote add origin https://github.com/<usuario>/neverdead-pc-recomp.git
git push -u origin main
git tag -a v0.1.0-alpha -m "Primera Alpha" && git push origin v0.1.0-alpha
```

Verificación previa obligatoria (un leak de `assets/` o del `.bin` es irrecuperable
una vez empujado):

```bat
git ls-files | findstr /i "assets NeverDead_base.bin build_clang generated .workbuddy"
:: debe devolver VACÍO
```

## 6. Recordatorios finales

- `.freebuff/` reapareció tras la limpieza (tooling de agente): sigue en el ignore.
- El parche del SDK (`rex/ppc/context.h` en `rexglue_sdk/`) vive FUERA del repo. Documenta
  en el README o en `docs/` que la build asume ese parche (hay copia del original en
  `context.h.rex_nd_orig`), o el binario de cualquiera else recompilará con el ping-pong
  de `ldmxcsr` de vuelta.
- El hash/pins del SDK: fijar `REXSDK_VERSION` en el cache de CMake para builds reproducibles.
