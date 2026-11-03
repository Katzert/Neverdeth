@echo off
setlocal
echo [*] Inicializando entorno MSVC 2022 x64...
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvarsall.bat" x64
if %errorlevel% neq 0 (
    echo [!] Error al cargar vcvarsall.bat
    exit /b %errorlevel%
)

set "CMAKE_BIN=C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
set "SDK_DIR=C:/Users/katze/.gemini/antigravity-ide/scratch/vibe-recomp/rexglue_sdk/win-amd64"
set "PATH=C:\Users\katze\AppData\Local\Programs\Python\Python312\Scripts;%PATH%"

cd /d "C:\Users\katze\.gemini\antigravity-ide\scratch\vibe-recomp\neverdead_project"

echo [*] Configurando CMake con Ninja y MSVC...
"%CMAKE_BIN%" -B build_msvc -G "Ninja" -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH="%SDK_DIR%"
if %errorlevel% neq 0 (
    echo [!] Error en CMake configure
    exit /b %errorlevel%
)

echo [*] Compilando neverdead.exe con Ninja...
"%CMAKE_BIN%" --build build_msvc --config Release
if %errorlevel% neq 0 (
    echo [!] Error en CMake build
    exit /b %errorlevel%
)

echo [✓] Compilacion completada con exito.
exit /b 0
