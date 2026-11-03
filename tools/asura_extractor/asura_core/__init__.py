"""Public API re-exports for the Asura core package."""

from __future__ import annotations

from . import archive as _archive
from . import config as _config
from . import export as _export
from .gui import app as _app
from .gui import audio as _audio
from .gui import preview as _preview

from .config import *  # noqa: F401,F403
from .export import *  # noqa: F401,F403
from .archive import *  # noqa: F401,F403
from .gui.preview import *  # noqa: F401,F403
from .gui.audio import *  # noqa: F401,F403
from .gui.app import *  # noqa: F401,F403

__all__: list[str] = []
for module in (_config, _export, _archive, _preview, _audio, _app):
    names = getattr(module, "__all__", None)
    if names is None:
        names = [name for name in module.__dict__.keys() if not name.startswith("_")]
    __all__.extend(names)

_seen: set[str] = set()
__all__ = [name for name in __all__ if not (name in _seen or _seen.add(name))]

_EXTRA_PRIVATES = [
    "_prepare_preview_audio",
    "_prepare_preview_image",
    "_prepare_photo_image",
    "_parse_dds_header",
    "_decode_alpha_only_dds",
    "_decode_uncompressed_color_dds",
    "_decode_block_compressed_dds",
    "_composite_transparency_preview",
    "_encode_image_to_dds",
    "_pcm_to_float32",
    "_sounddevice_backend_available",
    "_describe_preview_format",
    "_release_archive_buffer",
    "_write_bytes_in_chunks",
    "_wrap_asura_container",
]

for name in _EXTRA_PRIVATES:
    for module in (_preview, _audio, _archive, _app):
        if hasattr(module, name):
            globals()[name] = getattr(module, name)
            __all__.append(name)
            break

_seen.clear()
__all__ = [name for name in __all__ if not (name in _seen or _seen.add(name))]
