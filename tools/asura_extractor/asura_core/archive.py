"""Binary archive handling helpers for the Asura core package."""

from __future__ import annotations

import json
import mmap
import os
import shutil
import struct
import threading
import zlib
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Sequence, Tuple

from .config import (
    ASURA_MAGIC,
    ASURA_ZLB_MAGIC,
    ASURA_ZBB_MAGIC,
    CHUNK_HEADER,
    RSFL_ENTRY_STRUCT,
    RSFL_CHUNK_HEADER,
    MEMORY_MAP_THRESHOLD,
)
from .export import resolve_export_relative_path

class RSFLParsingError(RuntimeError):
    """Raised when the archive layout does not match expectations."""


def normalize_relative_path(name: str) -> str:
    """Return a normalised relative path using forward slashes."""

    normalised = name.replace("\\", "/")
    return normalised.lstrip("/")

def _unwrap_asura_container(
    data: bytes | mmap.mmap,
) -> Tuple[bytes | mmap.mmap, Dict[str, object]]:
    """Return the raw Asura archive payload and compression metadata."""

    if data[: len(ASURA_MAGIC)] == ASURA_MAGIC:
        return data, {"kind": "raw"}

    if data[: len(ASURA_ZLB_MAGIC)] == ASURA_ZLB_MAGIC:
        if len(data) < 20:
            raise RSFLParsingError("truncated AsuraZlb header")

        unknown = struct.unpack_from("<I", data, 8)[0]
        compressed_size = struct.unpack_from("<I", data, 12)[0]
        expected_size = struct.unpack_from("<I", data, 16)[0]
        payload = data[20:]
        if compressed_size and compressed_size <= len(payload):
            payload = payload[:compressed_size]
        try:
            uncompressed = zlib.decompress(payload)
        except zlib.error as exc:  # pragma: no cover - depends on archive contents
            raise RSFLParsingError(f"failed to decompress AsuraZlb archive: {exc}") from exc
        if expected_size and len(uncompressed) != expected_size:
            expected_size = len(uncompressed)
        return uncompressed, {
            "kind": "zlb",
            "unknown": unknown,
            "expected_size": expected_size,
        }

    if data[: len(ASURA_ZBB_MAGIC)] == ASURA_ZBB_MAGIC:
        if len(data) < 16:
            raise RSFLParsingError("truncated AsuraZbb header")

        total_compressed = struct.unpack_from("<I", data, 8)[0]
        total_size = struct.unpack_from("<I", data, 12)[0]
        cursor = 16
        output = bytearray()
        chunk_sizes: List[int] = []

        while cursor + 8 <= len(data):
            chunk_compressed = struct.unpack_from("<I", data, cursor)[0]
            chunk_size = struct.unpack_from("<I", data, cursor + 4)[0]
            cursor += 8
            if chunk_compressed == 0:
                break
            chunk_payload = data[cursor : cursor + chunk_compressed]
            if len(chunk_payload) != chunk_compressed:
                raise RSFLParsingError("truncated AsuraZbb chunk payload")
            cursor += chunk_compressed
            try:
                chunk_data = zlib.decompress(chunk_payload)
            except zlib.error as exc:  # pragma: no cover - depends on archive contents
                raise RSFLParsingError(f"failed to decompress AsuraZbb chunk: {exc}") from exc
            if chunk_size and len(chunk_data) != chunk_size:
                raise RSFLParsingError(
                    "decompressed chunk size does not match the value stored in the header"
                )
            output.extend(chunk_data)
            chunk_sizes.append(len(chunk_data))
            if total_size and len(output) >= total_size:
                break

        if total_size and len(output) != total_size:
            total_size = len(output)

        return bytes(output), {
            "kind": "zbb",
            "chunk_sizes": chunk_sizes,
            "total_size": total_size,
        }

    raise RSFLParsingError(
        "unsupported Asura container wrapper; try running the QuickBMS script first"
    )

def _wrap_asura_container(data: bytes, wrapper: Dict[str, object]) -> bytes:
    """Reapply the original Asura wrapper (if any) after patching."""

    kind = wrapper.get("kind") if wrapper else "raw"
    if kind == "raw":
        return data

    if kind == "zlb":
        compressed = zlib.compress(data)
        unknown = int(wrapper.get("unknown", 0))
        header = struct.pack(
            "<8sIII", ASURA_ZLB_MAGIC, unknown, len(compressed), len(data)
        )
        return header + compressed

    if kind == "zbb":
        chunk_sizes = list(wrapper.get("chunk_sizes") or [])
        if not chunk_sizes:
            chunk_sizes = [len(data)]
        payload = bytearray()
        total_compressed = 0
        cursor = 0
        size_index = 0
        default_size = chunk_sizes[-1]
        while cursor < len(data):
            size_hint = chunk_sizes[size_index] if size_index < len(chunk_sizes) else default_size
            if size_hint <= 0:
                size_hint = default_size or len(data)
            end = min(len(data), cursor + size_hint)
            chunk = data[cursor:end]
            compressed_chunk = zlib.compress(chunk)
            payload.extend(struct.pack("<II", len(compressed_chunk), len(chunk)))
            payload.extend(compressed_chunk)
            total_compressed += len(compressed_chunk)
            cursor = end
            size_index += 1

        header = struct.pack("<8sII", ASURA_ZBB_MAGIC, total_compressed, len(data))
        return header + payload

    raise RSFLParsingError(f"unsupported wrapper kind: {kind}")

def _write_bytes_in_chunks(
    path: Path,
    data: bytes,
    *,
    chunk_size: int = 2 * 1024 * 1024,
    cancel_event: threading.Event | None = None,
    progress_callback: Callable[[int], None] | None = None,
) -> Tuple[str, Exception | None]:
    """Write *data* to *path* in chunks while reporting progress.

    Returns a tuple ``(status, error)`` where *status* is ``"success"``,
    ``"cancelled"``, or ``"error"``.  When an error occurs, *error* contains the
    exception raised while attempting to write the file.
    """

    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")

    total = len(data)
    written = 0
    status = "success"

    try:
        with path.open("wb") as handle:
            if total == 0 and progress_callback is not None:
                progress_callback(0)

            for start in range(0, total, chunk_size):
                if cancel_event is not None and cancel_event.is_set():
                    status = "cancelled"
                    break

                chunk = data[start : start + chunk_size]
                handle.write(chunk)
                written += len(chunk)
                if progress_callback is not None:
                    progress_callback(written)

        if status != "success":
            try:
                path.unlink()
            except FileNotFoundError:
                pass

        return status, None

    except Exception as exc:  # pragma: no cover - depends on filesystem errors.
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return "error", exc

def _read_padded_string(data: bytes, offset: int) -> Tuple[str, int]:
    """Return a null-terminated string padded to 4-byte boundaries.

    The function returns the decoded string and the total amount of bytes
    consumed (including the padding).
    """

    cursor = offset
    chunks: List[int] = []
    while cursor < len(data):
        block = data[cursor : cursor + 4]
        chunks.append(block)
        cursor += 4
        if b"\x00" in block:
            break
    else:
        raise RSFLParsingError("unterminated padded string inside RSFL table")

    raw = b"".join(chunks)
    string_bytes = raw.split(b"\x00", 1)[0]
    try:
        text = string_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = string_bytes.decode("latin-1")
    return text, cursor - offset

def _scan_for_rsfl(data: bytes) -> Dict[str, object]:
    """Locate RS managed chunks and describe their layout."""

    if not data.startswith(ASURA_MAGIC):
        raise RSFLParsingError("file is not an Asura archive")

    cursor = len(ASURA_MAGIC)
    rscf_chunks: List[Dict[str, object]] = []

    while cursor + CHUNK_HEADER.size <= len(data):
        magic, size, type1, type2 = CHUNK_HEADER.unpack_from(data, cursor)

        if magic == b"\x00\x00\x00\x00" and size == 0:
            break

        if size == 0:
            raise RSFLParsingError("encountered zero-sized chunk while scanning")

        if magic == b"LFSR":
            header_start = cursor
            header_end = cursor + 16
            rsfl_magic, rsfl_size, rsfl_type1, rsfl_type2, count = RSFL_CHUNK_HEADER.unpack_from(
                data, header_end
            )
            if rsfl_magic != 0x5246534C:  # 'LFSR'
                raise RSFLParsingError("corrupted RSFL inner header")
            return {
                "layout": "rsfl",
                "chunks": [
                    {
                        "offset": header_start,
                        "chunk_size": size,
                        "type1": type1,
                        "type2": type2,
                        "rsfl": {
                            "inner_size": rsfl_size,
                            "type1": rsfl_type1,
                            "type2": rsfl_type2,
                            "entry_count": count,
                        },
                    }
                ],
            }

        if magic == b"RSCF":
            header_offset = cursor + 16
            if header_offset + 12 > len(data):
                raise RSFLParsingError("truncated RSCF header")
            version, data_offset, data_span = struct.unpack_from("<III", data, header_offset)
            rscf_chunks.append(
                {
                    "offset": cursor,
                    "chunk_size": size,
                    "type1": type1,
                    "type2": type2,
                    "rscf": {
                        "version": version,
                        "data_offset": data_offset,
                        "data_span": data_span,
                    },
                }
            )

        cursor += size

    if rscf_chunks:
        return {"layout": "rscf", "chunks": rscf_chunks}

    raise RSFLParsingError("unable to locate RS controlled chunks")

def _parse_rsfl_entries(
    data: bytes, rsfl_offset: int, rsfl_chunk_size: int, entry_count: int
) -> Tuple[List[Dict[str, int]], int]:
    """Parse RSFL entries and return both the entry list and the end offset."""

    entries: List[Dict[str, int]] = []
    table_offset = rsfl_offset + 16 + RSFL_CHUNK_HEADER.size
    cursor = table_offset
    for _ in range(entry_count):
        name, consumed = _read_padded_string(data, cursor)
        cursor += consumed
        entry_struct_offset = cursor
        raw_offset, size, unk = RSFL_ENTRY_STRUCT.unpack_from(data, cursor)
        cursor += RSFL_ENTRY_STRUCT.size
        entries.append(
            {
                "name": name,
                "raw_offset": raw_offset,
                "size": size,
                "unk": unk,
                "table_offset": entry_struct_offset,
            }
        )
    return entries, cursor

def _parse_rscf_entries(data: bytes, chunk_info: Dict[str, object]) -> List[Dict[str, object]]:
    """Parse a single RSCF chunk and return its entry description."""

    chunk_offset = int(chunk_info["offset"])
    chunk_size = int(chunk_info["chunk_size"])
    header = dict(chunk_info.get("rscf") or {})

    header_offset = chunk_offset + 16
    if header_offset + 12 > len(data):
        raise RSFLParsingError("truncated RSCF chunk header")

    version = int(header.get("version", 0))
    data_offset = int(header.get("data_offset", 0))
    data_span = int(header.get("data_span", 0))

    string_offset = header_offset + 12
    name, consumed = _read_padded_string(data, string_offset)
    string_end = string_offset + consumed

    chunk_end = chunk_offset + chunk_size

    candidate_offsets: List[int] = []

    def _register_candidate(value: int) -> None:
        if value not in candidate_offsets:
            candidate_offsets.append(value)

    _register_candidate(string_end + data_offset)

    masked_offset = data_offset & 0x00FFFFFF
    if masked_offset != data_offset:
        _register_candidate(string_end + masked_offset)

    _register_candidate(chunk_end - data_span)

    payload_offset = None
    payload_end = None
    for candidate in candidate_offsets:
        if candidate < string_end:
            continue
        end = candidate + data_span
        if end > chunk_end or end > len(data):
            continue
        payload_offset = candidate
        payload_end = end
        break

    if payload_offset is None or payload_end is None:
        raise RSFLParsingError("RSCF payload exceeds chunk bounds")

    relative_name = normalize_relative_path(name)

    entry = {
        "layout": "rscf",
        "name": name,
        "relative_path": str(relative_name),
        "offset": payload_offset,
        "size": data_span,
        "raw_offset": None,
        "table_offset": None,
        "offset_anchor": payload_offset,
        "anchor_kind": "absolute",
        "chunk_offset": chunk_offset,
        "chunk_size": chunk_size,
        "header_offset": header_offset,
        "header_version": version,
        "header_data_offset": data_offset,
        "header_data_span": data_span,
    }

    return [entry]

def _resolve_entry_offset(raw_offset: int, size: int, data_length: int, rsfl_offset: int, rsfl_chunk_size: int) -> int:
    """Translate the offset stored in the RSFL table into an absolute position."""

    offset_relative = raw_offset + rsfl_chunk_size
    if offset_relative + size > data_length:
        offset_relative = raw_offset + rsfl_offset
    if offset_relative + size > data_length:
        raise RSFLParsingError("entry points outside of archive bounds")
    return offset_relative

def _normalise_entries(
    entries: List[Dict[str, int]],
    data: bytes,
    rsfl_offset: int,
    rsfl_chunk_size: int,
) -> List[Dict[str, object]]:
    normalised: List[Dict[str, object]] = []
    for entry in entries:
        absolute_offset = _resolve_entry_offset(
            entry["raw_offset"], entry["size"], len(data), rsfl_offset, rsfl_chunk_size
        )
        relative_name = normalize_relative_path(entry["name"])
        chunk_end = rsfl_offset + rsfl_chunk_size
        offset_anchor = absolute_offset - entry["raw_offset"]
        normalised.append(
            {
                "name": entry["name"],
                "relative_path": str(relative_name),
                "offset": absolute_offset,
                "size": entry["size"],
                "unk": entry["unk"],
                "raw_offset": entry["raw_offset"],
                "table_offset": entry["table_offset"],
                "offset_anchor": offset_anchor,
                "anchor_kind": (
                    "chunk_end"
                    if offset_anchor == chunk_end
                    else "chunk_start"
                    if offset_anchor == rsfl_offset
                    else "absolute"
                ),
                "layout": "rsfl",
            }
        )
    return normalised

def _read_archive_bytes(path: Path) -> bytes | mmap.mmap:
    """Return the bytes for ``path`` using a memory map when appropriate."""

    try:
        size = path.stat().st_size
    except OSError as exc:  # pragma: no cover - filesystem dependent
        raise RSFLParsingError(f"unable to stat archive: {exc}") from exc

    if size >= max(0, MEMORY_MAP_THRESHOLD):
        flags = os.O_RDONLY
        # Windows requires the O_BINARY flag to avoid implicit newline conversion.
        flags |= getattr(os, "O_BINARY", 0)
        fd = os.open(path, flags)
        try:
            return mmap.mmap(fd, length=0, access=mmap.ACCESS_READ)
        finally:
            os.close(fd)

    return path.read_bytes()

def _release_archive_buffer(buffer: object) -> None:
    """Release buffers that expose a ``close`` method (e.g. memory maps)."""

    close = getattr(buffer, "close", None)
    if callable(close):
        try:
            close()
        except Exception:  # pragma: no cover - best-effort cleanup
            pass

def load_archive(
    archive_path: Path,
) -> Tuple[bytes | mmap.mmap, Dict[str, object], List[Dict[str, object]], Dict[str, object]]:
    """Read an Asura archive and return its bytes, metadata and entries."""

    original_bytes = _read_archive_bytes(archive_path)
    data, wrapper = _unwrap_asura_container(original_bytes)
    if data is not original_bytes:
        _release_archive_buffer(original_bytes)
    layout_info = _scan_for_rsfl(data)

    layout = layout_info.get("layout")
    entries: List[Dict[str, object]] = []

    if layout == "rsfl":
        chunk = layout_info["chunks"][0]
        rsfl_details = chunk.get("rsfl") or {}
        rsfl_offset = int(chunk["offset"])
        rsfl_chunk_size = int(chunk["chunk_size"])
        rsfl_type1 = int(rsfl_details.get("type1", 0))
        rsfl_type2 = int(rsfl_details.get("type2", 0))
        entry_count = int(rsfl_details.get("entry_count", 0))

        entries_raw, table_end = _parse_rsfl_entries(
            data, rsfl_offset, rsfl_chunk_size, entry_count
        )
        entries = _normalise_entries(entries_raw, data, rsfl_offset, rsfl_chunk_size)

        chunk_info: Dict[str, object] = {
            "layout": "rsfl",
            "offset": rsfl_offset,
            "chunk_size": rsfl_chunk_size,
            "type1": rsfl_type1,
            "type2": rsfl_type2,
            "table_end": table_end,
            "entry_count": entry_count,
        }
    elif layout == "rscf":
        chunk_info = {
            "layout": "rscf",
            "chunks": [],
        }
        for chunk in layout_info.get("chunks", []):
            chunk_entries = _parse_rscf_entries(data, chunk)
            entries.extend(chunk_entries)
            chunk_info["chunks"].append(
                {
                    "offset": int(chunk["offset"]),
                    "chunk_size": int(chunk["chunk_size"]),
                    "type1": int(chunk["type1"]),
                    "type2": int(chunk["type2"]),
                    "header": dict(chunk.get("rscf") or {}),
                }
            )
    else:  # pragma: no cover - defensive, should not happen
        raise RSFLParsingError("unsupported resource layout")

    return data, chunk_info, entries, wrapper

def _extract_replacement_payload(value: object) -> bytes | None:
    """Return the payload bytes stored in *value* if available."""

    payload: Any
    if isinstance(value, dict):
        payload = value.get("payload")
    else:
        payload = value

    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload)

    return None

def _normalise_replacement_payloads(
    replacements: Dict[str, object]
) -> Dict[str, bytes]:
    """Return a mapping with raw byte payloads extracted from replacements."""

    normalised: Dict[str, bytes] = {}
    for rel_path, value in replacements.items():
        payload = _extract_replacement_payload(value)
        if payload is not None:
            normalised[rel_path] = payload

    return normalised

def _apply_replacements(
    archive_bytes: bytes | mmap.mmap,
    entries: List[Dict[str, object]],
    replacements: Dict[str, object],
) -> Tuple[bytearray, List[Dict[str, object]]]:
    """Return a new archive with the requested replacements applied."""

    resolved_replacements = _normalise_replacement_payloads(replacements)

    updated = bytearray(archive_bytes)
    # The RSFL table typically stores offsets relative to either the chunk start
    # or the chunk end.  ``offset_anchor`` records the absolute position that was
    # used for the original entry so we can preserve the same addressing mode.
    for entry in entries:
        rel_path = entry["relative_path"]
        payload = resolved_replacements.get(rel_path)
        if payload is None:
            continue

        new_size = len(payload)
        start = entry["offset"]
        end = start + entry["size"]

        layout = entry.get("layout", "rsfl")

        if layout == "rscf":
            chunk_offset = entry.get("chunk_offset")
            header_offset = entry.get("header_offset")
            if chunk_offset is None or header_offset is None:
                raise RSFLParsingError(
                    f"replacement for {rel_path} is missing chunk metadata"
                )

            old_size = entry["size"]
            delta = new_size - old_size

            updated[start:end] = payload
            entry["offset"] = start
            entry["size"] = new_size

            struct.pack_into("<I", updated, header_offset + 8, new_size)
            entry["header_data_span"] = new_size

            if delta:
                new_chunk_size = entry["chunk_size"] + delta
                if new_chunk_size <= 0:
                    raise RSFLParsingError(
                        f"replacement for {rel_path} would shrink the RSCF chunk below zero bytes"
                    )

                struct.pack_into("<I", updated, chunk_offset + 4, new_chunk_size)
                entry["chunk_size"] = new_chunk_size

                for other in entries:
                    if other is entry:
                        continue
                    if other.get("layout") != "rscf":
                        continue
                    other_chunk_offset = other.get("chunk_offset")
                    if other_chunk_offset is None:
                        continue
                    if other_chunk_offset <= chunk_offset:
                        continue

                    other["chunk_offset"] = other_chunk_offset + delta
                    other_offset = other.get("offset")
                    if other_offset is not None:
                        other["offset"] = other_offset + delta
                    other_anchor = other.get("offset_anchor")
                    if other_anchor is not None:
                        other["offset_anchor"] = other_anchor + delta
                    other_header_offset = other.get("header_offset")
                    if other_header_offset is not None:
                        other["header_offset"] = other_header_offset + delta

            continue

        if new_size == entry["size"]:
            new_offset = start
            updated[start:end] = payload
        else:
            # The new payload does not fit in place; append it to the archive and
            # realign to 4 bytes to keep offsets consistent with the original
            # layout.
            padding = (-len(updated)) % 4
            if padding:
                updated.extend(b"\x00" * padding)
            new_offset = len(updated)
            updated.extend(payload)

        anchor = entry["offset_anchor"]
        raw_offset = new_offset - anchor
        if raw_offset < 0:
            raise RSFLParsingError(
                f"replacement for {rel_path} would produce a negative offset"
            )
        if raw_offset > 0xFFFFFFFF:
            raise RSFLParsingError(
                f"replacement for {rel_path} exceeds 32-bit offset capacity"
            )

        RSFL_ENTRY_STRUCT.pack_into(updated, entry["table_offset"], raw_offset, new_size, entry["unk"])
        entry["offset"] = new_offset
        entry["size"] = new_size
        entry["raw_offset"] = raw_offset

    return updated, entries

def _collect_replacement_payloads(
    files_info: Sequence[Dict[str, object]],
    modified_dir: Path,
) -> Dict[str, bytes]:
    """Return payloads for files that exist inside *modified_dir*."""

    replacements: Dict[str, bytes] = {}
    for manifest_entry in files_info:
        rel_path = manifest_entry.get("relative_path")
        if not isinstance(rel_path, str) or not rel_path:
            continue

        candidates: List[str] = []
        exported_relative = manifest_entry.get("exported_path")
        if isinstance(exported_relative, str) and exported_relative:
            candidates.append(exported_relative)
        candidates.append(rel_path)

        payload_path: Path | None = None
        for candidate in candidates:
            candidate_path = modified_dir / Path(*candidate.split("/"))
            if candidate_path.exists():
                payload_path = candidate_path
                break

        if payload_path is None:
            continue

        replacements[rel_path] = payload_path.read_bytes()

    return replacements

def _extract_chunk_type_overrides(
    layout_info: Dict[str, object],
    archive_bytes: bytes | mmap.mmap | None,
) -> Dict[int, Tuple[int, int]]:
    """Return chunk header type overrides keyed by chunk offset."""

    overrides: Dict[int, Tuple[int, int]] = {}
    if not archive_bytes:
        return overrides

    layout = layout_info.get("layout")
    if layout == "rscf":
        for chunk in layout_info.get("chunks", []):
            offset = int(chunk.get("offset", -1))
            if offset < 0:
                continue
            try:
                _magic, _size, type1, type2 = CHUNK_HEADER.unpack_from(archive_bytes, offset)
            except struct.error:
                continue
            overrides[offset] = (type1, type2)
    elif layout == "rsfl":
        offset = int(layout_info.get("offset", -1))
        if offset >= 0:
            try:
                _magic, _size, type1, type2 = CHUNK_HEADER.unpack_from(archive_bytes, offset)
            except struct.error:
                type1 = type2 = 0
            overrides[offset] = (type1, type2)

    return overrides

def _encode_rscf_chunk(
    *,
    type1: int,
    type2: int,
    version: int,
    data_offset: int,
    name: str,
    payload: bytes,
) -> bytes:
    """Return the bytes for a single RSCF chunk."""

    try:
        encoded_name = name.encode("utf-8")
    except UnicodeEncodeError:
        encoded_name = name.encode("latin-1", errors="replace")
    encoded_name += b"\x00"
    while len(encoded_name) % 4:
        encoded_name += b"\x00"

    rscf_header = struct.pack("<III", version & 0xFFFFFFFF, data_offset & 0xFFFFFFFF, len(payload))
    chunk_body = bytearray()
    chunk_body.extend(rscf_header)
    chunk_body.extend(encoded_name)
    chunk_body.extend(payload)

    chunk_size = CHUNK_HEADER.size + len(chunk_body)
    chunk_header = CHUNK_HEADER.pack(b"RSCF", chunk_size, type1 & 0xFFFFFFFF, type2 & 0xFFFFFFFF)
    return chunk_header + bytes(chunk_body)

def _build_rscf_patch(
    layout_info: Dict[str, object],
    patch_entries: Sequence[Tuple[Dict[str, object], bytes]],
    chunk_type_overrides: Dict[int, Tuple[int, int]],
) -> Tuple[bytes, List[str]]:
    """Return a patch archive for RSCF controlled entries."""

    archive = bytearray(ASURA_MAGIC)
    chunk_map = {int(chunk.get("offset", -1)): chunk for chunk in layout_info.get("chunks", [])}
    written: List[str] = []

    for manifest_entry, payload in patch_entries:
        rel_path = manifest_entry["relative_path"]
        chunk_offset = int(manifest_entry.get("chunk_offset", -1))
        chunk_details = chunk_map.get(chunk_offset) or {}
        type1, type2 = chunk_type_overrides.get(chunk_offset, (0, 0))
        if not type1 and not type2:
            type1 = int(chunk_details.get("type1", 0))
            type2 = int(chunk_details.get("type2", 0))

        header = dict(chunk_details.get("header") or {})
        version = int(manifest_entry.get("header_version", header.get("version", 0)))
        data_offset = int(
            manifest_entry.get("header_data_offset", header.get("data_offset", 0))
        )
        name = manifest_entry.get("name") or rel_path.replace("/", "\\")

        archive.extend(
            _encode_rscf_chunk(
                type1=type1,
                type2=type2,
                version=version,
                data_offset=data_offset,
                name=name,
                payload=payload,
            )
        )
        written.append(rel_path)

    return bytes(archive), written

def _build_rsfl_patch(
    layout_info: Dict[str, object],
    patch_entries: Sequence[Tuple[Dict[str, object], bytes]],
    chunk_type_overrides: Dict[int, Tuple[int, int]],
) -> Tuple[bytes, List[str]]:
    """Return a patch archive for RSFL controlled entries."""

    if not patch_entries:
        return b"", []

    archive = bytearray(ASURA_MAGIC)
    table = bytearray()
    struct_offsets: List[int] = []
    payload_offsets: List[int] = []
    payload_area = bytearray()
    written: List[str] = []

    for manifest_entry, payload in patch_entries:
        rel_path = manifest_entry["relative_path"]
        name = manifest_entry.get("name") or rel_path.replace("/", "\\")
        try:
            encoded = name.encode("utf-8")
        except UnicodeEncodeError:
            encoded = name.encode("latin-1", errors="replace")
        encoded += b"\x00"
        while len(encoded) % 4:
            encoded += b"\x00"
        table.extend(encoded)
        struct_offsets.append(len(table))
        table.extend(b"\x00" * RSFL_ENTRY_STRUCT.size)

        padding = (-len(payload_area)) % 4
        if padding:
            payload_area.extend(b"\x00" * padding)
        payload_offsets.append(len(payload_area))
        payload_area.extend(payload)
        written.append(rel_path)

    rsfl_type1 = int(layout_info.get("type1", 0))
    rsfl_type2 = int(layout_info.get("type2", 0))
    inner_size = RSFL_CHUNK_HEADER.size + len(table) + len(payload_area)
    count = len(patch_entries)

    # Populate RSFL entry structs now that we know the final layout.
    for (manifest_entry, payload), struct_offset, payload_offset in zip(
        patch_entries, struct_offsets, payload_offsets
    ):
        raw_offset = CHUNK_HEADER.size + RSFL_CHUNK_HEADER.size + len(table) + payload_offset
        if raw_offset > 0xFFFFFFFF:
            raise RSFLParsingError(
                f"replacement for {manifest_entry['relative_path']} exceeds 32-bit offset capacity"
            )
        unk = int(manifest_entry.get("unk", 0)) & 0xFFFFFFFF
        RSFL_ENTRY_STRUCT.pack_into(table, struct_offset, raw_offset, len(payload), unk)

    chunk_body = bytearray()
    chunk_body.extend(
        RSFL_CHUNK_HEADER.pack(0x5246534C, inner_size, rsfl_type1 & 0xFFFFFFFF, rsfl_type2 & 0xFFFFFFFF, count)
    )
    chunk_body.extend(table)
    chunk_body.extend(payload_area)

    chunk_size = CHUNK_HEADER.size + len(chunk_body)
    chunk_offset = int(layout_info.get("offset", 8))
    type1, type2 = chunk_type_overrides.get(chunk_offset, (0, 0))
    chunk_header = CHUNK_HEADER.pack(b"LFSR", chunk_size, type1 & 0xFFFFFFFF, type2 & 0xFFFFFFFF)

    archive.extend(chunk_header)
    archive.extend(chunk_body)
    return bytes(archive), written

def generate_patch_archive(
    manifest: Dict[str, object],
    replacements: Dict[str, object],
    *,
    archive_bytes: bytes | mmap.mmap | None = None,
    original_entries: Sequence[Dict[str, object]] | None = None,
) -> Tuple[bytes, List[str]]:
    """Build a minimal Asura archive containing the provided replacements."""

    files_info = manifest.get("files", [])
    if not files_info:
        raise RSFLParsingError("manifest does not contain any file entries")

    layout_info = manifest.get("rsfl") or {}
    layout_kind = layout_info.get("layout")
    if layout_kind not in {"rsfl", "rscf"}:
        raise RSFLParsingError("manifest does not describe a supported layout")

    manifest_index = {entry.get("relative_path"): entry for entry in files_info}
    entry_index = (
        {entry.get("relative_path"): entry for entry in original_entries}
        if original_entries
        else {}
    )

    patch_entries: List[Tuple[Dict[str, object], bytes]] = []
    normalised = _normalise_replacement_payloads(replacements)

    for rel_path, payload in normalised.items():
        manifest_entry = manifest_index.get(rel_path)
        if manifest_entry is None:
            continue

        if original_entries is not None and archive_bytes is not None:
            original_entry = entry_index.get(rel_path)
            if original_entry is None:
                continue
            start = int(original_entry.get("offset", 0))
            size = int(original_entry.get("size", 0))
            original_payload = archive_bytes[start : start + size]
            if payload == original_payload:
                continue

        patch_entries.append((manifest_entry, payload))

    if not patch_entries:
        raise RSFLParsingError("no modified files differ from the original archive")

    overrides = _extract_chunk_type_overrides(layout_info, archive_bytes)

    if layout_kind == "rscf":
        return _build_rscf_patch(layout_info, patch_entries, overrides)
    return _build_rsfl_patch(layout_info, patch_entries, overrides)

def write_patch_archive(
    original_archive: Path,
    manifest_path: Path,
    modified_dir: Path,
    output_path: Path,
) -> List[str]:
    """Write a minimal patch archive for files modified in ``modified_dir``."""

    manifest = json.loads(manifest_path.read_text())
    if original_archive.name != manifest.get("archive"):
        raise RSFLParsingError(
            "original archive file name does not match manifest metadata"
        )

    files_info = manifest.get("files", [])
    if not files_info:
        raise RSFLParsingError("manifest does not contain any file entries")

    archive_bytes, layout_info, entries, _wrapper = load_archive(original_archive)
    try:
        replacements = _collect_replacement_payloads(files_info, modified_dir)
        if not replacements:
            raise RSFLParsingError("no modified files found in the provided directory")

        patch_bytes, written = generate_patch_archive(
            {"rsfl": layout_info, "files": files_info, "archive": manifest.get("archive")},
            replacements,
            archive_bytes=archive_bytes,
            original_entries=entries,
        )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(patch_bytes)
        return written
    finally:
        _release_archive_buffer(archive_bytes)

def build_replacement_log(
    replacements: Dict[str, Dict[str, object]],
    *,
    archive_path: Path | None = None,
) -> Dict[str, object]:
    """Return a serialisable description of the queued replacements."""

    entries: List[Dict[str, object]] = []

    for relative_path, record in replacements.items():
        if not isinstance(relative_path, str) or not relative_path:
            continue

        entry: Dict[str, object] = {"relative_path": relative_path}

        source_info = record.get("source_info")
        if isinstance(source_info, dict):
            source_entry: Dict[str, object] = {}
            path_value = source_info.get("path")
            if isinstance(path_value, str) and path_value:
                source_entry["path"] = path_value
            metadata_path = source_info.get("metadata_path")
            if isinstance(metadata_path, str) and metadata_path:
                source_entry["metadata_path"] = metadata_path
            if source_entry:
                entry["source"] = source_entry

            metadata = source_info.get("metadata")
            if isinstance(metadata, dict):
                entry["metadata"] = metadata

        for numeric_key in ("offset", "size"):
            numeric_value = record.get(numeric_key)
            if isinstance(numeric_value, int):
                entry[numeric_key] = int(numeric_value)

        entries.append(entry)

    log_payload: Dict[str, object] = {
        "schema": 1,
        "generated": datetime.utcnow().isoformat() + "Z",
        "replacements": entries,
    }

    if archive_path is not None:
        log_payload["archive"] = str(archive_path)

    return log_payload

def write_replacement_log(
    patch_path: Path,
    replacements: Dict[str, Dict[str, object]],
    *,
    archive_path: Path | None = None,
) -> Path:
    """Write a JSON log describing queued replacements next to ``patch_path``."""

    log_payload = build_replacement_log(replacements, archive_path=archive_path)
    log_path = patch_path.with_suffix(patch_path.suffix + ".replacements.json")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(log_payload, indent=2, ensure_ascii=False))
    return log_path

def repack_archive(original_archive: Path, manifest_path: Path, modified_dir: Path, output_path: Path) -> None:
    """Create a patched copy of the archive using files stored on disk."""

    manifest = json.loads(manifest_path.read_text())
    if original_archive.name != manifest.get("archive"):
        raise RSFLParsingError(
            "original archive file name does not match manifest metadata"
        )

    files_info = manifest.get("files", [])
    if not files_info:
        raise RSFLParsingError("manifest does not contain any file entries")

    archive_bytes, layout_info, entries, wrapper = load_archive(original_archive)
    try:
        manifest_wrapper = manifest.get("wrapper") or {"kind": "raw"}
        if manifest_wrapper.get("kind") != wrapper.get("kind"):
            raise RSFLParsingError(
                "manifest was generated from an archive with a different compression wrapper"
            )
        replacements = _collect_replacement_payloads(files_info, modified_dir)
        if not replacements:
            raise RSFLParsingError("no modified files found in the provided directory")

        updated, _ = _apply_replacements(archive_bytes, entries, replacements)
        wrapped = _wrap_asura_container(bytes(updated), wrapper)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(wrapped)
    finally:
        _release_archive_buffer(archive_bytes)

def extract_archive(archive_path: Path, output_dir: Path) -> Path:
    """Extract RSFL controlled files and write a manifest.

    Returns the path to the generated manifest file.
    """

    data, rsfl_info, entries, wrapper = load_archive(archive_path)

    try:
        archive_name = archive_path.name
        output_dir.mkdir(parents=True, exist_ok=True)

        # Dump all files controlled by the RSFL table.
        extracted_files: List[Dict[str, object]] = []
        for entry in entries:
            payload = data[entry["offset"] : entry["offset"] + entry["size"]]
            relative_path = entry["relative_path"]
            export_relative, original_relative = resolve_export_relative_path(
                relative_path, payload
            )
            relative_target = (
                Path(*export_relative.split("/")) if export_relative else Path()
            )
            target_path = output_dir / relative_target
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(payload)
            entry_copy = dict(entry)
            if original_relative is not None:
                entry_copy["exported_path"] = export_relative
                entry_copy["original_path"] = original_relative
            extracted_files.append(entry_copy)

        manifest = {
            "archive": archive_name,
            "rsfl": rsfl_info,
            "wrapper": wrapper,
            "files": extracted_files,
        }

        manifest_path = output_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2))

        # Store pristine copy of the archive next to the extraction to simplify repacking.
        shutil.copy2(archive_path, output_dir / archive_name)
        return manifest_path
    finally:
        _release_archive_buffer(data)

