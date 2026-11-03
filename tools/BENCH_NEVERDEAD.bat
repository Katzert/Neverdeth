@echo off
rem BENCH_NEVERDEAD.bat - benchmark manual con telemetria encendida.
rem Sin ND_DIAG=1 NO hay lineas [FRAME] y cualquier medicion da 0 muestras.
cd /d "%~dp0.."
set ND_DIAG=1
set ND_PROFILE_THREADS=1
echo ============================================================
echo   NeverDead BENCH (telemetria ON: ND_DIAG=1)
echo   - A (base):            build_clang\neverdead.exe --game_data_root assets --cache_path cache
echo   - B (sin tiled shm):   anadir --no-d3d12_tiled_shared_memory
echo   - C (preset 16 flags): F1 en el juego, boton "Preset: PC Bajo Recursos"
echo   Arnes automatico:      python tools\bench_v6.py A   (o B / C)
echo   Los logs van a build_clang\logs\ ; compara con scratch\bench_v6_*.log
echo ============================================================
build_clang\neverdead.exe --game_data_root assets --cache_path cache
