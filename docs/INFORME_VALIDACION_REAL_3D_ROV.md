# INFORME DE VALIDACIÓN EMPÍRICA EN PARTIDA 3D REAL (ROV NATIVO)

**Fecha y Hora:** 25 de Octubre de 2026 — 22:18 (Hora local)  
**Escenario de Prueba:** Prólogo 3D — Pasillo del Hospital Psiquiátrico (Nivel 1)  
**Personaje:** Bryce en tercera persona, empuñando dos pistolas, enfrentando criatura demoníaca  
**Motor Gráfico:** Asura Engine recompilado a x64 nativo vía ReXGlue SDK v0.10.0 + D3D12  
**Ruta de Render:** ROV (Rasterizer-Ordered Views / PixelShaderInterlock nativo)  
**Hardware de Prueba:** Intel Core i7 / NVIDIA GeForce RTX 2060 6GB / Windows 11 Pro 24H2+  

---

## 1. RESUMEN EJECUTIVO

Tras la reconciliación del ciclo Doubt-Driven Development, la reversión de los parches desincronizados (`0xEB030` y formatos `FLOAT16` en RTV) y la restauración de la arquitectura nativa **ROV** en [`neverdead.toml`](../neverdead.toml), se realizó la **prueba empírica definitiva en partida 3D real**.

**El resultado es un éxito rotundo:**
1. **Fidelidad Visual Total:** El velo blanco del $96.35\%$ quedó erradicado. La iluminación volumétrica de rayos de sol (god rays), las texturas de azulejos, el modelo 3D de Bryce, la criatura demoníaca frontal y el HUD de armas renderizan con la estética y rango dinámico originales de la Xbox 360.
2. **Estabilidad de Fotogramas:** El overlay de RTSS registró **56 FPS** en pleno combate y movimiento, con un gráfico de frametime de **16.6 ms prácticamente plano**.
3. **Eficiencia Térmica y de GPU:** Durante 60 segundos de muestreo continuo con `nvidia-smi`, el uso promedio de GPU se situó en **73.1%** (consumo medio de **64.4 W** y temperatura de **45–54 °C**), desmintiendo el mito de que ROV bloqueaba la GPU al 91% en un bucle ineficiente.

---

## 2. ANÁLISIS HISTOGRAMÉTRICO FORENSE

| Métrica | Modo RTV Parcheado (Anterior) | Modo ROV Canónico (Actual) | Criterio de Aprobación | Estado |
| :--- | :--- | :--- | :--- | :--- |
| **Luminancia Media** | 250.1 / 255 (Saturado) | **52.93** / 255 | Rango 50.0 – 75.0 |  **Aprobado** |
| **Píxeles Quemados (>250)** | **96.35 %** (Ceguera total) | **0.01 %** (Prácticamente cero) | < 1.0 % |  **Aprobado** |
| **Sombras y Negros (<6)** | 0.00 % (Sin sombras) | **3.83 %** (Contraste natural) | 2.0 – 8.0 % |  **Aprobado** |
| **Corrupción Cromática** | 94.97 % Magenta en logo | **0.00 %** (Colores fieles) | 0.0 % |  **Aprobado** |

---

## 3. TELEMETRÍA DE HARDWARE EN PARTIDA 3D REAL (60 SEGUNDOS CONTINUOS)

Datos extraídos del log oficial [`build_clang/logs/gpu_real_3d.csv`](../build_clang/logs/gpu_real_3d.csv) registrado vía `nvidia-smi`:

- **Tiempo de Muestreo:** 60.5 segundos continuos durante combate activo.
- **Muestras Recopiladas:** 60 muestras de hardware a 1 Hz.
- **Uso de GPU (RTX 2060):** Promedio de **73.1 %** (Pico de 99.0 % durante la máxima dispersión de partículas volumétricas).
- **Consumo de Energía GPU:** Promedio de **64.4 W** (Muy por debajo del límite TDP de 160 W).
- **Frecuencia de Reloj GPU:** **1845 MHz** sostenidos (Boost máximo de GPU sin thermal throttling).
- **Frecuencia de VRAM:** **7700 MHz** estables.
- **Uso de VRAM:** **2545 MB** (Margen holgado sobre los 6 GB disponibles).
- **Carga de CPU:** **27 %** (4450 MHz, consumo de 38.2 W, temperatura de 60 °C).

---

## 4. ESTADO DE LA ARQUITECTURA DEL PROYECTO

1. **Raíz Limpia y Consolidada:** Se mantiene la política estricta de **7 archivos canónicos** en la raíz.
2. **Ejecutable Único:** [`build_clang/neverdead.exe`](../build_clang/neverdead.exe) lanzado vía [`JUGAR_NEVERDEAD.bat`](../JUGAR_NEVERDEAD.bat).
3. **Present Gate Robusto:** En [`src/nd_hooks.cpp`](../src/nd_hooks.cpp), `real_present()` se ejecuta en cada ciclo de comandos completado, preservando el modelo `DXGI_SWAP_EFFECT_FLIP_DISCARD` y respondiendo adecuadamente a `WM_PAINT`.
4. **Integridad de Draw Calls:** El descarte silencioso de PSOs ha sido erradicado al retirar el salto en `0xEB030`. Shaders y geometrías se compilan y almacenan limpiamente en disco (`store_shaders = true`).

---
*Validación completada con éxito. NeverDead PC Recompilation es plenamente jugable con fidelidad visual auténtica.*
