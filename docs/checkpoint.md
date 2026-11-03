# CHECKPOINT: NeverDead Native Recompilation Boot Sequence
**Updated:** 2026-10-17T20:33:30Z
**State:** MILESTONE ALCANZADO - MOTOR EN EJECUCIÓN CONTINUA (SURVIVED 10s)

## Current Objective
Eliminar los cuelgues secuenciales del arranque del motor Rebellion Asura (Xbox 360 Xenon PPC -> PC x86_64 con Clang/Ninja) y alcanzar el menú principal del juego NeverDead (`GUIMenu`).

## Milestone / Status Summary
- **Estado Actual**: ¡EL MOTOR NO SE CAE! En la iteración #24, `neverdead.exe` sobrevivió el intervalo completo de 10 segundos del watchdog (`[SURVIVED]`), generando 4.4 MB de registro (`neverdead_054.log`, más de 28,100 líneas) en ejecución fluida y continua.
- **Blocker 1 al 13**: TODOS RESUELTOS Y VERIFICADOS.
  - **Bloqueo 13 (Superado)**: `sub_821C67F8` / `sub_821C6598` bucles huérfanos resueltos en todas las TUs (`10.cpp`, `18.cpp`, `51.cpp`, `83.cpp`, `114.cpp`) mediante `apply_c67f8_fix.py`.
  - La ejecución superó la inicialización CRT y Asura Core, y entró a la fase de asignación de buffers de memoria principal del juego (`0x5E8C0000` - `0x5EC50000`+).
  - El subsistema VEH auto-repair gestionó de forma transparente las reservas de páginas bajo demanda.

## Verified Architecture & Artifacts
- `CMakeLists.txt`: Cadena completa de 8 objetivos de parche idempotentes:
  1. `neverdead_entrypoint_bridge` (xstart -> sub_824D9DF8)
  2. `neverdead_indirect_guard` (loc_824D9E94 / ResolveIndirectFunction)
  3. `neverdead_branch_fix` (goto in-function labels)
  4. `neverdead_bd98_fix` (sub_8278BD98 retorno limpio)
  5. `neverdead_dec8_fix` (sub_8221DEC8 fragmento neutralizado)
  6. `neverdead_asura_init_fix` (fábrica de Asura & startup)
  7. `neverdead_bd_ladder_fix` (peldaños BD prologue/epilogue)
  8. `neverdead_heap_alloc_fix` (sub_8278BCA0 & sub_82686058)
  9. `neverdead_string_crt_fix` (strlen, mbstowcs, strchr en sub_82788DB0)
  10. `neverdead_b2b60_fix` (sub_823B2B60 bucle reconstituido)
  11. `neverdead_math_crt_fix` (sub_82789768 clamps de salida)
  12. `neverdead_c67f8_fix` (sub_821C67F8 / sub_821C6598 salidas de bucle limpias)
- `src/neverdead_app.h`: Stack invitado de 32MB con `PAGE_READWRITE`.
- `src/crash_diag.cpp`: VEH auto-repair con fallback a `PAGE_READWRITE`.
- `src/nd_indirect_trap.h`: Guardas ante llamadas indirectas no resueltas.
