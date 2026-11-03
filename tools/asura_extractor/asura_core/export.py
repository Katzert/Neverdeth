"""Export helpers shared between the CLI utilities and the GUI."""

from __future__ import annotations

import contextlib
import io
import wave
from pathlib import Path
from typing import Dict, Tuple

from .config import (
    MAGIC_TO_IMAGE_FORMAT,
    SUPPORTED_AUDIO_EXTENSIONS,
    SUPPORTED_TEXTURE_EXTENSIONS,
)


def is_texture_entry(entry_name: str) -> bool:
    """Return ``True`` when the entry name uses a supported texture suffix."""

    suffix = Path(entry_name).suffix.lower()
    return suffix in SUPPORTED_TEXTURE_EXTENSIONS

def is_image_entry(entry_name: str) -> bool:
    """Alias for :func:`is_texture_entry` maintained for GUI compatibility."""

    return is_texture_entry(entry_name)

def is_audio_entry(entry_name: str) -> bool:
    """Return ``True`` when the entry name uses a supported audio suffix."""

    suffix = Path(entry_name).suffix.lower()
    return suffix in SUPPORTED_AUDIO_EXTENSIONS

def is_supported_entry(entry_name: str) -> bool:
    """Return ``True`` for entries that represent textures or audio payloads."""

    return is_texture_entry(entry_name) or is_audio_entry(entry_name)

def detect_image_format(payload: bytes) -> Tuple[str | None, str | None]:
    """Return a tuple of (format_name, extension) for recognised image payloads."""

    for magic, format_name, extension in MAGIC_TO_IMAGE_FORMAT:
        if payload.startswith(magic):
            return format_name, extension
    return None, None

def detect_wav_metadata(payload: bytes) -> Dict[str, int] | None:
    """Return audio metadata for RIFF/WAVE payloads when available."""

    if len(payload) < 12:
        return None
    if not payload.startswith(b"RIFF"):
        return None
    if payload[8:12] != b"WAVE":
        return None

    try:
        with contextlib.closing(wave.open(io.BytesIO(payload))) as wav_file:
            channels = wav_file.getnchannels()
            sample_rate = wav_file.getframerate()
            sample_width = wav_file.getsampwidth()
    except (wave.Error, EOFError, OSError):
        return None

    if channels <= 0 or sample_rate <= 0 or sample_width <= 0:
        return None

    return {
        "channels": int(channels),
        "sample_rate": int(sample_rate),
        "bits_per_sample": int(sample_width * 8),
    }

def _coerce_audio_metadata(value: object) -> Dict[str, int] | None:
    """Return a validated copy of audio metadata from metadata dictionaries."""

    if not isinstance(value, dict):
        return None

    channels = value.get("channels")
    sample_rate = value.get("sample_rate")
    bits_per_sample = value.get("bits_per_sample")

    if not all(isinstance(field, int) for field in (channels, sample_rate, bits_per_sample)):
        return None
    if channels <= 0 or sample_rate <= 0 or bits_per_sample <= 0:
        return None

    return {
        "channels": channels,
        "sample_rate": sample_rate,
        "bits_per_sample": bits_per_sample,
    }

def resolve_export_relative_path(relative_path: str, payload: bytes) -> Tuple[str, str | None]:
    """Return the preferred export path for ``relative_path`` based on ``payload``."""

    original = Path(*relative_path.split("/")) if relative_path else Path()
    audio_metadata = detect_wav_metadata(payload)
    extension: str | None = None
    if audio_metadata is not None:
        extension = ".wav"
    else:
        _, image_extension = detect_image_format(payload)
        extension = image_extension

    if extension and original.suffix.lower() != extension:
        adjusted = original.with_suffix(extension)
        return str(adjusted.as_posix()), str(original.as_posix())
    return str(original.as_posix()), None

