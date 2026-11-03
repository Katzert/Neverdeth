@echo off
title NeverDead PC Recompilation (F8 para alternar 60 / 30 FPS, F1 para Menu)
cd /d "%~dp0"
echo ============================================================
echo   Iniciando NeverDead PC Recomp (ReXGlue SDK - ROV Nativo)
echo   [F1] Menu de Opciones y Optimizacion iGPU
echo   [F8] Alternar en vivo entre 60 FPS y 30 FPS
echo   Directorio del proyecto: %~dp0
echo ============================================================

set PATH=%~dp0build_clang;%PATH%
rem El clamp de FPS del invitado se deja siempre en 0.0f (ver nd_hooks.cpp):
rem con 60.0f el motor Asura se bloquea tras el primer swap y la ventana
rem queda en negro aunque se sigan presentando frames. El limite real de
rem FPS lo aplica el pacer del host (menu F1 / tecla F8).
build_clang\neverdead.exe --game_data_root assets --cache_path cache

set EXIT_CODE=%ERRORLEVEL%
if %EXIT_CODE% neq 0 (
    echo.
    echo [ERROR] neverdead.exe finalizo con codigo: %EXIT_CODE%
    echo Revisa el log mas reciente en build_clang\logs\  ^(o relanza con: set ND_DIAG=1^)
    pause
)
