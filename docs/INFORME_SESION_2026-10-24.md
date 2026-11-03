# Informe de sesión — 2026-10-24 (20:30)

**Objetivo:** reconstruir el contexto real del proyecto y preparar un handoff para Gemini
Argon, con evidencia verificada, sin parches y sin whack-a-mole.

**Método:** tres subagentes en paralelo (documentación de ReXGlue, auditoría del código
actual, análisis de assets/herramientas) + verificación directa de cada hallazgo contra
`generated/`, `build_clang/logs/`, capturas y `codegen.d`. Nada se aceptó por el informe
de otra IA sin comprobarlo.

---

## 1. Estado real (lo que se puede defender con evidencia)

| Nivel | Estado | Evidencia |
|---|---|---|
| Título D3D12 estable 40 s | ✅ | `build_clang/after_start_40s.png` (17:17) |
| Menú principal **3D** | ✅ **una vez** | `build_clang/shots/04_after_enter_6s.png` (17:25:07) |
| Transición menú → gameplay | ❌ | 12+ capturas 17:51-19:35, todas título |
| Telemetría de `Vd*` | ❌ | 0 líneas `IMPORT-REAL` en todas las corridas |
| Gameplay 3D | ❌ | no verificado |

La captura del menú es del **mismo renderer** que el título (diff estructural 47,3 medio
vs 4-5 entre títulos), así que no es una captura de Xenia. Pero **no se ha reproducido**
después del rebuild de las 17:50.

---

## 2. Hallazgos nuevos (verificados en esta sesión)

### H1 — La capa de hooks está muerta por un desfase de −0x2000

Los hooks de `src/nd_import_diag.h` apuntan a `0x827BE084` (`VdInitializeEngines`) y
`0x827BDFD4` (`VdSwap`), pero el árbol registra los stubs reales en `0x827C0084` y
`0x827BFFD4` (`neverdead_init.cpp:24791` y `:24780`). `grep 827BE084 generated/` → 0.
El código generado llama directamente a `__imp__*` (`neverdead_recomp.7.cpp:22746`).

**Consecuencia:** no hay forma de saber desde el log si el juego llama a `VdInitializeEngines`
o `VdSwap`. Todo diagnóstico de GPU basado en esos logs es inválido.

### H2 — El menú 3D se perdió al regenerar el árbol

- 17:25 → 26 pipelines, menú capturado.
- 17:45 → se añade `config/gap_chunks.toml`.
- 17:46-17:49 → regeneración.
- 17:50 → build actual, **31 pipelines**, y el menú deja de aparecer.

Correlación fuerte; causalidad **no probada**. El test de discriminación está en el
handoff (§6.3): repetir con input determinista y, si falla, rebuild sin `gap_chunks.toml`.

### H3 — `functions.toml` es una semilla huérfana

19.982 entradas construidas desde `.pdata` que **el codegen no consume**: no están en
`neverdead_manifest.toml` ni en `codegen.d`. El árbol se genera sólo con `.pdata` +
`vtables.toml` (3.420 labels interiores declarados como *chunks*) + `gap_chunks.toml`
(6) + CRT.

La doc de ReXGlue dice que los chunks **no son entry points**; el proyecto los usa como
si lo fueran y funciona, pero es una desviación que hay que justificar o corregir.

### H4 — El VEH enmascara fallos

`src/crash_diag.cpp:47-69` repara divisiones por cero del worker de GPU y `:73-124`
commitea páginas y continúa ante AVs. Cualquier métrica de "sobrevive" obtenida con el VEH
activo mezcla "no falla" con "falla reparado".

### H5 — `CESA.bik` no existe y no es recuperable

27 `.bik` en `assets/FMV/`, ninguno CESA; `default.xex.bak` busca `GAME:\FMV\*.bik` por
wildcard; barrido de los 54 `.asrBE` sin `CESA` ni `.bik`. No es un bug de path.

---

## 3. Los tres ZIP y Sniper Elite V2

- `AsuraEngineExtractor` y `quickbms/asura.bms` entienden RSFL/RSCF **little-endian**;
  los `.asrBE` de Xbox 360 son **big-endian** con fourcc invertido. El extractor falla;
  un parser de ~40 líneas sí resuelve los 54 (verificado en 3 muestras).
- `asura.bms` es el único que entiende `ASTS` (banco de sonido), pero compara fourcc
  literales y necesita 4 constantes parcheadas.
- `sr-sev2.iso` es un **instalador** SKIDROW (Setup.exe + .bin), no un árbol de assets.
- **Ninguno desbloquea el 3D.** `assets/` no tiene geometría, shaders ni código.

---

## 4. Cambios hechos en el repo

| Fichero | Qué es |
|---|---|
| `docs/HANDOFF_GEMINI_ARGON_v2.md` | **Entregable principal.** Contexto completo + 11 preguntas para Gemini |
| `docs/REVIEW_INFORME_2026-10-24.md` | Revisión adversarial del informe de la otra IA |
| `docs/INFORME_SESION_2026-10-24.md` | Este documento |
| `tools/asura_extractor/` | ZIP extraído para analizarlo (no se modificó ningún asset) |
| `build_clang/cmp_ul.png`, `shots/bright_10_menu.png` | Artefactos de análisis visual |

No se tocó `generated/`, `src/`, `config/` ni ningún asset.

---

## 5. Próximos pasos (orden innegociable)

1. **P1 — Observabilidad:** hookear `__imp__VdSwap` / `__imp__VdInitializeEngines` con
   contador. Sin esto, el resto es fe.
2. **P2 — VEH a log-only:** medir AVs reales.
3. **P0/P3 — Reproducir el menú:** input determinista (pad START + Enter a t=5 s), 3
   corridas; si falla, bisect sin `gap_chunks.toml`.
4. **P4 — Resolver los 8 unresolved** del codegen con el mecanismo que indique Gemini.
5. **P5 — N4/N5:** menú → Single Player → frame in-game con draw calls y depth buffer.

---

## 6. Riesgos abiertos

- **El build bueno no está archivado.** El `neverdead.exe` de las 17:25 se sobrescribió.
  Cualquier conclusión sobre "qué cambió" es inferencia por mtime.
- **La captura del menú no tiene log contemporáneo.** `neverdead_051.log` termina 3 s
  antes de la captura.
- **El VEH puede estar tapando un fallo real** que explique la regresión del menú.
- **`functions.toml` huérfano**: o se integra con validación, o se archiva; dejarlo en
  `config/` invita a que el siguiente agente lo use sin saber que está fuera del manifest.
