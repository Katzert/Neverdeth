"""Audio preview helpers and sounddevice integration."""

from __future__ import annotations

import array
import contextlib
import io
import sys
import wave
from typing import Dict, List

np = None
NUMPY_AVAILABLE = False
try:  # pragma: no cover - numpy is an optional dependency.
    import numpy as np  # type: ignore[assignment]

    NUMPY_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency.
    pass

sounddevice = None
SOUNDDEVICE_AVAILABLE = False
try:  # pragma: no cover - sounddevice is an optional dependency.
    import sounddevice  # type: ignore[assignment]

    SOUNDDEVICE_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency.
    pass

def _sounddevice_backend_available() -> bool:
    """Return True when sounddevice + numpy playback is available."""

    return (
        SOUNDDEVICE_AVAILABLE
        and NUMPY_AVAILABLE
        and sounddevice is not None
        and np is not None
    )

def _prepare_preview_audio(payload: bytes) -> Dict[str, object]:
    """Return PCM data and metadata for WAV previews."""

    stream = io.BytesIO(payload)
    with contextlib.closing(wave.open(stream)) as wav_file:
        params = wav_file.getparams()
        frame_count = int(params.nframes)
        frame_rate = int(params.framerate)
        channels = int(params.nchannels)
        sample_width = int(params.sampwidth)
        duration = (frame_count / frame_rate) if frame_rate else 0.0
        wav_file.rewind()
        pcm_bytes = wav_file.readframes(frame_count)

    waveform: List[float] | None = None
    if pcm_bytes and sample_width == 2 and channels > 0:
        samples = array.array("h")
        samples.frombytes(pcm_bytes)
        if sys.byteorder != "little":
            samples.byteswap()
        frame_total = len(samples) // channels if channels else 0
        if frame_total:
            step = max(1, frame_total // 512)
            waveform = []
            for frame_index in range(0, frame_total, step):
                if len(waveform) >= 512:
                    break
                start = frame_index * channels
                end = start + channels
                if end > len(samples):
                    break
                frame_slice = samples[start:end]
                if not frame_slice:
                    break
                if channels == 1:
                    value = float(frame_slice[0])
                else:
                    value = float(sum(frame_slice) / len(frame_slice))
                waveform.append(value)

    return {
        "pcm": pcm_bytes,
        "sample_rate": frame_rate,
        "channels": channels,
        "sample_width": sample_width,
        "frame_count": frame_count,
        "duration": duration,
        "waveform": waveform,
    }

def _pcm_to_float32(
    pcm_bytes: bytes, sample_width: int, channels: int
) -> "numpy.ndarray":
    """Normalise PCM bytes into a float32 numpy array for sounddevice."""

    if not _sounddevice_backend_available() or np is None:
        raise RuntimeError("sounddevice playback is unavailable")

    if channels <= 0:
        raise ValueError("invalid channel count")

    if sample_width == 1:
        data = np.frombuffer(pcm_bytes, dtype=np.uint8).astype(np.float32)
        data = (data - 128.0) / 128.0
    elif sample_width == 2:
        data = np.frombuffer(pcm_bytes, dtype="<i2").astype(np.float32)
        data /= 32768.0
    elif sample_width == 3:
        raw = np.frombuffer(pcm_bytes, dtype=np.uint8)
        frame_total = raw.size // 3
        if frame_total == 0:
            raise ValueError("no audio frames available")
        trimmed = raw[: frame_total * 3].reshape(frame_total, 3)
        signed = (
            trimmed[:, 0].astype(np.int32)
            | (trimmed[:, 1].astype(np.int32) << 8)
            | (trimmed[:, 2].astype(np.int32) << 16)
        )
        negative_mask = signed & 0x0080_0000
        signed = np.where(negative_mask != 0, signed | -0x0100_0000, signed)
        data = signed.astype(np.float32) / float(1 << 23)
    elif sample_width == 4:
        data = np.frombuffer(pcm_bytes, dtype="<i4").astype(np.float32)
        data /= 2147483648.0
    else:
        raise ValueError(f"unsupported sample width: {sample_width}")

    frames = data.size // channels
    if frames == 0:
        raise ValueError("audio payload does not contain complete frames")
    data = data[: frames * channels]
    if channels == 1:
        result = data.reshape(frames)
    else:
        result = data.reshape(frames, channels)
    return np.ascontiguousarray(result, dtype=np.float32)

def _describe_preview_format(
    format_hint: str | None,
    detected_format: str | None,
    dds_info: Dict[str, object] | None,
) -> Tuple[str, str]:
    """Return the status and info label strings for a previewed texture."""

    base = (format_hint or detected_format or "").strip()
    dds_label_text = ""

    if isinstance(dds_info, dict):
        compression = dds_info.get("compression")
        channel_masks = dds_info.get("channel_masks") or {}
        mask_parts = [
            f"{channel}={int(channel_masks.get(channel, 0)):#010x}"
            for channel in ("R", "G", "B", "A")
        ]
        mask_text = ", ".join(mask_parts) if mask_parts else ""
        if compression:
            base = f"DDS {compression}" + (f", {mask_text}" if mask_text else "")
            info_sections = [str(compression)]
        else:
            base = f"DDS {mask_text}" if mask_text else "DDS"
            info_sections = []
        if mask_text:
            info_sections.append(mask_text)
        if info_sections:
            dds_label_text = f"DDS • {' • '.join(info_sections)}"
        else:
            dds_label_text = "DDS"

    if not base:
        base = "unknown"

    return base, dds_label_text

