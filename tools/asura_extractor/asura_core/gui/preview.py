"""Image preview helpers used by the Tkinter interface."""

from __future__ import annotations

import io
import math
import struct
from typing import Callable, Dict, List, Tuple

from ..config import (
    BLOCK_SIZES,
    DDS_DX10_STRUCT,
    DDS_PIXELFORMAT_STRUCT,
    DDPF_ALPHA,
    DDPF_ALPHAPIXELS,
    DDPF_FOURCC,
    DDPF_LUMINANCE,
    DDPF_RGB,
    DXGI_BLOCK_DECODERS,
    DXGI_FORMAT_NAMES,
    FOURCC_BLOCK_DECODERS,
)

Image = None
ImageDraw = None
ImageTk = None
UnidentifiedImageError = Exception
PIL_AVAILABLE = False
PIL_IMAGETK_AVAILABLE = False
try:  # pragma: no cover - Pillow availability depends on the environment.
    from PIL import Image, ImageDraw, UnidentifiedImageError

    PIL_AVAILABLE = True
except Exception:  # pragma: no cover - Pillow availability depends on the environment.
    pass

if PIL_AVAILABLE:
    try:  # pragma: no cover - ImageTk availability depends on the environment.
        from PIL import ImageTk

        PIL_IMAGETK_AVAILABLE = True
    except Exception:  # pragma: no cover - ImageTk availability depends on the environment.
        pass

def _friendly_dxgi_compression_name(name: str) -> str:
    """Return a succinct description for DXGI compression names."""

    if not name.startswith("BC"):
        return name

    if "_" not in name:
        return name

    base, suffix = name.split("_", 1)
    if not suffix:
        return base

    if suffix == "UNORM":
        return base
    if suffix == "UNORM_SRGB":
        return f"{base} (sRGB)"
    if suffix == "TYPELESS":
        return f"{base} (typeless)"
    if suffix == "SNORM":
        return f"{base} (SNORM)"
    if suffix == "UF16":
        return f"{base} (UF16)"
    if suffix == "SF16":
        return f"{base} (SF16)"

    return f"{base} ({suffix.replace('_', ' ').title()})"

def _composite_transparency_preview(image: "Image.Image", tile_size: int = 16):
    """Composite semi-transparent textures over a checkerboard for previewing."""

    if not PIL_AVAILABLE or ImageDraw is None:
        return image
    if image.mode != "RGBA":
        return image

    alpha_channel = image.getchannel("A")
    extrema = alpha_channel.getextrema()
    if not extrema:
        return image

    min_alpha, max_alpha = extrema
    if min_alpha == 255 and max_alpha == 255:
        return image

    width, height = image.size
    checkerboard = Image.new("RGBA", image.size, (192, 192, 192, 255))
    drawer = ImageDraw.Draw(checkerboard)
    light = (220, 220, 220, 255)
    dark = (160, 160, 160, 255)
    for y in range(0, height, tile_size):
        for x in range(0, width, tile_size):
            color = light if (x // tile_size + y // tile_size) % 2 == 0 else dark
            drawer.rectangle(
                (x, y, min(x + tile_size, width), min(y + tile_size, height)),
                fill=color,
            )

    return Image.alpha_composite(checkerboard, image)

def _prepare_photo_image(source: "Image.Image") -> "ImageTk.PhotoImage":
    """Return a Tk-compatible image, normalising fully opaque textures."""

    if not PIL_IMAGETK_AVAILABLE or ImageTk is None:
        raise RuntimeError("ImageTk is not available")

    display_image = source
    if display_image.mode == "RGBA":
        try:
            alpha_extrema = display_image.getchannel("A").getextrema()
        except Exception:  # pragma: no cover - defensive for unexpected modes.
            alpha_extrema = None
        if alpha_extrema and alpha_extrema[0] == alpha_extrema[1] == 255:
            display_image = display_image.convert("RGB")

    return ImageTk.PhotoImage(display_image)

def _prepare_preview_image(payload: bytes) -> Dict[str, object]:
    """Decode a texture payload and return preview metadata."""

    if not PIL_AVAILABLE or Image is None:
        raise RuntimeError("Pillow is not available")

    stream = io.BytesIO(payload)
    try:
        format_hint = None
        dds_info: Dict[str, object] | None = None
        if payload.startswith(b"DDS "):
            format_hint = "DDS"
            dds_info = _parse_dds_header(payload)

        fallback_display: "Image.Image" | None = None
        try:
            image = Image.open(stream)
            image.load()
            detected_format = image.format
        except (UnidentifiedImageError, OSError, ValueError, NotImplementedError):
            fallback_image: "Image.Image" | None
            fallback_image = None
            if format_hint == "DDS":
                fallback_image, fallback_display = _decode_alpha_only_dds(payload)
                if fallback_image is None:
                    fallback_image, fallback_display = _decode_uncompressed_color_dds(payload)
                if fallback_image is None:
                    fallback_image, fallback_display = _decode_block_compressed_dds(payload)
            if fallback_image is None:
                raise
            image = fallback_image
            detected_format = format_hint or "DDS"
        if image.mode != "RGBA":
            image = image.convert("RGBA")
        display_image = fallback_display or _composite_transparency_preview(image)
    finally:
        stream.close()

    return {
        "image": image,
        "display_image": display_image,
        "format_hint": format_hint,
        "detected_format": detected_format,
        "dds_info": dds_info,
    }

def _encode_luminance_image_to_dds(image: "Image.Image") -> bytes:
    """Return an uncompressed 8-bit luminance DDS payload."""

    if image.mode != "L":
        image = image.convert("L")

    width, height = image.size
    if width <= 0 or height <= 0:
        raise ValueError("image must have positive dimensions")

    pixel_data = image.tobytes("raw", "L")
    row_pitch = width

    header = bytearray(128)
    struct.pack_into("<4s", header, 0, b"DDS ")
    struct.pack_into("<I", header, 4, 124)
    struct.pack_into("<I", header, 8, 0x0002100F)
    struct.pack_into("<I", header, 12, height)
    struct.pack_into("<I", header, 16, width)
    struct.pack_into("<I", header, 20, row_pitch)
    struct.pack_into("<I", header, 24, 0)
    struct.pack_into("<I", header, 28, 0)

    struct.pack_into("<I", header, 76, 32)
    struct.pack_into("<I", header, 80, DDPF_LUMINANCE)
    struct.pack_into("<4s", header, 84, b"\x00\x00\x00\x00")
    struct.pack_into("<I", header, 88, 8)
    struct.pack_into("<I", header, 92, 0xFF)
    struct.pack_into("<I", header, 96, 0)
    struct.pack_into("<I", header, 100, 0)
    struct.pack_into("<I", header, 104, 0)

    struct.pack_into("<I", header, 108, 0x00001000)

    return bytes(header) + pixel_data


def _encode_alpha_image_to_dds(image: "Image.Image") -> bytes:
    """Return an uncompressed 8-bit alpha DDS payload."""

    if image.mode != "L":
        image = image.convert("L")

    width, height = image.size
    if width <= 0 or height <= 0:
        raise ValueError("image must have positive dimensions")

    pixel_data = image.tobytes("raw", "L")
    row_pitch = width

    header = bytearray(128)
    struct.pack_into("<4s", header, 0, b"DDS ")
    struct.pack_into("<I", header, 4, 124)
    struct.pack_into("<I", header, 8, 0x0002100F)
    struct.pack_into("<I", header, 12, height)
    struct.pack_into("<I", header, 16, width)
    struct.pack_into("<I", header, 20, row_pitch)
    struct.pack_into("<I", header, 24, 0)
    struct.pack_into("<I", header, 28, 0)

    struct.pack_into("<I", header, 76, 32)
    struct.pack_into("<I", header, 80, DDPF_ALPHA)
    struct.pack_into("<4s", header, 84, b"\x00\x00\x00\x00")
    struct.pack_into("<I", header, 88, 8)
    struct.pack_into("<I", header, 92, 0)
    struct.pack_into("<I", header, 96, 0)
    struct.pack_into("<I", header, 100, 0)
    struct.pack_into("<I", header, 104, 0xFF)

    struct.pack_into("<I", header, 108, 0x00001000)

    return bytes(header) + pixel_data


def _encode_image_to_dds(image: "Image.Image") -> bytes:
    """Return an uncompressed DDS payload for the provided RGBA image."""

    if image.mode != "RGBA":
        image = image.convert("RGBA")

    width, height = image.size
    if width <= 0 or height <= 0:
        raise ValueError("image must have positive dimensions")

    pixel_data = image.tobytes("raw", "BGRA")
    row_pitch = width * 4

    header = bytearray(128)
    struct.pack_into("<4s", header, 0, b"DDS ")
    struct.pack_into("<I", header, 4, 124)
    # DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PIXELFORMAT | DDSD_PITCH
    struct.pack_into("<I", header, 8, 0x0002100F)
    struct.pack_into("<I", header, 12, height)
    struct.pack_into("<I", header, 16, width)
    struct.pack_into("<I", header, 20, row_pitch)
    struct.pack_into("<I", header, 24, 0)
    struct.pack_into("<I", header, 28, 0)

    struct.pack_into("<I", header, 76, 32)
    struct.pack_into("<I", header, 80, DDPF_RGB | DDPF_ALPHAPIXELS)
    struct.pack_into("<4s", header, 84, b"\x00\x00\x00\x00")
    struct.pack_into("<I", header, 88, 32)
    struct.pack_into("<I", header, 92, 0x00FF0000)
    struct.pack_into("<I", header, 96, 0x0000FF00)
    struct.pack_into("<I", header, 100, 0x000000FF)
    struct.pack_into("<I", header, 104, 0xFF000000)

    struct.pack_into("<I", header, 108, 0x00001000)  # DDSCAPS_TEXTURE

    return bytes(header) + pixel_data

def _parse_dds_header(payload: bytes) -> Dict[str, object] | None:
    """Parse a DDS header to expose compression and channel mask details."""

    if not payload.startswith(b"DDS "):
        return None

    if len(payload) < 4 + 124:
        return None

    header = memoryview(payload)
    try:
        (
            _magic,
            header_size,
            _flags,
            _height,
            _width,
            _pitch_or_linear,
            _depth,
            mipmaps,
        ) = struct.unpack_from("<4s7I", header, 0)
    except struct.error:
        return None

    if header_size != 124:
        return None

    try:
        (
            pf_size,
            pf_flags,
            fourcc_raw,
            rgb_bit_count,
            r_mask,
            g_mask,
            b_mask,
            a_mask,
        ) = DDS_PIXELFORMAT_STRUCT.unpack_from(header, 4 + 72)
    except struct.error:
        return None

    if pf_size != DDS_PIXELFORMAT_STRUCT.size:
        return None

    fourcc = fourcc_raw.rstrip(b"\x00")
    compression = None
    fourcc_name: str | None = None
    dxgi_format_value: int | None = None

    if pf_flags & DDPF_FOURCC and fourcc:
        if fourcc == b"DX10" and len(header) >= 4 + 124 + DDS_DX10_STRUCT.size:
            try:
                (dxgi_format, _, _, _, _) = DDS_DX10_STRUCT.unpack_from(
                    header, 4 + 124
                )
            except struct.error:
                dxgi_format = None
            if dxgi_format is not None:
                dxgi_format_value = int(dxgi_format)
                name = DXGI_FORMAT_NAMES.get(dxgi_format)
                if name:
                    compression = _friendly_dxgi_compression_name(name)
                else:
                    compression = f"DXGI_FORMAT_{dxgi_format}"
            else:
                compression = "DX10"
            fourcc_name = "DX10"
        else:
            try:
                fourcc_name = fourcc.decode("ascii").strip() or None
            except UnicodeDecodeError:
                fourcc_name = None
            compression = fourcc_name or compression
    elif pf_flags & DDPF_RGB:
        bits = rgb_bit_count or 0
        if pf_flags & DDPF_ALPHAPIXELS:
            compression = f"Uncompressed {bits}-bit RGBA"
        else:
            compression = f"Uncompressed {bits}-bit RGB"
    elif pf_flags & DDPF_LUMINANCE:
        bits = rgb_bit_count or 0
        if pf_flags & DDPF_ALPHAPIXELS:
            compression = f"Uncompressed {bits}-bit luminance-alpha"
        else:
            compression = f"Uncompressed {bits}-bit luminance"
    elif pf_flags & DDPF_ALPHA:
        bits = rgb_bit_count or 0
        compression = f"Uncompressed {bits}-bit alpha"

    channel_masks = {
        "R": r_mask if r_mask else 0,
        "G": g_mask if g_mask else 0,
        "B": b_mask if b_mask else 0,
        "A": a_mask if a_mask else 0,
    }

    info: Dict[str, object] = {
        "compression": compression,
        "channel_masks": channel_masks,
        "pixel_format_flags": pf_flags,
        "bits_per_pixel": rgb_bit_count,
    }

    if fourcc_name:
        info["fourcc"] = fourcc_name
    if dxgi_format_value is not None:
        info["dxgi_format"] = dxgi_format_value

    info["mipmaps"] = int(mipmaps) if mipmaps else 0

    return info

def _decode_alpha_only_dds(
    payload: bytes,
) -> Tuple["Image.Image" | None, "Image.Image" | None]:
    """Return fallback previews for alpha-only or luminance-only DDS textures."""

    if not payload.startswith(b"DDS "):
        return None, None

    if len(payload) < 128:
        return None, None

    header = memoryview(payload)
    try:
        (
            _,
            header_size,
            _flags,
            height,
            width,
            pitch_or_linear,
            _depth,
            _mipmaps,
        ) = struct.unpack_from(
            "<4s7I", header, 0
        )
    except struct.error:
        return None, None

    if header_size != 124:
        return None, None

    try:
        (
            pf_size,
            pf_flags,
            _fourcc,
            rgb_bit_count,
            r_mask,
            g_mask,
            b_mask,
            a_mask,
        ) = DDS_PIXELFORMAT_STRUCT.unpack_from(header, 4 + 72)
    except struct.error:
        return None, None

    if pf_size != DDS_PIXELFORMAT_STRUCT.size:
        return None, None

    is_alpha_only = bool(pf_flags & DDPF_ALPHA) and not (
        pf_flags & (DDPF_RGB | DDPF_FOURCC)
    )
    is_luminance_only = bool(pf_flags & DDPF_LUMINANCE) and not (
        pf_flags & (DDPF_RGB | DDPF_FOURCC)
    )

    if not (is_alpha_only or is_luminance_only):
        return None, None

    bits = rgb_bit_count or 0
    if bits <= 0:
        bits = 8
    if bits not in (8,):
        return None, None

    treat_alpha_as_grayscale = False
    if is_alpha_only:
        # The DDS alpha-only format normally uses the alpha mask for the single
        # 8-bit channel.  Some textures are authored with the alpha flag set but
        # without a usable alpha mask, effectively behaving like luminance-only
        # data.  Remember that we should display those textures using an opaque
        # grayscale preview once the pixel data has been decoded.
        if a_mask == 0:
            treat_alpha_as_grayscale = True
    elif is_luminance_only:
        if r_mask == 0:
            return None, None

    bytes_per_pixel = max(1, bits // 8)
    if bytes_per_pixel <= 0:
        return None, None

    row_pitch = width * bytes_per_pixel
    if pitch_or_linear and height:
        if pitch_or_linear % height == 0:
            candidate = pitch_or_linear // height
            if candidate >= width * bytes_per_pixel:
                row_pitch = candidate
        elif pitch_or_linear >= width * bytes_per_pixel and pitch_or_linear <= len(payload):
            row_pitch = pitch_or_linear

    top_level_size = width * height * bytes_per_pixel
    pixel_data_offset = 128
    available_bytes = len(payload) - pixel_data_offset
    required_bytes = row_pitch * height
    if available_bytes < required_bytes:
        if available_bytes < top_level_size:
            return None, None
        row_pitch = width * bytes_per_pixel
        required_bytes = top_level_size

    channel_slice = payload[
        pixel_data_offset : pixel_data_offset + min(required_bytes, available_bytes)
    ]
    try:
        if row_pitch != width * bytes_per_pixel:
            channel_image = Image.frombuffer(
                "L",
                (width, height),
                bytes(channel_slice[: required_bytes]),
                "raw",
                "L",
                row_pitch,
                1,
            )
        else:
            channel_image = Image.frombytes(
                "L",
                (width, height),
                channel_slice[:top_level_size],
            )
    except ValueError:
        return None, None

    if is_alpha_only:
        if treat_alpha_as_grayscale:
            opaque_alpha = Image.new("L", (width, height), 255)
            grayscale_preview = Image.merge(
                "RGBA", (channel_image, channel_image, channel_image, opaque_alpha)
            )
            return grayscale_preview, grayscale_preview

        rgba_image = Image.new("RGBA", (width, height), (255, 255, 255, 0))
        rgba_image.putalpha(channel_image)
        opaque_alpha = Image.new("L", (width, height), 255)
        grayscale_preview = Image.merge(
            "RGBA", (channel_image, channel_image, channel_image, opaque_alpha)
        )
        return rgba_image, grayscale_preview

    opaque_alpha = Image.new("L", (width, height), 255)
    grayscale_rgba = Image.merge(
        "RGBA", (channel_image, channel_image, channel_image, opaque_alpha)
    )
    return grayscale_rgba, grayscale_rgba

def _decode_uncompressed_color_dds(
    payload: bytes,
) -> Tuple["Image.Image" | None, "Image.Image" | None]:
    """Decode uncompressed RGB/RGBA DDS payloads without relying on Pillow."""

    if not payload.startswith(b"DDS "):
        return None, None

    if len(payload) < 128:
        return None, None

    header = memoryview(payload)
    try:
        (
            _magic,
            header_size,
            _flags,
            height,
            width,
            pitch_or_linear,
            _depth,
            _mipmaps,
        ) = struct.unpack_from("<4s7I", header, 0)
    except struct.error:
        return None, None

    if header_size != 124:
        return None, None

    try:
        (
            pf_size,
            pf_flags,
            _fourcc,
            rgb_bit_count,
            r_mask,
            g_mask,
            b_mask,
            a_mask,
        ) = DDS_PIXELFORMAT_STRUCT.unpack_from(header, 4 + 72)
    except struct.error:
        return None, None

    if pf_size != DDS_PIXELFORMAT_STRUCT.size:
        return None, None

    if not (pf_flags & DDPF_RGB):
        return None, None

    if pf_flags & (DDPF_FOURCC | DDPF_LUMINANCE):
        return None, None

    if width <= 0 or height <= 0:
        return None, None

    bits = rgb_bit_count or 0
    if bits <= 0 or bits > 32:
        return None, None

    bytes_per_pixel = (bits + 7) // 8
    if bytes_per_pixel <= 0 or bytes_per_pixel > 4:
        return None, None

    row_pitch = width * bytes_per_pixel
    if pitch_or_linear and pitch_or_linear >= row_pitch:
        row_pitch = pitch_or_linear

    top_level_size = width * height * bytes_per_pixel
    data_offset = 128
    available_bytes = len(payload) - data_offset
    required_bytes = row_pitch * height
    if available_bytes < required_bytes:
        if available_bytes < top_level_size:
            return None, None
        row_pitch = width * bytes_per_pixel
        required_bytes = top_level_size

    pixel_bytes = payload[data_offset : data_offset + min(required_bytes, available_bytes)]

    def _expand_channel(value: int, mask: int) -> int:
        if mask == 0:
            return 0
        lowest_bit = (mask & -mask).bit_length() - 1
        if lowest_bit < 0:
            return 0
        span_mask = mask >> lowest_bit
        bit_count = span_mask.bit_length()
        if bit_count <= 0:
            return 0
        component = (value & mask) >> lowest_bit
        max_source = (1 << bit_count) - 1
        if max_source <= 0:
            return 0
        return (component * 255 + max_source // 2) // max_source

    output = bytearray(width * height * 4)
    for y in range(height):
        row_start = y * row_pitch
        for x in range(width):
            pixel_start = row_start + x * bytes_per_pixel
            pixel_end = pixel_start + bytes_per_pixel
            if pixel_end > len(pixel_bytes):
                return None, None
            pixel_value = int.from_bytes(
                pixel_bytes[pixel_start:pixel_end], "little", signed=False
            )

            r = _expand_channel(pixel_value, r_mask)
            g = _expand_channel(pixel_value, g_mask)
            b = _expand_channel(pixel_value, b_mask)
            a = 255
            if pf_flags & DDPF_ALPHAPIXELS:
                if a_mask:
                    a = _expand_channel(pixel_value, a_mask)
                else:
                    a = 255

            dest = (y * width + x) * 4
            output[dest : dest + 4] = bytes((r, g, b, a))

    try:
        image = Image.frombytes("RGBA", (width, height), bytes(output))
    except ValueError:
        return None, None

    return image, image

def _decode_rgb565(color: int) -> Tuple[int, int, int]:
    """Return the 8-bit RGB tuple represented by a RGB565 colour value."""

    r = (color >> 11) & 0x1F
    g = (color >> 5) & 0x3F
    b = color & 0x1F

    r = (r << 3) | (r >> 2)
    g = (g << 2) | (g >> 4)
    b = (b << 3) | (b >> 2)

    return r, g, b

def _build_bc1_palette(color0: int, color1: int) -> List[Tuple[int, int, int, int]]:
    """Return the RGBA palette used for BC1/DXT colour decoding."""

    c0 = _decode_rgb565(color0)
    c1 = _decode_rgb565(color1)

    if color0 > color1:
        palette = [
            (c0[0], c0[1], c0[2], 255),
            (c1[0], c1[1], c1[2], 255),
            (
                (2 * c0[0] + c1[0]) // 3,
                (2 * c0[1] + c1[1]) // 3,
                (2 * c0[2] + c1[2]) // 3,
                255,
            ),
            (
                (c0[0] + 2 * c1[0]) // 3,
                (c0[1] + 2 * c1[1]) // 3,
                (c0[2] + 2 * c1[2]) // 3,
                255,
            ),
        ]
    else:
        palette = [
            (c0[0], c0[1], c0[2], 255),
            (c1[0], c1[1], c1[2], 255),
            (
                (c0[0] + c1[0]) // 2,
                (c0[1] + c1[1]) // 2,
                (c0[2] + c1[2]) // 2,
                255,
            ),
            (0, 0, 0, 0),
        ]

    return palette

def _decode_bc1_block(block: bytes) -> List[Tuple[int, int, int, int]]:
    """Decode a single 8-byte BC1 block into RGBA tuples."""

    try:
        color0, color1, indices = struct.unpack_from("<HHI", block, 0)
    except struct.error:
        return []

    palette = _build_bc1_palette(color0, color1)
    pixels: List[Tuple[int, int, int, int]] = []
    for pixel in range(16):
        index = (indices >> (2 * pixel)) & 0x3
        pixels.append(palette[index])

    return pixels

def _decode_bc2_block(block: bytes) -> List[Tuple[int, int, int, int]]:
    """Decode a BC2/DXT3 block into RGBA tuples."""

    if len(block) < 16:
        return []

    alpha_bits = int.from_bytes(block[:8], "little", signed=False)
    colour_block = block[8:16]
    colours = _decode_bc1_block(colour_block)
    if len(colours) != 16:
        return []

    pixels: List[Tuple[int, int, int, int]] = []
    for pixel in range(16):
        alpha = ((alpha_bits >> (4 * pixel)) & 0xF) * 17
        r, g, b, _ = colours[pixel]
        pixels.append((r, g, b, alpha))

    return pixels

def _decode_signed_byte(value: int) -> int:
    """Return the signed interpretation of an 8-bit value, clamping -128."""

    signed = struct.unpack("<b", struct.pack("<B", value))[0]
    if signed == -128:
        signed = -127
    return signed

def _decode_bc4_block(
    block: bytes, *, signed: bool
) -> Tuple[List[int], List[float]]:
    """Decode a BC4 channel block returning byte values and normalised floats."""

    if len(block) < 8:
        return [], []

    a0_raw = block[0]
    a1_raw = block[1]
    if signed:
        a0 = _decode_signed_byte(a0_raw)
        a1 = _decode_signed_byte(a1_raw)
    else:
        a0 = int(a0_raw)
        a1 = int(a1_raw)

    palette: List[int] = [a0, a1]
    if signed:
        if a0 > a1:
            for step in range(1, 7):
                value = ((7 - step) * a0 + step * a1) / 7.0
                palette.append(int(round(value)))
        else:
            for step in range(1, 5):
                value = ((5 - step) * a0 + step * a1) / 5.0
                palette.append(int(round(value)))
            palette.append(-127)
            palette.append(127)
    else:
        if a0 > a1:
            for step in range(1, 7):
                value = ((7 - step) * a0 + step * a1) // 7
                palette.append(int(value))
        else:
            for step in range(1, 5):
                value = ((5 - step) * a0 + step * a1) // 5
                palette.append(int(value))
            palette.append(0)
            palette.append(255)

    selector_bits = int.from_bytes(block[2:8], "little", signed=False)
    byte_values: List[int] = []
    normalised: List[float] = []
    for pixel in range(16):
        index = (selector_bits >> (3 * pixel)) & 0x7
        sample = palette[index]
        if signed:
            sample = max(-127, min(127, sample))
            normal = sample / 127.0 if sample else 0.0
            byte_value = int(round((normal + 1.0) * 0.5 * 255))
        else:
            sample = max(0, min(255, sample))
            normal = sample / 255.0 * 2.0 - 1.0
            byte_value = sample
        byte_values.append(max(0, min(255, byte_value)))
        normalised.append(max(-1.0, min(1.0, normal)))

    return byte_values, normalised

def _decode_bc3_block(block: bytes) -> List[Tuple[int, int, int, int]]:
    """Decode a BC3/DXT5 block into RGBA tuples."""

    if len(block) < 16:
        return []

    alpha_bytes, _ = _decode_bc4_block(block[:8], signed=False)
    if len(alpha_bytes) != 16:
        return []

    colours = _decode_bc1_block(block[8:16])
    if len(colours) != 16:
        return []

    pixels: List[Tuple[int, int, int, int]] = []
    for pixel in range(16):
        r, g, b, _ = colours[pixel]
        pixels.append((r, g, b, alpha_bytes[pixel]))

    return pixels

def _decode_bc4_to_rgba(block: bytes, *, signed: bool) -> List[Tuple[int, int, int, int]]:
    """Decode a BC4 block producing grayscale RGBA pixels."""

    channel_bytes, _ = _decode_bc4_block(block, signed=signed)
    if len(channel_bytes) != 16:
        return []

    pixels: List[Tuple[int, int, int, int]] = []
    for value in channel_bytes:
        pixels.append((value, value, value, 255))

    return pixels

def _decode_bc5_block(block: bytes, *, signed: bool) -> List[Tuple[int, int, int, int]]:
    """Decode a BC5 block (two BC4 channels) into RGBA tuples."""

    if len(block) < 16:
        return []

    red_bytes, red_normals = _decode_bc4_block(block[:8], signed=signed)
    green_bytes, green_normals = _decode_bc4_block(block[8:16], signed=signed)
    if len(red_bytes) != 16 or len(green_bytes) != 16:
        return []

    pixels: List[Tuple[int, int, int, int]] = []
    for idx in range(16):
        r = red_bytes[idx]
        g = green_bytes[idx]
        nx = red_normals[idx]
        ny = green_normals[idx]
        nz_sq = max(0.0, 1.0 - nx * nx - ny * ny)
        nz = math.sqrt(nz_sq)
        b = int(round((nz * 0.5 + 0.5) * 255))
        pixels.append((r, g, b, 255))

    return pixels

def _decode_block_compressed_dds(
    payload: bytes,
) -> Tuple["Image.Image" | None, "Image.Image" | None]:
    """Decode BC-compressed DDS payloads into Pillow images for preview."""

    if not payload.startswith(b"DDS "):
        return None, None

    if len(payload) < 128:
        return None, None

    header = memoryview(payload)
    try:
        (
            _magic,
            header_size,
            _flags,
            height,
            width,
            _pitch_or_linear,
            _depth,
            _mipmaps,
        ) = struct.unpack_from("<4s7I", header, 0)
    except struct.error:
        return None, None

    if header_size != 124:
        return None, None

    try:
        (
            pf_size,
            pf_flags,
            fourcc,
            _rgb_bit_count,
            _r_mask,
            _g_mask,
            _b_mask,
            _a_mask,
        ) = DDS_PIXELFORMAT_STRUCT.unpack_from(header, 4 + 72)
    except struct.error:
        return None, None

    if pf_size != DDS_PIXELFORMAT_STRUCT.size:
        return None, None

    if not (pf_flags & DDPF_FOURCC):
        return None, None

    data_offset = 128
    decoder_info: Tuple[str, Dict[str, object]] | None = None

    if fourcc == b"DX10":
        if len(payload) < data_offset + DDS_DX10_STRUCT.size:
            return None, None
        try:
            dxgi_format, _, _, _, _ = DDS_DX10_STRUCT.unpack_from(header, data_offset)
        except struct.error:
            return None, None
        decoder_info = DXGI_BLOCK_DECODERS.get(dxgi_format)
        data_offset += DDS_DX10_STRUCT.size
    else:
        decoder_info = FOURCC_BLOCK_DECODERS.get(fourcc)

    if not decoder_info:
        return None, None

    decoder_kind, decoder_args = decoder_info
    block_size = BLOCK_SIZES.get(decoder_kind)
    if block_size is None:
        return None, None

    if width <= 0 or height <= 0:
        return None, None

    blocks_x = (width + 3) // 4
    blocks_y = (height + 3) // 4
    required = blocks_x * blocks_y * block_size
    if len(payload) < data_offset + required:
        return None, None

    output = bytearray(width * height * 4)
    block_cursor = data_offset
    decoder: Callable[[bytes], List[Tuple[int, int, int, int]]]

    def _select_decoder(kind: str) -> Callable[[bytes], List[Tuple[int, int, int, int]]]:
        if kind == "BC1":
            return _decode_bc1_block
        if kind == "BC2":
            return _decode_bc2_block
        if kind == "BC3":
            return _decode_bc3_block
        if kind == "BC4":
            return lambda data: _decode_bc4_to_rgba(data, signed=bool(decoder_args.get("signed")))
        if kind == "BC5":
            return lambda data: _decode_bc5_block(data, signed=bool(decoder_args.get("signed")))
        return lambda _: []

    decoder = _select_decoder(decoder_kind)

    for block_y in range(blocks_y):
        for block_x in range(blocks_x):
            block = payload[block_cursor : block_cursor + block_size]
            block_cursor += block_size
            colours = decoder(block)
            if len(colours) != 16:
                return None, None
            for pixel in range(16):
                px = block_x * 4 + (pixel & 0x3)
                py = block_y * 4 + (pixel >> 2)
                if px >= width or py >= height:
                    continue
                dest = (py * width + px) * 4
                output[dest : dest + 4] = bytes(colours[pixel])

    try:
        image = Image.frombytes("RGBA", (width, height), bytes(output))
    except ValueError:
        return None, None

    return image, None

