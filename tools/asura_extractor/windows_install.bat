@echo off
setlocal

if "%~1"=="" (
    set "PYTHON=python"
) else (
    set "PYTHON=%~1"
)

%PYTHON% -m pip install --upgrade pip
%PYTHON% -m pip install -r "%~dp0..\requirements.txt"

echo.
echo Dependencias instaladas correctamente.
endlocal
