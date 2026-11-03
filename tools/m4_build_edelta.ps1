# M4 (E-Delta, swe_native_3d_v2) - build del ARBOL COPIA del experimento E-Delta.
# Mismo MSVC env + Clang + Ninja + ReXGlue SDK que build_neverdead_v2.ps1, pero:
#   - NEVERDEAD_GENERATED_DIR = <copia del arbol v2 con el cuerpo de
#     sub_8278BD80 sustituido por el bloque real del invitado>
#   - NEVERDEAD_REXGLUE_CMAKE = tools/rexglue_v2_edelta.cmake (derivado
#     mecanicamente de tools/rexglue_v2.cmake con la ruta de la copia)
#   - build dir propio: NO toca build_clang_v2 ni generated/neverdead_default_v2
# El arbol de producto NO se modifica.
$vcvars = "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvarsall.bat"
$cmake = "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
$sdk = "C:/Users/katze/.gemini/antigravity-ide/scratch/vibe-recomp/rexglue_sdk/win-amd64"
$llvmBin = "C:\Program Files\LLVM\bin"
$ninjaDir = "C:\Users\katze\AppData\Local\Programs\Python\Python312\Scripts"
$tree = $env:M4_EDELTA_TREE
$build = $env:M4_EDELTA_BUILD
if (-not $tree -or -not $build) { Write-Error "Faltan M4_EDELTA_TREE / M4_EDELTA_BUILD"; exit 2 }

$tempBatch = [System.IO.Path]::GetTempFileName() + ".bat"
"@call `"$vcvars`" x64 >nul 2>&1`r`n@set" | Set-Content -Path $tempBatch -Encoding ASCII
cmd.exe /c $tempBatch | ForEach-Object {
    if ($_ -match '^(.*?)=(.*)$') { [System.Environment]::SetEnvironmentVariable($matches[1], $matches[2], "Process") }
}
Remove-Item $tempBatch -ErrorAction SilentlyContinue
$env:PATH = "$llvmBin;$ninjaDir;$env:PATH"
Set-Location "C:\Users\katze\.gemini\antigravity-ide\scratch\vibe-recomp\neverdead_project"

Write-Host "[M4/E-Delta] Configurando $build ..."
& $cmake -B $build -G "Ninja" -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH="$sdk" `
    -DCMAKE_C_COMPILER="clang.exe" -DCMAKE_CXX_COMPILER="clang++.exe" `
    -DNEVERDEAD_REXGLUE_CMAKE=C:/Users/katze/.gemini/antigravity-ide/scratch/vibe-recomp/neverdead_project/tools/rexglue_v2_edelta.cmake `
    -DNEVERDEAD_GENERATED_DIR="$tree"
if ($LASTEXITCODE -ne 0) { Write-Error "Fallo en CMake configure ($LASTEXITCODE)"; exit $LASTEXITCODE }
$env:NEVERDEAD_GENERATED_DIR = $tree
Write-Host "[M4/E-Delta] Compilando ..."
& $cmake --build $build --config Release
$e = $LASTEXITCODE
Write-Host "[M4/E-Delta] BUILD_EXITCODE=$e"
exit $e
