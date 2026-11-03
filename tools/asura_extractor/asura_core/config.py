"""Shared configuration constants for the Asura core package."""

from __future__ import annotations

import os
import struct
from typing import Dict, Tuple

ASURA_MAGIC = b"Asura   "
ASURA_ZLB_MAGIC = b"AsuraZlb"
ASURA_ZBB_MAGIC = b"AsuraZbb"

CHUNK_HEADER = struct.Struct("<4sIII")
RSFL_ENTRY_STRUCT = struct.Struct("<III")
RSFL_CHUNK_HEADER = struct.Struct("<5I")

MEMORY_MAP_THRESHOLD = int(os.environ.get("RS_MEMORY_MAP_THRESHOLD", 256 * 1024 * 1024))

SUPPORTED_TEXTURE_EXTENSIONS = {
    ".bmp",
    ".dds",
    ".gif",
    ".jpg",
    ".jpeg",
    ".png",
    ".tga",
    ".tif",
    ".tiff",
}

SUPPORTED_ARCHIVE_SUFFIXES: Tuple[str, ...] = (
    ".asr",
    ".gui",
    ".pc",
    ".en",
    ".fr",
    ".de",
    ".sp",
    ".it",
    ".ge",
    ".pl",
)

SUPPORTED_AUDIO_EXTENSIONS = {
    ".wav",
}

CATEGORY_LABELS = {
    "textures": "Textures",
    "audio": "Audio",
    "other": "Other",
}

MAGIC_TO_IMAGE_FORMAT = (
    (b"DDS ", "DDS", ".dds"),
    (b"\x89PNG\r\n\x1a\n", "PNG", ".png"),
    (b"BM", "BMP", ".bmp"),
    (b"GIF8", "GIF", ".gif"),
    (b"\xff\xd8\xff", "JPEG", ".jpg"),
    (b"II*\x00", "TIFF", ".tif"),
    (b"MM\x00*", "TIFF", ".tif"),
)

DDS_PIXELFORMAT_STRUCT = struct.Struct("<II4sI4I")
DDS_DX10_STRUCT = struct.Struct("<5I")

DDPF_ALPHAPIXELS = 0x00000001
DDPF_ALPHA = 0x00000002
DDPF_FOURCC = 0x00000004
DDPF_RGB = 0x00000040
DDPF_LUMINANCE = 0x00020000

DXGI_FORMAT_NAMES = {
    70: "BC1_TYPELESS",
    71: "BC1_UNORM",
    72: "BC1_UNORM_SRGB",
    73: "BC2_TYPELESS",
    74: "BC2_UNORM",
    75: "BC2_UNORM_SRGB",
    76: "BC3_TYPELESS",
    77: "BC3_UNORM",
    78: "BC3_UNORM_SRGB",
    79: "BC4_TYPELESS",
    80: "BC4_UNORM",
    81: "BC4_SNORM",
    82: "BC5_TYPELESS",
    83: "BC5_UNORM",
    84: "BC5_SNORM",
    94: "BC6H_TYPELESS",
    95: "BC6H_UF16",
    96: "BC6H_SF16",
    97: "BC7_TYPELESS",
    98: "BC7_UNORM",
    99: "BC7_UNORM_SRGB",
}

FOURCC_BLOCK_DECODERS: Dict[bytes, Tuple[str, Dict[str, object]]] = {
    b"DXT1": ("BC1", {}),
    b"DXT2": ("BC2", {}),
    b"DXT3": ("BC2", {}),
    b"DXT4": ("BC3", {}),
    b"DXT5": ("BC3", {}),
    b"BC4U": ("BC4", {"signed": False}),
    b"BC4S": ("BC4", {"signed": True}),
    b"ATI1": ("BC4", {"signed": False}),
    b"BC5U": ("BC5", {"signed": False}),
    b"BC5S": ("BC5", {"signed": True}),
    b"ATI2": ("BC5", {"signed": False}),
}

DXGI_BLOCK_DECODERS: Dict[int, Tuple[str, Dict[str, object]]] = {
    70: ("BC1", {}),
    71: ("BC1", {}),
    72: ("BC1", {}),
    73: ("BC2", {}),
    74: ("BC2", {}),
    75: ("BC2", {}),
    76: ("BC3", {}),
    77: ("BC3", {}),
    78: ("BC3", {}),
    79: ("BC4", {"signed": False}),
    80: ("BC4", {"signed": False}),
    81: ("BC4", {"signed": True}),
    82: ("BC5", {"signed": False}),
    83: ("BC5", {"signed": False}),
    84: ("BC5", {"signed": True}),
    94: ("BC6", {}),
    95: ("BC6", {}),
    96: ("BC6", {}),
    97: ("BC7", {}),
    98: ("BC7", {}),
    99: ("BC7", {}),
}

BLOCK_SIZES = {
    "BC1": 8,
    "BC2": 16,
    "BC3": 16,
    "BC4": 8,
    "BC5": 16,
    "BC6": 16,
    "BC7": 16,
}

__all__ = [
    "ASURA_MAGIC",
    "ASURA_ZLB_MAGIC",
    "ASURA_ZBB_MAGIC",
    "CHUNK_HEADER",
    "RSFL_ENTRY_STRUCT",
    "RSFL_CHUNK_HEADER",
    "MEMORY_MAP_THRESHOLD",
    "SUPPORTED_TEXTURE_EXTENSIONS",
    "SUPPORTED_ARCHIVE_SUFFIXES",
    "SUPPORTED_AUDIO_EXTENSIONS",
    "CATEGORY_LABELS",
    "MAGIC_TO_IMAGE_FORMAT",
    "DDS_PIXELFORMAT_STRUCT",
    "DDS_DX10_STRUCT",
    "DDPF_ALPHAPIXELS",
    "DDPF_ALPHA",
    "DDPF_FOURCC",
    "DDPF_RGB",
    "DDPF_LUMINANCE",
    "DXGI_FORMAT_NAMES",
    "FOURCC_BLOCK_DECODERS",
    "DXGI_BLOCK_DECODERS",
    "BLOCK_SIZES",
]
