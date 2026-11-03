# Script para compilar neverdead.exe con Clang (LLVM) y Ninja
$vcvars = "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvarsall.bat"
$cmake = "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
$sdk = "C:/Users/katze/.gemini/antigravity-ide/scratch/vibe-recomp/rexglue_sdk/win-amd64"
$llvmBin = "C:\Program Files\LLVM\bin"
$ninjaDir = "C:\Users\katze\AppData\Local\Programs\Python\Python312\Scripts"

# Cargar entorno MSVC para headers de Windows SDK y CRT
$tempBatch = [System.IO.Path]::GetTempFileName() + ".bat"
"@call `"$vcvars`" x64 >nul 2>&1`r`n@set" | Set-Content -Path $tempBatch -Encoding ASCII

cmd.exe /c $tempBatch | ForEach-Object {
    if ($_ -match '^(.*?)=(.*)$') {
        [System.Environment]::SetEnvironmentVariable($matches[1], $matches[2], "Process")
    }
}
Remove-Item $tempBatch -ErrorAction SilentlyContinue

$env:PATH = "$llvmBin;$ninjaDir;$env:PATH"

Write-Host "[*] Entorno MSVC + Clang 23.1 configurado."
Write-Host "[*] Clang++: $(Get-Command clang++.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)"
Write-Host "[*] Ninja:   $(Get-Command ninja.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)"
Write-Host "[*] CMake:   $cmake"

Set-Location "C:\Users\katze\.gemini\antigravity-ide\scratch\vibe-recomp\neverdead_project"

Write-Host "[*] Configurando proyecto con CMake (Clang + Ninja)..."
& $cmake -B build_clang -G "Ninja" -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH="$sdk" -DCMAKE_C_COMPILER="clang.exe" -DCMAKE_CXX_COMPILER="clang++.exe"

if ($LASTEXITCODE -ne 0) {
    Write-Error "Fallo en CMake configure (codigo $LASTEXITCODE)"
    exit $LASTEXITCODE
}

Write-Host "[*] Compilando neverdead.exe con Ninja..."
& $cmake --build build_clang --config Release

if ($LASTEXITCODE -ne 0) {
    Write-Error "Fallo en compilacion Ninja (codigo $LASTEXITCODE)"
    exit $LASTEXITCODE
}

Write-Host "[OK] Compilacion exitosa. Binario neverdead.exe generado con Clang y Ninja."
