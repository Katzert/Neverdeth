<div align="center">
  <img src="docs/assets/neverdeth_logo_transparent.png" alt="Neverdeth" width="420" />
  <p>
    <a href="README.md">English</a> | <b>Español</b>
  </p>
</div>

**Neverdeth** es un EmuComp no oficial para Windows x64 de *NeverDead* (Xbox 360, 2012). Recompila estáticamente el ejecutable PowerPC a C++23 con el ReXGlue SDK y conecta el runtime de Xbox 360 y las llamadas gráficas de Xenos con un backend Direct3D 12.

Este repositorio no contiene código original ni recursos propietarios del juego. Es necesario disponer de una copia original del juego para Xbox 360.

## Características

- **Arquitectura EmuComp x64:** Recompilación estática del ejecutable PowerPC junto con una capa de traducción para llamadas XDK y Xenos a Direct3D 12, sin sobrecarga de máquinas virtuales.
- **Caché de Shaders Bajo Demanda:** Biblioteca local de pipelines Direct3D 12 y caché de PSOs en memoria. Los nuevos estados se compilan cuando se necesitan y sus descriptores se persisten con un escritor asíncrono en segundo plano. La pre-compilación masiva al arrancar es opcional (`ND_PSO_PRECOMPILE=1`).
- **Opciones de Framerate:** `30 FPS`, `60 FPS` y desbloqueado, seleccionables en el menú F10 o en `config/settings.ini`. Los menús y las cinemáticas Bink se mantienen a 30 FPS; el 3D usa el VBlank nativo.
- **Menú Gráfico en Tiempo Real (F10):** Permite activar o desactivar en vivo sombras, SSAO, rayos volumétricos, niebla, bloom HDR, desenfoque de movimiento, RMLAA y el HUD.
- **Extractor Integrado:** Lee los datos del juego desde imágenes `.iso` desencriptadas (XISO/XGD2), contenedores Games on Demand (`.data`) o carpetas extraídas en el primer inicio.

## Requisitos del Sistema

- **Sistema Operativo:** Windows 10 (64 bits, versión 20H2 o superior) o Windows 11
- **GPU:** Tarjeta gráfica compatible con Direct3D 12 (NVIDIA GeForce serie GTX 900+, AMD Radeon serie RX 5000+ o Intel Arc)
- **Runtime:** [Microsoft Visual C++ 2015-2022 Redistributable (x64)](https://aka.ms/vs/17/release/vc_redist.x64.exe)
- **Control:** Mando compatible con XInput obligatorio (mando de Xbox, o DualShock/DualSense mediante emulador XInput). No hay soporte para jugar con teclado.
- **Datos del Juego:** Copia original sin modificar de *NeverDead* para Xbox 360 (Title ID `4B4E081F`) en formato `.iso` desencriptado (XISO/XGD2), contenedor Games on Demand o carpeta con `default.xex`

## Instalación y Uso

1. Descarga el ZIP más reciente desde la sección [Releases](https://github.com/Katzert/Neverdeth/releases) y descomprímelo en una unidad con al menos 7 GB de espacio libre.
2. Ejecuta `Neverdeth.exe`.
3. En el asistente de primer inicio, selecciona tu imagen `.iso`, contenedor `.data` o carpeta del juego. Al terminar la extracción el juego arranca automáticamente; la caché de shaders se va poblando bajo demanda conforme aparecen nuevos pipelines.

> **Nota:** Las imágenes `.iso` tipo Redump con cifrado físico de disco deben desencriptarse o convertirse a formato XISO estándar antes de usarse.

## Configuración

Pulsa **F10** en cualquier momento durante la partida para abrir la ventana de ajustes, o edita manualmente el archivo `config/settings.ini`:

- **Framerate:** El framerate desbloqueado en gameplay 3D está activado por defecto (`UnlockFramerate = true`) y puede limitarse a 60 o 30 FPS desde el menú F10 o en `config/settings.ini`.
- **Opciones Gráficas:** Los ajustes individuales de sombras, oclusión ambiental (SSAO), rayos de luz, niebla, bloom, motion blur, antialiasing (RMLAA) y visibilidad del HUD se guardan automáticamente en `config/settings.ini`.

## Estado Alpha

Es una alpha temprana: pueden faltar efectos, aparecer tirones por compilación de shaders al entrar por primera vez al 3D y el comportamiento varía según el driver de la GPU. Se agradecen reportes con logs `ND_DIAG=1`.

## Compilación desde el Código Fuente

### Requisitos de Compilación

- Visual Studio 2022 Build Tools y Windows 10/11 SDK
- Clang/LLVM 18+
- CMake 3.25+ y Ninja
- Python 3.10+ (más `Pillow` si ejecutas `tools/gameplay_qa.py`)
- [ReXGlue SDK 0.10.0](https://github.com/rexglue/rexglue-sdk) instalado (vía `CMAKE_PREFIX_PATH` o ruta por defecto, o `REXSDK_DIR` si usas un checkout fuente del SDK)
- Tu propia copia original de `NeverDead` para Xbox 360 (`assets/default.xex` extraído en `assets/` antes de ejecutar `rexglue codegen`)

### Comandos de Compilación

1. Aplica el parche de cabeceras del SDK siguiendo [patches/sdk/README.md](patches/sdk/README.md) sobre el directorio `include` de tu ReXGlue SDK.
2. Coloca `default.xex` en `assets/` y genera el código C++ recompilado a partir de `neverdead_manifest.toml`:

```powershell
rexglue codegen neverdead_manifest.toml
```

3. Asegúrate de tener configuradas las variables `INCLUDE`, `LIB` y `PATH` en tu terminal para MSVC Build Tools, Windows SDK y LLVM/Clang, y ejecuta:

```powershell
cmake --preset clang-release
ninja -C build_clang neverdead
```

Para generar el paquete distribuible en `dist/`:

```powershell
python tools/package_release.py
```

## Agradecimientos

Desarrollado sobre el toolchain de recompilación estática [ReXGlue SDK](https://github.com/rexglue), apoyado en la documentación e investigación de hardware del proyecto [Xenia](https://xenia.jp/).

*NeverDead* fue desarrollado por Rebellion Developments y publicado por Konami Digital Entertainment. Este proyecto no tiene fines comerciales ni está afiliado ni respaldado por Rebellion Developments ni Konami.

## Apoyo

Neverdeth es un proyecto personal por afición. Si quieres colaborar para probar más herramientas y hardware, o simplemente invitarme un café, puedes hacerlo aquí:

- **Patreon:** [patreon.com/Katzert](https://www.patreon.com/Katzert)
- **USDC / USDT (EVM):** `0x2486534a24CB3a2108DF2F73f3872B89bb7032C9`
  *(Redes compatibles: Polygon, Arbitrum, BNB Chain)*

## Licencia

Este proyecto se publica bajo la [Licencia MIT](LICENSE). El código fuente, los scripts de compilación y la documentación se ofrecen tal cual, sin garantía alguna.

*NeverDead* y todos los recursos, marcas registradas y derechos de autor del juego pertenecen a Rebellion Developments y Konami Digital Entertainment. Este proyecto no incluye datos del juego y requiere la copia original obtenida legalmente por el usuario. La licencia MIT aplica únicamente al código y herramientas propias de Neverdeth, no a propiedad intelectual de terceros ni del juego.
