# Neverdeth v0.1.1-alpha

Maintenance release. / Versión de mantenimiento.

## English

- **Startup:** the shader-compilation overlay is no longer created while the
  graphics device is still initializing, which removes a UI-thread deadlock that
  could leave the window unresponsive before the first frame.
- **3D pacing:** the native VBlank worker is left untouched. Overriding its wait
  branch at runtime could stall rendering as soon as gameplay started.
- **Shutdown:** pipeline descriptors are persisted by the asynchronous bundle
  writer; serializing the D3D12 pipeline library from the window close handler
  (which raced with render threads) has been removed.
- **Robustness:** bounded validation when reading the cached pipeline
  descriptors, serialized access to the D3D12 pipeline library, and opt-in
  background bulk pre-compilation (`ND_PSO_PRECOMPILE=1`).
- **Docs & Config:** updated `README.md`, `README.es.md`, and configuration
  comments to reflect the current runtime defaults (`UnlockFramerate = true`),
  keyboard bindings in `neverdead.toml`, and `clang-release` build preset.

## Español

- **Arranque:** el indicador de compilación de shaders ya no se crea mientras el
  device gráfico se está inicializando, evitando un bloqueo del hilo de interfaz
  antes del primer frame.
- **Ritmo en 3D:** se respeta el worker nativo de VBlank; modificar su espera en
  caliente podía detener el renderizado al empezar la partida.
- **Cierre:** los descriptores de pipelines se persisten con el escritor
  asíncrono; se eliminó la serialización de la biblioteca D3D12 desde el cierre
  de la ventana, que competía con los hilos de render.
- **Robustez:** validación acotada al leer la caché de descriptores, acceso
  serializado a la biblioteca de pipelines y pre-compilación masiva opcional
  (`ND_PSO_PRECOMPILE=1`).
- **Documentación y Configuración:** actualizados `README.md`, `README.es.md` y
  los comentarios de configuración para reflejar los valores por defecto
  actuales (`UnlockFramerate = true`), los mapeos de teclado en `neverdead.toml`
  y el preset de compilación `clang-release`.

## Notes / Notas

This is still an alpha and requires your own unmodified Xbox 360 copy of
*NeverDead* (Title ID `4B4E081F`). No game data is included in this package.
Sigue siendo una alpha y requiere tu copia original sin modificar de *NeverDead*
(Title ID `4B4E081F`). El paquete no incluye datos del juego.
