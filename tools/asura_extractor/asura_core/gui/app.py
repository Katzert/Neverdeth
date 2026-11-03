"""Tkinter application for browsing Asura archives."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import math
import os
import queue
import shlex
import shutil
import struct
import subprocess
import tempfile
import threading
import textwrap
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Dict, Iterable, List, Sequence, Set, Tuple

import mmap

from ..archive import (
    RSFLParsingError,
    _apply_replacements,
    _extract_replacement_payload,
    _release_archive_buffer,
    _wrap_asura_container,
    _write_bytes_in_chunks,
    build_replacement_log,
    generate_patch_archive,
    load_archive,
    write_replacement_log,
)
from ..config import CATEGORY_LABELS, SUPPORTED_ARCHIVE_SUFFIXES
from ..export import (
    _coerce_audio_metadata,
    detect_image_format,
    detect_wav_metadata,
    is_audio_entry,
    is_image_entry,
    is_supported_entry,
    resolve_export_relative_path,
)
from .audio import (
    NUMPY_AVAILABLE,
    SOUNDDEVICE_AVAILABLE,
    _describe_preview_format,
    _pcm_to_float32,
    _prepare_preview_audio,
    _sounddevice_backend_available,
    sounddevice,
)
from .preview import (
    Image,
    ImageTk,
    PIL_AVAILABLE,
    PIL_IMAGETK_AVAILABLE,
    UnidentifiedImageError,
    _composite_transparency_preview,
    _encode_alpha_image_to_dds,
    _encode_image_to_dds,
    _encode_luminance_image_to_dds,
    _prepare_photo_image,
    _prepare_preview_image,
    _parse_dds_header,
)

try:  # pragma: no cover - Tkinter is optional.
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    TK_AVAILABLE = True
except Exception:  # pragma: no cover - Tk availability is platform dependent.
    tk = None  # type: ignore[assignment]
    filedialog = None  # type: ignore[assignment]
    messagebox = None  # type: ignore[assignment]
    ttk = None  # type: ignore[assignment]
    TK_AVAILABLE = False

try:  # pragma: no cover - Drag and drop support depends on optional packages.
    from tkinterdnd2 import DND_FILES, TkinterDnD

    TKDND_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency.
    DND_FILES = None
    TkinterDnD = None  # type: ignore[assignment]
    TKDND_AVAILABLE = False

IMAGEMAGICK_CANDIDATES = ("magick", "convert")
IMAGEMAGICK_COMPRESSION_ALIASES = {
    "DXT1": "dxt1",
    "DXT2": "dxt3",
    "DXT3": "dxt3",
    "DXT4": "dxt5",
    "DXT5": "dxt5",
    "ATI1": "bc4",
    "BC4U": "bc4",
    "BC4S": "bc4",
    "BC1": "bc1",
    "BC2": "bc2",
    "BC3": "bc3",
    "BC4": "bc4",
    "BC5": "bc5",
    "BC5U": "bc5",
    "BC5S": "bc5",
    "ATI2": "bc5",
    "BC6": "bc6",
    "BC7": "bc7",
    "NONE": "none",
}
IMAGEMAGICK_DXGI_MAP = {
    70: "bc1",
    71: "bc1",
    72: "bc1",
    73: "bc2",
    74: "bc2",
    75: "bc2",
    76: "bc3",
    77: "bc3",
    78: "bc3",
    79: "bc4",
    80: "bc4",
    81: "bc4",
    82: "bc5",
    83: "bc5",
    84: "bc5",
    94: "bc6",
    95: "bc6",
    96: "bc6",
    97: "bc7",
    98: "bc7",
    99: "bc7",
}

def _normalise_archive_suffix(candidate: str | None) -> str:
    """Return the preferred archive suffix when prompting for output paths."""

    if candidate:
        lowered = candidate.lower()
        if lowered in SUPPORTED_ARCHIVE_SUFFIXES:
            return lowered

    return SUPPORTED_ARCHIVE_SUFFIXES[0]

def _build_archive_filetypes(preferred_suffix: str) -> List[Tuple[str, str]]:
    """Return Tk file dialog filters prioritising *preferred_suffix*."""

    ordered_suffixes = [preferred_suffix]
    ordered_suffixes.extend(
        suffix for suffix in SUPPORTED_ARCHIVE_SUFFIXES if suffix != preferred_suffix
    )

    pattern = " ".join(f"*{suffix}" for suffix in ordered_suffixes)
    readable = ", ".join(f"*{suffix}" for suffix in ordered_suffixes)
    description = f"Supported Asura archives ({readable})"

    return [
        (description, pattern),
        ("All files", "*.*"),
    ]

class TextureManagerGUI:
    """Minimal Tk based browser for previewing and patching texture entries."""

    def __init__(self) -> None:
        if not TK_AVAILABLE:
            raise RuntimeError("Tkinter is not available on this platform")

        if TKDND_AVAILABLE and TkinterDnD is not None:
            self.root = TkinterDnD.Tk()
        else:
            self.root = tk.Tk()
        self.root.title("RSFL Texture Manager")
        self.root.geometry("720x480")

        self._configure_styles()

        self.archive_path: Path | None = None
        self.archive_bytes: bytes | mmap.mmap | None = None
        self.all_entries: List[Dict[str, object]] = []
        self.image_entries: List[Dict[str, object]] = []
        self.audio_entries: List[Dict[str, object]] = []
        self.previewable_entries: List[Dict[str, object]] = []
        self.supported_entries: List[Dict[str, object]] = []
        self.entries: List[Dict[str, object]] = []
        self.replacements: Dict[str, Dict[str, object]] = {}
        self.wrapper_info: Dict[str, object] | None = None
        self.layout_info: Dict[str, object] | None = None
        self.node_to_entry: Dict[str, Dict[str, object]] = {}
        self.entry_nodes: Dict[str, str] = {}
        self.node_to_path: Dict[str, Tuple[str, ...]] = {}
        self.tree_menu: tk.Menu | None = None
        self.last_activated_path: str | None = None
        self._expand_all_on_refresh = False
        self.drag_and_drop_enabled = False
        self.drag_and_drop_status_message: str | None = None
        self.preview_display_base: "Image.Image" | None = None
        self._fit_scale: float | None = None
        self._current_zoom = 1.0
        self.thumbnail_images: List["ImageTk.PhotoImage"] = []
        self._drag_tempdir: Path | None = None
        self._drag_export_paths: List[Path] = []
        self._folder_import_thread: threading.Thread | None = None
        self._folder_import_cancel_event: threading.Event | None = None

        self._preview_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="rsfl-preview"
        )
        self._preview_task: Future[Dict[str, object]] | None = None
        self._preview_request_id = 0
        self._preview_task_kind = "image"
        self._pending_channel_state: Dict[str, int] | None = None
        self._audio_preview_data: Dict[str, object] | None = None
        self._audio_play_obj: object | None = None
        self._audio_is_playing = False
        self._audio_poll_job: str | None = None
        self._sounddevice_stop_requested = False

        self._tree_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="rsfl-tree"
        )
        self._tree_refresh_generation = 0
        self._tree_insert_job: str | None = None

        self.backup_var = tk.BooleanVar(value=False)
        self.backup_prompted = False

        self._build_ui()
        self._setup_drag_and_drop()
        self._show_dependency_warnings()
        self.root.protocol("WM_DELETE_WINDOW", self._shutdown)

    # ------------------------------------------------------------------ UI --
    def _configure_styles(self) -> None:
        if not TK_AVAILABLE:
            return

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        base_background = style.lookup("TFrame", "background") or "#f0f0f0"
        self.root.configure(background=base_background)

        style.configure("Modern.TFrame", background=base_background)
        style.configure("Modern.TLabel", background=base_background, font=("Segoe UI", 10))
        style.configure("Status.TLabel", background=base_background, font=("Segoe UI", 9))
        style.configure("Accent.TButton", padding=(10, 6))
        style.map(
            "Accent.TButton",
            background=[("active", "#4c6ef5"), ("disabled", "#bfc4d6")],
            foreground=[("active", "#ffffff"), ("disabled", "#6c7489")],
        )
        style.configure("Accent.TButton", background="#3b5bdb", foreground="#ffffff")
        style.configure("Treeview", font=("Segoe UI", 10))
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
        style.configure("Modern.TCheckbutton", background=base_background, font=("Segoe UI", 9))

    def _call_in_main_thread(self, func, *args, **kwargs):
        """Execute *func* on Tk's main thread and return its result."""

        if threading.current_thread() is threading.main_thread():
            return func(*args, **kwargs)

        result: "queue.Queue[Tuple[bool, Any]]" = queue.Queue()

        def invoke() -> None:
            try:
                value = func(*args, **kwargs)
            except Exception as exc:  # pragma: no cover - Tk callbacks propagate to caller
                result.put((False, exc))
            else:
                result.put((True, value))

        self.root.after(0, invoke)
        success, payload = result.get()
        if success:
            return payload
        raise payload

    def _show_info(self, title: str, message: str) -> object | None:
        if messagebox is None:
            return None
        return self._call_in_main_thread(messagebox.showinfo, title, message)

    def _show_warning(self, title: str, message: str) -> object | None:
        if messagebox is None:
            return None
        return self._call_in_main_thread(messagebox.showwarning, title, message)

    def _show_error(self, title: str, message: str) -> object | None:
        if messagebox is None:
            return None
        return self._call_in_main_thread(messagebox.showerror, title, message)

    def _ask_yes_no(self, title: str, message: str) -> bool:
        if messagebox is None:
            return False
        response = self._call_in_main_thread(messagebox.askyesno, title, message)
        return bool(response)

    def _setup_drag_and_drop(self) -> None:
        if not hasattr(self, "root"):
            return

        self.drag_and_drop_status_message = None
        if not (TKDND_AVAILABLE and DND_FILES):
            message = (
                "Arrastra y suelta deshabilitado: instala tkinterdnd2 (pip install tkinterdnd2)."
            )
            self.drag_and_drop_status_message = message
            self._set_status(message)
            return

        drop_errors: List[str] = []
        widgets = []
        for attr in ("tree", "preview_canvas", "root"):
            widget = getattr(self, attr, None)
            if widget is not None:
                widgets.append(widget)

        for widget in widgets:
            register = getattr(widget, "drop_target_register", None)
            bind = getattr(widget, "dnd_bind", None)
            if register is None or bind is None:
                continue
            try:
                register(DND_FILES)
                bind("<<Drop>>", self._on_files_dropped)
                self.drag_and_drop_enabled = True
            except Exception as exc:  # pragma: no cover - depends on tkdnd availability
                drop_errors.append(str(exc))

        drag_errors: List[str] = []
        tree = getattr(self, "tree", None)
        if tree is not None:
            drag_register = getattr(tree, "drag_source_register", None)
            drag_bind = getattr(tree, "dnd_bind", None)
            if drag_register is not None and drag_bind is not None:
                try:
                    drag_register(1, DND_FILES)
                    drag_bind("<<DragInitCmd>>", self._on_tree_drag_init)
                    drag_bind("<<DragEndCmd>>", self._on_tree_drag_end)
                    self.drag_and_drop_enabled = True
                except Exception as exc:  # pragma: no cover - depends on tkdnd availability
                    drag_errors.append(str(exc))

        if not self.drag_and_drop_enabled:
            if drop_errors or drag_errors:
                failure_reasons = "; ".join(err for err in drop_errors + drag_errors if err)
                if failure_reasons:
                    reason = textwrap.shorten(failure_reasons, width=160, placeholder="…")
                    message = (
                        "Drag and drop disabled: tkinterdnd2 is installed but failed to initialize "
                        f"({reason})."
                    )
                    self.drag_and_drop_status_message = message
                    self._set_status(message)
                    return

            message = (
                "Drag and drop disabled: install tkinterdnd2 (pip install tkinterdnd2)."
            )
            self.drag_and_drop_status_message = message
            self._set_status(message)
            return

        guidance = (
            "Abre un archivo RS, selecciona la textura o el audio que quieras reemplazar y "
            "arrastra el archivo (.dds, .png, .tga o .wav) sobre la ventana. También puedes "
            "arrastrar un archivo .rsmeta para que la herramienta busque el reemplazo junto a la metadata, "
            "o arrastrar entradas del árbol hacia tu escritorio para exportarlas; no arrastres manifest.json."
        )
        self.drag_and_drop_status_message = guidance
        self._set_status(guidance)

    def _show_dependency_warnings(self) -> None:
        if messagebox is None:
            return

        warnings: List[Tuple[str, str]] = []

        if not PIL_AVAILABLE:
            warnings.append(
                (
                    "Falta Pillow",
                    "Las vistas previas de texturas estarán deshabilitadas porque Pillow no está instalado.",
                )
            )
        elif not PIL_IMAGETK_AVAILABLE:
            warnings.append(
                (
                    "Falta Pillow ImageTk",
                    "Instala pillow[tk] o python3-tk para habilitar las vistas previas de texturas.",
                )
            )

        audio_missing: List[str] = []
        if not SOUNDDEVICE_AVAILABLE:
            audio_missing.append("sounddevice")
        if not NUMPY_AVAILABLE:
            audio_missing.append("numpy")
        if audio_missing:
            if len(audio_missing) == 1:
                deps = audio_missing[0]
            else:
                deps = " y ".join(audio_missing)
            warnings.append(
                (
                    "Audio deshabilitado",
                    f"La reproducción de audio no funcionará hasta instalar {deps} (pip install { ' '.join(audio_missing) }).",
                )
            )

        if not TKDND_AVAILABLE:
            warnings.append(
                (
                    "Arrastrar y soltar deshabilitado",
                    "Instala tkinterdnd2 para habilitar arrastrar y soltar (pip install tkinterdnd2).",
                )
            )
        elif not self.drag_and_drop_enabled:
            message = self.drag_and_drop_status_message or (
                "Arrastrar y soltar no pudo inicializarse; revisa la instalación de tkinterdnd2."
            )
            warnings.append(("Arrastrar y soltar deshabilitado", message))

        for title, text in warnings:
            try:
                self._show_warning(title, text)
            except Exception:
                break

        if self.drag_and_drop_enabled and self.drag_and_drop_status_message:
            try:
                self._show_info("Arrastrar y soltar habilitado", self.drag_and_drop_status_message)
            except Exception:
                pass

    def _on_files_dropped(self, event: object) -> None:
        if self.archive_bytes is None:
            return

        data = getattr(event, "data", "")
        if not data:
            return

        paths: List[Path] = []
        try:
            raw_paths = self.root.tk.splitlist(data)
        except Exception:
            try:
                raw_paths = shlex.split(data)
            except ValueError:
                raw_paths = [data]
        for item in raw_paths:
            path_text = item.strip()
            if not path_text:
                continue
            cleaned = path_text
            if cleaned.startswith("{") and cleaned.endswith("}"):
                cleaned = cleaned[1:-1]
            paths.append(Path(cleaned))

        if not paths:
            return

        directories = [path for path in paths if path.is_dir()]
        handled_directory = False
        for directory_path in directories:
            try:
                started = self._start_folder_import(directory_path)
            except Exception:
                started = False
            handled_directory = handled_directory or started
        if handled_directory:
            return

        metadata_paths = [
            path
            for path in paths
            if path.name.lower().endswith(".rsmeta")
        ]
        for metadata_path in metadata_paths:
            try:
                handled = self._import_replacement_from_metadata_file(metadata_path)
            except Exception:
                handled = False
            if handled:
                return
        if metadata_paths:
            return

        entry = self._current_entry_selection()
        if entry is None:
            self._show_info(
                "Select an entry",
                "Choose a texture or audio entry in the list before dropping a file.",
            )
            return

        self._import_replacement_from_path(entry, paths[0])

    def _on_tree_drag_init(self, _event: object) -> Tuple[str, str, str] | None:
        if self.archive_bytes is None or not DND_FILES:
            return None

        try:
            node_ids = list(self.tree.selection())
        except tk.TclError:
            node_ids = []
        if not node_ids:
            return None

        entries = self._collect_entries_from_nodes(node_ids)
        if not entries:
            return None

        files = self._prepare_drag_temp_files(entries)
        if not files:
            return None

        data = str(self.root.tk.call("list", *map(str, files)))
        return ("copy", DND_FILES, data)

    def _on_tree_drag_end(self, _event: object) -> None:
        # Keep temporary files available for the duration of the session so
        # drop targets can finish copying them. Cleanups happen between drags
        # and on shutdown.
        pass

    def _cleanup_drag_tempdir(self) -> None:
        if self._drag_tempdir and self._drag_tempdir.exists():
            with contextlib.suppress(Exception):
                shutil.rmtree(self._drag_tempdir)
        self._drag_tempdir = None
        self._drag_export_paths = []

    def _prepare_drag_temp_files(
        self, entries: Sequence[Dict[str, object]]
    ) -> List[Path]:
        if self.archive_bytes is None:
            return []

        self._cleanup_drag_tempdir()
        tempdir = Path(tempfile.mkdtemp(prefix="asura-drag-"))
        root_name = self.archive_path.stem if self.archive_path else "asura-export"
        base_dir = tempdir / root_name
        base_dir.mkdir(parents=True, exist_ok=True)
        created: List[Path] = []
        nested_structure = False
        for entry in entries:
            payload = self.archive_bytes[entry["offset"] : entry["offset"] + entry["size"]]
            replacement_value = self.replacements.get(entry["relative_path"])
            replacement_payload = _extract_replacement_payload(replacement_value)
            if replacement_payload is not None:
                payload = replacement_payload

            export_relative, _ = resolve_export_relative_path(
                entry["relative_path"], payload
            )
            relative_target = (
                Path(*export_relative.split("/")) if export_relative else Path()
            )
            if relative_target == Path():
                fallback_name = Path(entry.get("relative_path", "")).name or "entry"
                relative_target = Path(fallback_name)
            if relative_target.parent not in (Path(), Path(".")):
                nested_structure = True
            target = base_dir / relative_target
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            created.append(target)

        self._drag_tempdir = tempdir
        self._drag_export_paths = [base_dir, *created]
        if len(created) == 1 and not nested_structure:
            return created
        return [base_dir]

    def _current_entry_selection(self) -> Dict[str, object] | None:
        node_ids = [
            node_id for node_id in self.tree.selection() if node_id in self.node_to_entry
        ]
        if node_ids:
            return self.node_to_entry[node_ids[-1]]
        if self.last_activated_path and self.last_activated_path in self.entry_nodes:
            node_id = self.entry_nodes[self.last_activated_path]
            return self.node_to_entry.get(node_id)
        return None

    def _collect_entries_from_nodes(
        self, node_ids: Sequence[str]
    ) -> List[Dict[str, object]]:
        collected: List[Dict[str, object]] = []
        seen: set[str] = set()

        def visit(node_id: str) -> None:
            if node_id in self.node_to_entry:
                entry = self.node_to_entry[node_id]
                relative_path = entry.get("relative_path")
                if isinstance(relative_path, str) and relative_path not in seen:
                    collected.append(entry)
                    seen.add(relative_path)
            if hasattr(self.tree, "get_children"):
                try:
                    children = self.tree.get_children(node_id)
                except (tk.TclError, AttributeError):
                    children = ()
            else:
                children = ()
            for child in children:
                visit(child)

        for node_id in node_ids:
            visit(node_id)

        return collected

    def _entry_collections(self) -> List[Sequence[Dict[str, object]]]:
        collections: List[Sequence[Dict[str, object]]] = []
        seen_ids: Set[int] = set()
        for attr_name in (
            "entries",
            "supported_entries",
            "previewable_entries",
            "all_entries",
            "image_entries",
            "audio_entries",
        ):
            candidate = getattr(self, attr_name, None)
            if isinstance(candidate, Sequence) and not isinstance(candidate, (str, bytes)):
                collection_id = id(candidate)
                if collection_id in seen_ids:
                    continue
                seen_ids.add(collection_id)
                collections.append(candidate)
        return collections

    def _build_entry_index(self) -> Dict[str, Dict[str, object]]:
        index: Dict[str, Dict[str, object]] = {}
        for collection in self._entry_collections():
            for entry in collection:
                if not isinstance(entry, dict):
                    continue
                relative_path = entry.get("relative_path")
                if isinstance(relative_path, str):
                    index[relative_path] = entry
        return index

    def _load_sidecar_metadata(
        self, source_path: Path, source_info: Dict[str, object]
    ) -> Dict[str, object] | None:
        metadata_path = source_path.with_name(source_path.name + ".rsmeta")
        if not metadata_path.exists():
            alternate_metadata = source_path.with_suffix(".rsmeta")
            if alternate_metadata.exists():
                metadata_path = alternate_metadata

        if not metadata_path.exists():
            return None

        try:
            metadata = json.loads(metadata_path.read_text())
        except Exception as exc:  # pragma: no cover - GUI path
            self._show_warning(
                "Metadata not loaded",
                (
                    f"Could not read metadata from {metadata_path.name}: {exc}.\n"
                    "The currently selected entry will be used."
                ),
            )
            return None

        source_info["metadata_path"] = str(metadata_path)
        source_info["metadata"] = metadata
        if not isinstance(metadata, dict):
            return None

        audio_metadata = _coerce_audio_metadata(metadata.get("audio"))
        if audio_metadata is not None:
            metadata["audio"] = audio_metadata
            source_info["audio_metadata"] = audio_metadata
        else:
            metadata.pop("audio", None)

        return metadata

    def _resolve_entry_from_metadata(
        self,
        entry: Dict[str, object],
        metadata: Dict[str, object] | None,
        source_info: Dict[str, object],
    ) -> Tuple[Dict[str, object] | None, str]:
        if not isinstance(metadata, dict):
            return entry, ""

        metadata_note = ""
        meta_relative = metadata.get("relative_path")
        if isinstance(meta_relative, str) and meta_relative:
            resolved_entry: Dict[str, object] | None = None
            for collection in self._entry_collections():
                resolved_entry = next(
                    (
                        candidate
                        for candidate in collection
                        if candidate.get("relative_path") == meta_relative
                    ),
                    None,
                )
                if resolved_entry is not None:
                    break
            if resolved_entry is None:
                self._show_warning(
                    "Entry not found",
                    (
                        "The metadata references an entry that could not be found in this archive:\n"
                        f"{meta_relative}\n"
                        "Select the entry manually to continue."
                    ),
                )
                return entry, ""

            metadata_path = source_info.get("metadata_path")
            metadata_note = (
                f" using metadata from {Path(metadata_path).name}" if metadata_path else ""
            )
            return resolved_entry, metadata_note

        self._show_warning(
            "Incomplete metadata",
            (
                "The metadata does not contain a valid 'relative_path'.\n"
                "The current selection will be used."
            ),
        )
        return entry, ""

    def _import_replacement_from_metadata_file(self, metadata_path: Path) -> bool:
        try:
            metadata_text = metadata_path.read_text()
        except Exception as exc:  # pragma: no cover - GUI path
            self._show_error(
                "Unable to read metadata",
                f"Could not read metadata from {metadata_path.name}: {exc}",
            )
            return False

        try:
            metadata = json.loads(metadata_text)
        except Exception as exc:  # pragma: no cover - GUI path
            self._show_error(
                "Invalid metadata",
                f"{metadata_path.name} does not contain valid JSON: {exc}",
            )
            return False

        if not isinstance(metadata, dict):
            self._show_warning(
                "Invalid metadata",
                f"{metadata_path.name} does not contain a metadata object.",
            )
            return False

        relative_path = metadata.get("relative_path")
        if not isinstance(relative_path, str) or not relative_path:
            self._show_warning(
                "Incomplete metadata",
                (
                    f"{metadata_path.name} does not include a valid 'relative_path'.\n"
                    "Re-export the asset to regenerate the metadata before retrying."
                ),
            )
            return False

        entry_index = self._build_entry_index()
        entry = entry_index.get(relative_path)
        if entry is None:
            self._show_warning(
                "Entry not found",
                (
                    "The metadata references an entry that is not present in the opened archive:\n"
                    f"{relative_path}"
                ),
            )
            return False

        payload_path = metadata_path.with_suffix("")
        if not payload_path.exists():
            self._show_warning(
                "Replacement not found",
                (
                    f"Could not locate the exported file next to {metadata_path.name}.\n"
                    "Keep the replacement file and its .rsmeta companion together."
                ),
            )
            return False

        return self._import_replacement_from_path(entry, payload_path)

    def _confirm_replacement_format(
        self,
        entry: Dict[str, object],
        payload: bytes,
        metadata: Dict[str, object] | None = None,
    ) -> bool:
        if self.archive_bytes is None:
            return True

        original = self.archive_bytes[entry["offset"] : entry["offset"] + entry["size"]]
        warnings: List[str] = []

        entry_is_audio = is_audio_entry(entry.get("relative_path", ""))
        expected_audio = detect_wav_metadata(original)
        replacement_audio = detect_wav_metadata(payload)
        metadata_audio = None
        if isinstance(metadata, dict):
            metadata_audio = _coerce_audio_metadata(metadata.get("audio"))
        if expected_audio is None and metadata_audio is not None:
            expected_audio = metadata_audio

        if entry_is_audio or expected_audio is not None or metadata_audio is not None:
            if expected_audio is None:
                warnings.append(
                    "The original entry is marked as audio but its WAV metadata could not be parsed."
                )
            if replacement_audio is None:
                warnings.append(
                    "Replacement file is not recognised as uncompressed WAV audio."
                )
            if expected_audio and replacement_audio:
                for key, label in (
                    ("channels", "channel count"),
                    ("sample_rate", "sample rate"),
                    ("bits_per_sample", "bit depth"),
                ):
                    original_value = expected_audio.get(key)
                    replacement_value = replacement_audio.get(key)
                    if (
                        isinstance(original_value, int)
                        and isinstance(replacement_value, int)
                        and original_value != replacement_value
                    ):
                        warnings.append(
                            f"Expected {label} {original_value}, but replacement reports {replacement_value}."
                        )

            if not warnings:
                return True

            message = "\n\n".join(textwrap.fill(text, 90) for text in warnings)
            message += "\n\nDo you want to continue with the replacement?"
            return self._ask_yes_no("Unexpected audio format", message)

        original_dds = _parse_dds_header(original)
        new_dds = _parse_dds_header(payload)

        if original.startswith(b"DDS ") and not payload.startswith(b"DDS "):
            warnings.append(
                "The original texture is stored as DDS, but the imported file is not."
            )
        elif original_dds and new_dds:
            orig_comp = original_dds.get("compression")
            new_comp = new_dds.get("compression")
            if orig_comp and new_comp and orig_comp != new_comp:
                warnings.append(
                    f"Expected a {orig_comp} texture, but the imported file reports {new_comp}."
                )
            orig_alpha = bool(original_dds.get("channel_masks", {}).get("A"))
            new_alpha = bool(new_dds.get("channel_masks", {}).get("A"))
            if orig_alpha and not new_alpha:
                warnings.append(
                    "The original texture contains an alpha channel and the replacement does not."
                )
            if not orig_alpha and new_alpha:
                warnings.append(
                    "The original texture does not use an alpha channel; ensure adding one does not introduce artifacts."
                )
        elif original_dds and not new_dds:
            warnings.append(
                "No valid DDS header was detected in the replacement, but the original texture uses one."
            )

        original_suffix = Path(entry.get("relative_path", "")).suffix.lower()
        _, original_detected_extension = detect_image_format(original)
        if original_detected_extension:
            original_suffix = original_detected_extension

        source_suffix = ""
        _, detected_extension = detect_image_format(payload)
        if detected_extension:
            source_suffix = detected_extension
        elif payload.startswith(b"DDS "):
            source_suffix = ".dds"
        if source_suffix and original_suffix and source_suffix != original_suffix:
            warnings.append(
                f"The original texture uses the {original_suffix} extension, but the imported file appears to be {source_suffix}."
            )

        if not warnings:
            return True

        message = "\n\n".join(textwrap.fill(text, 90) for text in warnings)
        message += "\n\nDo you want to continue with the replacement?"
        return self._ask_yes_no("Unexpected format", message)

    def _maybe_prompt_for_backup(self) -> None:
        if self.backup_prompted:
            return
        response = self._ask_yes_no(
            "Create backup",
            (
                "Do you want to create an automatic backup before replacing textures or audio?\n"
                "You can change this preference from the 'Create automatic backup' checkbox."
            ),
        )
        self.backup_var.set(bool(response))
        self.backup_prompted = True

    def _maybe_create_backup(self, entry: Dict[str, object], source_path: Path) -> None:
        if self.archive_bytes is None or not self.backup_var.get():
            return

        original_payload = self.archive_bytes[entry["offset"] : entry["offset"] + entry["size"]]
        backup_dir = source_path.parent / "rsmanager_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)

        relative_path = entry.get("relative_path") or "entry"
        base_name = Path(relative_path.replace("/", "_") or "entry")
        suffix = base_name.suffix or source_path.suffix or ".bin"
        backup_name = f"{base_name.stem}_backup{suffix}"
        backup_path = backup_dir / backup_name
        if backup_path.exists():
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_path = backup_dir / f"{base_name.stem}_backup_{timestamp}{suffix}"

        try:
            backup_path.write_bytes(original_payload)
        except Exception as exc:  # pragma: no cover - filesystem errors are environment specific
            self._show_warning(
                "Backup not created",
                f"Failed to write the backup to {backup_path}: {exc}",
            )
            return

        self._set_status(f"Backup saved to {backup_path}")

    @staticmethod
    def _resolve_imagemagick_command() -> List[str] | None:
        for candidate in IMAGEMAGICK_CANDIDATES:
            executable = shutil.which(candidate)
            if executable:
                return [executable]
        return None

    @staticmethod
    def _map_dds_compression_to_imagemagick(
        dds_metadata: Dict[str, object] | None,
    ) -> str | None:
        if not isinstance(dds_metadata, dict):
            return None

        fourcc_value = dds_metadata.get("fourcc")
        if isinstance(fourcc_value, str):
            normalised = fourcc_value.strip().upper()
            mapped = IMAGEMAGICK_COMPRESSION_ALIASES.get(normalised)
            if mapped:
                return mapped

        compression_name = dds_metadata.get("compression")
        if isinstance(compression_name, str):
            normalised = compression_name.strip().upper()
            if "UNCOMPRESSED" in normalised:
                if "ALPHA" in normalised:
                    return "alpha"
                if "LUMINANCE" in normalised:
                    return "luminance"
                return "none"
            if normalised.startswith("DXGI_FORMAT_"):
                suffix = normalised.split("_", 2)[-1]
                if suffix.isdigit():
                    mapped = IMAGEMAGICK_DXGI_MAP.get(int(suffix))
                    if mapped:
                        return mapped
            base = normalised.split()[0].rstrip(")").lstrip("(")
            mapped = IMAGEMAGICK_COMPRESSION_ALIASES.get(base)
            if mapped:
                return mapped
            if base.startswith("BC") and base[2:].isdigit():
                return base.lower()

        dxgi_format = dds_metadata.get("dxgi_format")
        if isinstance(dxgi_format, int):
            mapped = IMAGEMAGICK_DXGI_MAP.get(dxgi_format)
            if mapped:
                return mapped

        return None

    @staticmethod
    def _resolve_imagemagick_settings(
        dds_metadata: Dict[str, object] | None,
    ) -> Tuple[str | None, int]:
        compression_option = TextureManagerGUI._map_dds_compression_to_imagemagick(dds_metadata)
        mipmaps = 10
        if isinstance(dds_metadata, dict):
            requested = dds_metadata.get("mipmaps")
            if isinstance(requested, int) and requested > 0:
                mipmaps = requested
            else:
                detected = dds_metadata.get("detected_mipmaps")
                if isinstance(detected, int) and detected > 0:
                    mipmaps = max(10, detected)
        if mipmaps <= 0:
            mipmaps = 10
        return compression_option, mipmaps

    def _encode_png_with_pillow(
        self,
        source_path: Path,
        *,
        mode: str,
        expected_label: str,
        dds_metadata: Dict[str, object] | None,
    ) -> Tuple[bytes, Dict[str, object]]:
        if not PIL_AVAILABLE or Image is None:
            raise RuntimeError("Pillow is required to import this PNG format")

        with Image.open(source_path) as png_image:
            png_image.load()
            source_mode = png_image.mode
            channel_image = png_image.convert("L")

        if mode == "alpha":
            payload = _encode_alpha_image_to_dds(channel_image)
            compression_value = "alpha"
        else:
            payload = _encode_luminance_image_to_dds(channel_image)
            compression_value = "luminance"

        mipmaps = 1
        requested_mipmaps: int | None = None
        if isinstance(dds_metadata, dict):
            requested = dds_metadata.get("mipmaps")
            if isinstance(requested, int) and requested > 0:
                requested_mipmaps = requested

        conversion_info: Dict[str, object] = {
            "tool": "Pillow",
            "compression": compression_value,
            "compression_display": expected_label,
            "mipmaps": mipmaps,
            "bytes": len(payload),
            "source_mode": source_mode,
        }
        if requested_mipmaps and requested_mipmaps > 1:
            conversion_info["note"] = (
                f"Requested {requested_mipmaps} mipmaps; generated base level only."
            )

        return payload, conversion_info

    def _transcode_png_to_dds(
        self,
        entry: Dict[str, object],
        source_path: Path,
        metadata: Dict[str, object] | None,
    ) -> Tuple[bytes, Dict[str, object]]:
        command = self._resolve_imagemagick_command()
        if command is None:
            raise RuntimeError(
                "ImageMagick is required to import PNG textures. Install ImageMagick and ensure 'magick' or 'convert' is available in PATH."
            )

        dds_metadata = metadata.get("dds") if isinstance(metadata, dict) else None
        if not isinstance(dds_metadata, dict) and self.archive_bytes is not None:
            offset = int(entry.get("offset", 0))
            size = int(entry.get("size", 0))
            original = self.archive_bytes[offset : offset + size]
            original_dds = _parse_dds_header(original)
            if original_dds is not None:
                dds_metadata = {
                    key: value
                    for key, value in original_dds.items()
                    if key in {"compression", "fourcc", "dxgi_format", "mipmaps"}
                }

        compression_option, mipmaps = self._resolve_imagemagick_settings(dds_metadata)

        expected_compression = None
        expects_alpha = False
        expects_luminance = False
        if isinstance(dds_metadata, dict):
            expected_compression = dds_metadata.get("compression")
        if isinstance(expected_compression, str):
            lowered = expected_compression.lower()
            expects_alpha = "uncompressed" in lowered and "alpha" in lowered
            expects_luminance = "uncompressed" in lowered and "luminance" in lowered

        if expects_alpha:
            try:
                return self._encode_png_with_pillow(
                    source_path,
                    mode="alpha",
                    expected_label="Uncompressed 8-bit alpha",
                    dds_metadata=dds_metadata,
                )
            except Exception:
                pass
        elif expects_luminance:
            try:
                return self._encode_png_with_pillow(
                    source_path,
                    mode="luminance",
                    expected_label="Uncompressed 8-bit luminance",
                    dds_metadata=dds_metadata,
                )
            except Exception:
                pass

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / f"{source_path.stem}.dds"
            command_line = list(command)
            command_line.append(str(source_path))
            if compression_option:
                command_line.extend(["-define", f"dds:compression={compression_option}"])
            if mipmaps and mipmaps > 0:
                command_line.extend(["-define", f"dds:mipmaps={mipmaps}"])
            command_line.append(str(output_path))

            try:
                completed = subprocess.run(
                    command_line, check=False, capture_output=True, text=True
                )
            except OSError as exc:
                raise RuntimeError(f"Failed to launch ImageMagick: {exc}") from exc

            if completed.returncode != 0:
                detail = completed.stderr.strip() or completed.stdout.strip()
                if not detail:
                    detail = f"ImageMagick exited with status {completed.returncode}"
                raise RuntimeError(detail)

            if not output_path.exists():
                raise RuntimeError("ImageMagick did not produce a DDS file.")

            converted_payload = output_path.read_bytes()
            command_repr = shlex.join(command_line)

        dds_info = _parse_dds_header(converted_payload)
        if expects_alpha and (
            not dds_info
            or dds_info.get("compression") != "Uncompressed 8-bit alpha"
        ):
            if PIL_AVAILABLE and Image is not None:
                return self._encode_png_with_pillow(
                    source_path,
                    mode="alpha",
                    expected_label="Uncompressed 8-bit alpha",
                    dds_metadata=dds_metadata,
                )
            raise RuntimeError(
                "ImageMagick could not preserve the 8-bit alpha format; install Pillow to enable a direct conversion."
            )
        if expects_luminance and (
            not dds_info
            or dds_info.get("compression") != "Uncompressed 8-bit luminance"
        ):
            if PIL_AVAILABLE and Image is not None:
                return self._encode_png_with_pillow(
                    source_path,
                    mode="luminance",
                    expected_label="Uncompressed 8-bit luminance",
                    dds_metadata=dds_metadata,
                )
            raise RuntimeError(
                "ImageMagick could not preserve the 8-bit luminance format; install Pillow to enable a direct conversion."
            )

        if compression_option is None:
            compression_display = "auto"
        elif compression_option.lower() == "none":
            compression_display = "uncompressed"
        else:
            compression_display = compression_option.upper()

        conversion_info = {
            "tool": "ImageMagick",
            "command": command_repr,
            "compression": compression_option or "auto",
            "compression_display": compression_display,
            "mipmaps": mipmaps,
            "bytes": len(converted_payload),
        }

        return converted_payload, conversion_info

    def _prepare_replacement_from_path(
        self,
        initial_entry: Dict[str, object],
        source_path: Path,
    ) -> Dict[str, object] | None:
        try:
            payload = source_path.read_bytes()
        except Exception as exc:  # pragma: no cover - GUI path
            self._show_error("Unable to read replacement", str(exc))
            return None

        source_info: Dict[str, object] = {"path": str(source_path)}
        metadata = self._load_sidecar_metadata(source_path, source_info)

        entry, metadata_note = self._resolve_entry_from_metadata(
            initial_entry, metadata, source_info
        )
        if entry is None:
            return None

        source_info["resolved_relative_path"] = entry["relative_path"]

        conversion_details: Dict[str, object] | None = None
        if source_path.suffix.lower() == ".png":
            try:
                payload, conversion_details = self._transcode_png_to_dds(
                    entry, source_path, metadata
                )
            except RuntimeError as exc:
                self._show_error("Unable to convert texture", str(exc))
                return None
            source_info["conversion"] = conversion_details
            source_info["original_suffix"] = ".png"
            source_info["converted_suffix"] = ".dds"
            source_info["converted_payload_size"] = len(payload)

        self._maybe_prompt_for_backup()
        if not self._confirm_replacement_format(entry, payload, metadata):
            return None

        self._maybe_create_backup(entry, source_path)

        return {
            "entry": entry,
            "payload": payload,
            "metadata": metadata,
            "metadata_note": metadata_note,
            "source_info": source_info,
            "conversion_details": conversion_details,
        }

    def _apply_prepared_replacement(
        self,
        prepared: Dict[str, object],
        *,
        set_focus: bool = True,
    ) -> bool:
        entry = prepared["entry"]
        payload = prepared["payload"]
        metadata = prepared["metadata"]
        metadata_note = prepared["metadata_note"]
        source_info = prepared["source_info"]
        conversion_details = prepared["conversion_details"]

        replacement_record: Dict[str, object] = {
            "payload": payload,
            "source_info": source_info,
        }
        if isinstance(metadata, dict):
            for numeric_key in ("offset", "size"):
                numeric_value = metadata.get(numeric_key)
                if isinstance(numeric_value, int):
                    replacement_record[numeric_key] = numeric_value

        self.replacements[entry["relative_path"]] = replacement_record
        self._refresh_list()

        if set_focus:
            node_id = self.entry_nodes.get(entry["relative_path"])
            if node_id is not None:
                self.tree.selection_set(node_id)
                self.tree.focus(node_id)
                self.tree.see(node_id)
                self.last_activated_path = entry["relative_path"]
                self._on_entry_selected(None)

        metadata_text = metadata_note or ""
        if conversion_details is not None:
            compression_display = (
                conversion_details.get("compression_display")
                or conversion_details.get("compression")
                or "auto"
            )
            mipmaps_value = conversion_details.get("mipmaps")
            detail_parts: List[str] = [str(compression_display)]
            if isinstance(mipmaps_value, int) and mipmaps_value > 0:
                detail_parts.append(f"{mipmaps_value} mipmaps")
            metadata_text += (
                "; converted from PNG via ImageMagick ("
                + ", ".join(detail_parts)
                + ")"
            )

        self._set_status(
            (
                f"Replacement prepared for {entry['relative_path']} "
                f"({len(payload)} bytes){metadata_text}"
            ).strip()
        )
        return True

    def _import_replacement_from_path(
        self,
        initial_entry: Dict[str, object],
        source_path: Path,
        *,
        set_focus: bool = True,
    ) -> bool:
        prepared = self._prepare_replacement_from_path(initial_entry, source_path)
        if prepared is None:
            return False

        if threading.current_thread() is threading.main_thread():
            return self._apply_prepared_replacement(prepared, set_focus=set_focus)

        return bool(
            self._call_in_main_thread(
                self._apply_prepared_replacement, prepared, set_focus=set_focus
            )
        )

    def _build_ui(self) -> None:
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)
        self.root.columnconfigure(1, weight=1)

        list_frame = ttk.Frame(self.root, padding=(10, 10, 5, 10), style="Modern.TFrame")
        list_frame.grid(row=0, column=0, sticky="nsew")

        list_frame.rowconfigure(0, weight=1)
        list_frame.rowconfigure(1, weight=0)
        list_frame.columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            list_frame,
            columns=("status", "size"),
            show="tree headings",
            selectmode="extended",
        )
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.heading("#0", text="Name", anchor="w")
        self.tree.heading("status", text="Status", anchor="w")
        self.tree.heading("size", text="Size", anchor="e")
        self.tree.column("#0", anchor="w", stretch=True, width=360)
        self.tree.column("status", anchor="w", stretch=False, width=140)
        self.tree.column("size", anchor="e", stretch=False, width=140)
        self.tree.bind("<<TreeviewSelect>>", self._on_entry_selected)
        self.tree.bind("<ButtonRelease-1>", self._remember_last_active)
        self.tree.tag_configure("modified", foreground="#d9534f")
        if tk is not None:
            self.tree_menu = tk.Menu(self.tree, tearoff=0)
            self.tree_menu.add_command(
                label="Export Selection", command=self.export_selected
            )
            self.tree_menu.add_separator()
            self.tree_menu.add_command(
                label="Export Selection to DDS", command=self.export_selected_as_dds
            )
            self.tree_menu.add_command(
                label="Export Selection to PNG", command=self.export_selected_as_png
            )
            self.tree.bind("<Button-3>", self._show_tree_context_menu)
            # macOS generates Control-Button-1 instead of Button-3 for context menus.
            self.tree.bind("<Control-Button-1>", self._show_tree_context_menu)

        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)

        control_frame = ttk.Frame(list_frame, style="Modern.TFrame")
        control_frame.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        control_frame.columnconfigure(0, weight=0)
        control_frame.columnconfigure(1, weight=0)
        control_frame.columnconfigure(2, weight=1)
        ttk.Button(
            control_frame,
            text="Expand All",
            command=self._expand_all_nodes,
            style="Accent.TButton",
        ).grid(
            row=0, column=0, padx=2, sticky="ew"
        )
        ttk.Button(
            control_frame,
            text="Collapse All",
            command=self._collapse_all_nodes,
            style="Accent.TButton",
        ).grid(
            row=0, column=1, padx=2, sticky="ew"
        )
        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(control_frame, textvariable=self.search_var)
        search_entry.grid(row=0, column=2, padx=(8, 0), sticky="ew")
        search_entry.bind("<KeyRelease>", self._apply_search_filter)

        preview_frame = ttk.Frame(
            self.root, padding=(5, 10, 10, 10), style="Modern.TFrame"
        )
        preview_frame.grid(row=0, column=1, sticky="nsew", padx=(10, 10), pady=10)
        preview_frame.rowconfigure(0, weight=1)
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.columnconfigure(1, weight=0)

        self.preview_canvas = tk.Canvas(preview_frame, highlightthickness=0)
        self.preview_canvas.grid(row=0, column=0, sticky="nsew")

        self.preview_vscroll = ttk.Scrollbar(
            preview_frame, orient=tk.VERTICAL, command=self.preview_canvas.yview
        )
        self.preview_vscroll.grid(row=0, column=1, sticky="ns", rowspan=2)

        self.preview_hscroll = ttk.Scrollbar(
            preview_frame, orient=tk.HORIZONTAL, command=self.preview_canvas.xview
        )
        self.preview_hscroll.grid(row=1, column=0, sticky="ew")

        self.preview_canvas.configure(
            yscrollcommand=self.preview_vscroll.set,
            xscrollcommand=self.preview_hscroll.set,
        )

        self.preview_inner = tk.Frame(self.preview_canvas)
        self.preview_window = self.preview_canvas.create_window(
            (0, 0), window=self.preview_inner, anchor="nw"
        )
        self.preview_inner.grid_columnconfigure(0, weight=1)
        self.preview_inner.grid_rowconfigure(0, weight=1)

        def _update_scroll_region(event: object) -> None:
            self.preview_canvas.configure(scrollregion=self.preview_canvas.bbox("all"))

        self.preview_inner.bind("<Configure>", _update_scroll_region)

        CTRL_MASK = 0x0004

        def _on_mousewheel(event: object) -> str | None:
            delta = getattr(event, "delta", 0)
            state = getattr(event, "state", 0)
            if state & CTRL_MASK and delta:
                step = int(delta / 120) if delta % 120 == 0 else int(delta / abs(delta))
                if step:
                    self._apply_zoom_steps(step)
                return "break"
            if delta:
                step = int(delta / 120) if delta % 120 == 0 else int(delta / abs(delta))
                if state & 0x0001:  # Shift pressed
                    self.preview_canvas.xview_scroll(-step, "units")
                else:
                    self.preview_canvas.yview_scroll(-step, "units")
                return "break"

        def _on_shift_wheel(event: object) -> str | None:
            delta = getattr(event, "delta", 0)
            if delta:
                step = int(delta / 120) if delta % 120 == 0 else int(delta / abs(delta))
                self.preview_canvas.xview_scroll(-step, "units")
                return "break"

        def _on_wheel_up(event: object) -> str:
            if getattr(event, "state", 0) & CTRL_MASK:
                self._apply_zoom_steps(1)
                return "break"
            self.preview_canvas.yview_scroll(-1, "units")
            return "break"

        def _on_wheel_down(event: object) -> str:
            if getattr(event, "state", 0) & CTRL_MASK:
                self._apply_zoom_steps(-1)
                return "break"
            self.preview_canvas.yview_scroll(1, "units")
            return "break"

        self.preview_canvas.bind("<MouseWheel>", _on_mousewheel)
        self.preview_canvas.bind("<Shift-MouseWheel>", _on_shift_wheel)
        self.preview_canvas.bind("<Button-4>", _on_wheel_up)
        self.preview_canvas.bind("<Button-5>", _on_wheel_down)
        self.preview_canvas.bind("<Control-MouseWheel>", _on_mousewheel)
        self.preview_canvas.bind("<Control-Button-4>", _on_wheel_up)
        self.preview_canvas.bind("<Control-Button-5>", _on_wheel_down)
        self.preview_canvas.bind("<Configure>", self._on_preview_canvas_configure)

        self.preview_content = ttk.Frame(
            self.preview_inner, style="Modern.TFrame"
        )
        self.preview_content.grid(row=0, column=0, sticky="nsew")
        self.preview_content.rowconfigure(0, weight=1)
        self.preview_content.columnconfigure(0, weight=1)

        self.preview_label = ttk.Label(
            self.preview_content,
            text="Select a texture or audio clip to preview",
            anchor="center",
            justify="center",
            style="Modern.TLabel",
        )
        self.preview_label.grid(row=0, column=0, sticky="nsew")
        self.preview_grid_frame = ttk.Frame(
            self.preview_content, style="Modern.TFrame"
        )
        self.preview_grid_frame.grid(row=0, column=0, sticky="nsew")
        self.preview_grid_frame.grid_remove()
        self.preview_info_label = ttk.Label(
            self.preview_inner,
            text="",
            anchor="center",
            justify="center",
            font=(None, 9),
            style="Modern.TLabel",
        )
        self.preview_info_label.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.preview_image = None
        self.preview_source_image = None

        self.audio_controls_frame = ttk.Frame(
            self.preview_inner, style="Modern.TFrame"
        )
        self.audio_controls_frame.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        for column in range(3):
            self.audio_controls_frame.columnconfigure(column, weight=1 if column == 2 else 0)

        self.audio_metadata_var = tk.StringVar(value="")
        self.audio_status_var = tk.StringVar(value="")

        self.audio_metadata_label = ttk.Label(
            self.audio_controls_frame,
            textvariable=self.audio_metadata_var,
            anchor="w",
            justify="left",
            style="Modern.TLabel",
        )
        self.audio_metadata_label.grid(
            row=0, column=0, columnspan=3, sticky="ew", pady=(0, 2)
        )

        self.audio_play_button = ttk.Button(
            self.audio_controls_frame,
            text="Play",
            command=self._play_audio_preview,
            style="Accent.TButton",
        )
        self.audio_play_button.grid(row=1, column=0, padx=(0, 8), sticky="w")

        self.audio_stop_button = ttk.Button(
            self.audio_controls_frame,
            text="Stop",
            command=self._stop_audio_preview,
            style="Accent.TButton",
        )
        self.audio_stop_button.grid(row=1, column=1, padx=(0, 8), sticky="w")

        self.audio_status_label = ttk.Label(
            self.audio_controls_frame,
            textvariable=self.audio_status_var,
            anchor="w",
            justify="left",
            font=(None, 9),
            style="Modern.TLabel",
        )
        self.audio_status_label.grid(row=1, column=2, sticky="w")

        self.audio_controls_frame.grid_remove()

        channel_frame = ttk.Frame(preview_frame, style="Modern.TFrame")
        channel_frame.grid(row=2, column=0, columnspan=2, sticky="ew", padx=4, pady=(6, 2))
        for column in range(4):
            channel_frame.columnconfigure(column, weight=1)

        self.channel_vars = {}
        self.channel_buttons = {}
        self._channel_default_state = {}
        for idx, channel in enumerate("RGBA"):
            default_value = 0 if channel == "A" else 1
            var = tk.IntVar(value=default_value)
            self._channel_default_state[channel] = default_value
            btn = ttk.Checkbutton(
                channel_frame,
                text=channel,
                variable=var,
                command=self._update_channel_preview,
            )
            btn.grid(row=0, column=idx, padx=2)
            self.channel_vars[channel] = var
            self.channel_buttons[channel] = btn
        self._set_channel_controls_state("disabled")

        button_frame = ttk.Frame(self.root, padding=(10, 0, 10, 8), style="Modern.TFrame")
        button_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=8)
        for i in range(7):
            button_frame.columnconfigure(i, weight=1)

        ttk.Button(
            button_frame,
            text="Open Archive",
            command=self.open_archive,
            style="Accent.TButton",
        ).grid(row=0, column=0, padx=4)
        ttk.Button(
            button_frame,
            text="Export Selected",
            command=self.export_selected,
            style="Accent.TButton",
        ).grid(row=0, column=1, padx=4)
        ttk.Button(
            button_frame,
            text="Export All",
            command=self.export_all,
            style="Accent.TButton",
        ).grid(row=0, column=2, padx=4)
        ttk.Button(
            button_frame,
            text="Import Replacement",
            command=self.import_replacement,
            style="Accent.TButton",
        ).grid(row=0, column=3, padx=4)
        ttk.Button(
            button_frame,
            text="Import Folder",
            command=self.import_folder,
            style="Accent.TButton",
        ).grid(row=0, column=4, padx=4)
        ttk.Button(
            button_frame,
            text="Create Patch",
            command=self.create_patch_archive,
            style="Accent.TButton",
        ).grid(row=0, column=5, padx=4)
        ttk.Button(
            button_frame,
            text="Load Modification Log",
            command=self.load_replacement_log,
            style="Accent.TButton",
        ).grid(row=0, column=6, padx=4)

        ttk.Checkbutton(
            button_frame,
            text="Create automatic backup",
            variable=self.backup_var,
            style="Modern.TCheckbutton",
        ).grid(row=1, column=0, columnspan=6, sticky="w", padx=4, pady=(6, 0))

        self.status = tk.StringVar(
            value=(
                "Select an archive to begin. "
                "'Create Patch' exports only modified files and writes a reusable log."
            )
        )
        status_bar = ttk.Label(
            self.root, textvariable=self.status, anchor="w", style="Status.TLabel"
        )
        status_bar.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 8))

    # ------------------------------------------------------------- utilities --
    def _refresh_list(self) -> None:
        if not hasattr(self, "tree"):
            return

        self._cancel_pending_tree_insert()

        try:
            selected_nodes = list(self.tree.selection())
        except tk.TclError:
            selected_nodes = []

        selected_paths: List[str] = []
        for node_id in selected_nodes:
            entry = self.node_to_entry.get(node_id)
            if entry is not None:
                relative_path = entry.get("relative_path")
                if isinstance(relative_path, str):
                    selected_paths.append(relative_path)

        if self._expand_all_on_refresh:
            expanded_paths: Set[Tuple[str, ...]] | None = None
        else:
            expanded_paths = {
                path
                for node_id, path in self.node_to_path.items()
                if path
                and self.tree.exists(node_id)
                and self.tree.item(node_id, "open")
            }

        entries_snapshot = list(self.entries)
        replacements_snapshot = dict(self.replacements)
        expand_all = self._expand_all_on_refresh

        self._tree_refresh_generation += 1
        generation = self._tree_refresh_generation

        def build_model() -> List[Dict[str, object]]:
            return TextureManagerGUI._build_tree_model_snapshot(
                entries_snapshot,
                replacements_snapshot,
                expanded_paths,
                expand_all,
            )

        future: Future[List[Dict[str, object]]] = self._tree_executor.submit(build_model)

        def on_done(task: Future[List[Dict[str, object]]]) -> None:
            try:
                nodes = task.result()
            except Exception as exc:  # pragma: no cover - background thread errors
                self.root.after(0, self._handle_tree_model_failure, generation, exc)
                return
            self.root.after(
                0, self._apply_tree_model, generation, nodes, selected_paths
            )

        future.add_done_callback(on_done)

    @staticmethod
    def _build_tree_model_snapshot(
        entries: Sequence[Dict[str, object]],
        replacements: Dict[str, Dict[str, object]],
        expanded_paths: Set[Tuple[str, ...]] | None,
        expand_all: bool,
    ) -> List[Dict[str, object]]:
        category_order = tuple(CATEGORY_LABELS.keys())
        nodes: List[Dict[str, object]] = []
        folder_nodes: Set[Tuple[str, ...]] = set()

        entries_by_category: Dict[str, List[Dict[str, object]]] = {
            key: [] for key in category_order
        }
        for entry in entries:
            relative_path_value = entry.get("relative_path")
            if not isinstance(relative_path_value, str):
                continue
            category_key = "other"
            if is_image_entry(relative_path_value):
                category_key = "textures"
            elif is_audio_entry(relative_path_value):
                category_key = "audio"
            entries_by_category.setdefault(category_key, []).append(entry)

        for category_key in category_order:
            category_entries = entries_by_category.get(category_key)
            if not category_entries:
                continue

            category_path = (category_key,)
            should_open = expand_all or (
                expanded_paths is not None and category_path in expanded_paths
            )
            nodes.append(
                {
                    "kind": "category",
                    "path": category_path,
                    "parent": (),
                    "text": CATEGORY_LABELS.get(category_key, category_key.title()),
                    "open": should_open,
                }
            )

            for entry in category_entries:
                relative_path = entry.get("relative_path")
                if not isinstance(relative_path, str):
                    continue
                segments = [segment for segment in relative_path.split("/") if segment]
                parent_path = category_path
                if segments[:-1]:
                    parent_path = TextureManagerGUI._ensure_folder_node(
                        nodes,
                        folder_nodes,
                        category_key,
                        segments[:-1],
                        expanded_paths,
                        expand_all,
                    )
                name_display, size_text, status_text, tags = (
                    TextureManagerGUI._compute_entry_row_metadata(
                        entry, replacements.get(relative_path)
                    )
                )
                if segments:
                    path_tuple = (category_key,) + tuple(segments)
                else:
                    path_tuple = (category_key, relative_path)
                nodes.append(
                    {
                        "kind": "entry",
                        "path": path_tuple,
                        "parent": parent_path,
                        "text": name_display,
                        "status_text": status_text,
                        "size_text": size_text,
                        "tags": tags,
                        "relative_path": relative_path,
                        "entry": entry,
                    }
                )

        return nodes

    @staticmethod
    def _ensure_folder_node(
        nodes: List[Dict[str, object]],
        folder_nodes: Set[Tuple[str, ...]],
        category_key: str,
        path_segments: Sequence[str],
        expanded_paths: Set[Tuple[str, ...]] | None,
        expand_all: bool,
    ) -> Tuple[str, ...]:
        if not path_segments:
            return (category_key,)

        key = (category_key,) + tuple(path_segments)
        if key in folder_nodes:
            return key

        parent_segments = path_segments[:-1]
        parent_key = (
            TextureManagerGUI._ensure_folder_node(
                nodes, folder_nodes, category_key, parent_segments, expanded_paths, expand_all
            )
            if parent_segments
            else (category_key,)
        )
        should_open = expand_all or (
            expanded_paths is not None and key in expanded_paths
        )
        nodes.append(
            {
                "kind": "folder",
                "path": key,
                "parent": parent_key,
                "text": path_segments[-1],
                "open": should_open,
            }
        )
        folder_nodes.add(key)
        return key

    @staticmethod
    def _compute_entry_row_metadata(
        entry: Dict[str, object],
        replacement: Dict[str, object] | bytes | None,
    ) -> Tuple[str, str, str, Tuple[str, ...]]:
        relative_path = entry.get("relative_path", "")
        name_display = relative_path.rsplit("/", 1)[-1] if relative_path else ""
        size_value = int(entry.get("size", 0))
        size_text = f"{size_value} bytes"
        status_text = ""
        tags: Tuple[str, ...] = ()

        if replacement is None:
            return name_display, size_text, status_text, tags

        replacement_payload = _extract_replacement_payload(replacement)
        if replacement_payload is not None:
            replacement_size = len(replacement_payload)
            if replacement_size != size_value:
                size_text += f" → {replacement_size} bytes"
            else:
                size_text += " → replacement"
        else:
            size_text += " → (missing payload)"
        name_display = f"{name_display} *" if name_display else "*"
        tags = ("modified",)
        status_parts: List[str] = ["Modified"]
        if isinstance(replacement, dict):
            source_info = replacement.get("source_info")
            if isinstance(source_info, dict):
                if isinstance(source_info.get("metadata"), dict):
                    status_parts.append("rsmeta")
                elif isinstance(source_info.get("path"), str):
                    origin_name = os.path.basename(source_info["path"])
                    if origin_name:
                        status_parts.append(origin_name)
        status_text = " • ".join(status_parts)
        return name_display, size_text, status_text, tags

    def _cancel_pending_tree_insert(self) -> None:
        if self._tree_insert_job is None:
            return
        try:
            self.root.after_cancel(self._tree_insert_job)
        except Exception:
            pass
        self._tree_insert_job = None

    def _handle_tree_model_failure(self, generation: int, exc: Exception) -> None:
        if generation != self._tree_refresh_generation:
            return
        self._set_status(f"Failed to refresh entries: {exc}")

    def _apply_tree_model(
        self,
        generation: int,
        nodes: Sequence[Dict[str, object]],
        selected_paths: Sequence[str],
    ) -> None:
        if generation != self._tree_refresh_generation:
            return
        if not hasattr(self, "tree"):
            return

        self._cancel_pending_tree_insert()

        try:
            children = self.tree.get_children("")
        except tk.TclError:
            children = ()
        if children:
            try:
                self.tree.delete(*children)
            except tk.TclError:
                return

        self.node_to_entry.clear()
        self.entry_nodes.clear()
        self.node_to_path.clear()

        path_to_id: Dict[Tuple[str, ...], str] = {}
        node_list = list(nodes)
        total = len(node_list)

        def process_chunk(index: int) -> None:
            if generation != self._tree_refresh_generation:
                return
            end = min(index + 200, total)
            for idx in range(index, end):
                node = node_list[idx]
                kind = node.get("kind")
                path = node.get("path")
                parent_path = node.get("parent")
                try:
                    if kind == "category":
                        node_id = self.tree.insert(
                            "",
                            "end",
                            text=node.get("text", ""),
                            values=("", ""),
                            open=bool(node.get("open")),
                        )
                    elif kind == "folder":
                        parent_id = path_to_id.get(parent_path)
                        if parent_id is None:
                            continue
                        node_id = self.tree.insert(
                            parent_id,
                            "end",
                            text=node.get("text", ""),
                            values=("", ""),
                            open=bool(node.get("open")),
                        )
                    else:
                        parent_id = path_to_id.get(parent_path)
                        if parent_id is None:
                            continue
                        node_id = self.tree.insert(
                            parent_id,
                            "end",
                            text=node.get("text", ""),
                            values=(
                                node.get("status_text", ""),
                                node.get("size_text", ""),
                            ),
                            tags=node.get("tags", ()),
                        )
                        entry = node.get("entry")
                        relative_path = node.get("relative_path")
                        if isinstance(relative_path, str) and isinstance(entry, dict):
                            self.node_to_entry[node_id] = entry
                            self.entry_nodes[relative_path] = node_id
                    if isinstance(path, tuple):
                        path_to_id[path] = node_id
                        self.node_to_path[node_id] = path
                except tk.TclError:
                    continue

            if end < total:
                self._tree_insert_job = self.root.after(1, process_chunk, end)
            else:
                self._tree_insert_job = None
                self._finalise_tree_selection(selected_paths)

        process_chunk(0)

    def _finalise_tree_selection(self, selected_paths: Sequence[str]) -> None:
        try:
            current_selection = self.tree.selection()
        except tk.TclError:
            current_selection = ()

        new_selection = [
            self.entry_nodes[path]
            for path in selected_paths
            if path in self.entry_nodes
        ]

        if new_selection:
            try:
                self.tree.selection_set(new_selection)
                focus_node = new_selection[-1]
                self.tree.focus(focus_node)
                self.tree.see(focus_node)
            except tk.TclError:
                pass
        else:
            try:
                if current_selection:
                    self.tree.selection_remove(current_selection)
            except tk.TclError:
                pass

        if self.last_activated_path and self.last_activated_path not in self.entry_nodes:
            self.last_activated_path = None

        self._expand_all_on_refresh = False

    def _apply_search_filter(self, _event: object | None = None) -> None:
        if not hasattr(self, "tree"):
            return

        query = ""
        if hasattr(self, "search_var"):
            query = self.search_var.get().strip().lower()

        selected_nodes = set()
        focus_path = None
        previous_last_activated = self.last_activated_path

        try:
            for node_id in self.tree.selection():
                entry = self.node_to_entry.get(node_id)
                if entry is not None:
                    selected_nodes.add(entry["relative_path"])
        except tk.TclError:
            selected_nodes = set()

        try:
            focus_node = self.tree.focus()
        except tk.TclError:
            focus_node = ""
        if focus_node in self.node_to_entry:
            focus_path = self.node_to_entry[focus_node]["relative_path"]

        if not query:
            filtered = list(self.previewable_entries)
        else:
            filtered = [
                entry
                for entry in self.supported_entries
                if query in entry["relative_path"].lower()
            ]

        self.entries = filtered
        self._refresh_list()

        if not filtered:
            self._clear_preview("No entries match the current search")
            return

        restored_paths: List[str] = [
            path for path in selected_nodes if path in self.entry_nodes
        ]
        if not restored_paths and focus_path and focus_path in self.entry_nodes:
            restored_paths.append(focus_path)
        if (
            not restored_paths
            and previous_last_activated
            and previous_last_activated in self.entry_nodes
        ):
            restored_paths.append(previous_last_activated)

        if restored_paths:
            unique_paths: List[str] = []
            seen: set[str] = set()
            for path in restored_paths:
                if path not in seen:
                    seen.add(path)
                    unique_paths.append(path)
            node_ids = [self.entry_nodes[path] for path in unique_paths]
            try:
                self.tree.selection_set(node_ids)
            except tk.TclError:
                node_ids = []
            focus_candidate = None
            if focus_path and focus_path in self.entry_nodes:
                focus_candidate = self.entry_nodes[focus_path]
            elif (
                previous_last_activated
                and previous_last_activated in self.entry_nodes
            ):
                focus_candidate = self.entry_nodes[previous_last_activated]
            elif node_ids:
                focus_candidate = node_ids[-1]
            if focus_candidate:
                try:
                    self.tree.focus(focus_candidate)
                    self.tree.see(focus_candidate)
                except tk.TclError:
                    pass
            self._on_entry_selected(None)
            return

        try:
            current_selection = self.tree.selection()
        except tk.TclError:
            current_selection = ()
        if current_selection:
            try:
                self.tree.selection_remove(current_selection)
            except tk.TclError:
                pass
        self._clear_preview()

    def _set_status(self, message: str) -> None:
        if threading.current_thread() is threading.main_thread():
            self.status.set(message)
            return
        self._call_in_main_thread(self.status.set, message)

    def _update_preview_scrollregion(self, reset_position: bool = False) -> None:
        if not hasattr(self, "preview_canvas"):
            return
        bbox = self.preview_canvas.bbox("all")
        if bbox is None:
            bbox = (0, 0, 0, 0)
        self.preview_canvas.configure(scrollregion=bbox)
        if reset_position:
            self.preview_canvas.xview_moveto(0)
            self.preview_canvas.yview_moveto(0)

    def _ensure_single_preview_mode(self) -> None:
        if hasattr(self, "preview_grid_frame"):
            self.preview_grid_frame.grid_remove()
        if hasattr(self, "preview_label"):
            self.preview_label.grid()

    def _ensure_grid_mode(self) -> None:
        if hasattr(self, "preview_label"):
            self.preview_label.grid_remove()
        if hasattr(self, "preview_grid_frame"):
            self.preview_grid_frame.grid()

    def _clear_thumbnail_grid(self) -> None:
        if not hasattr(self, "preview_grid_frame"):
            return
        for child in list(self.preview_grid_frame.winfo_children()):
            child.destroy()
        self.thumbnail_images = []

    def _on_preview_canvas_configure(self, _event: object) -> None:
        if self.preview_display_base is None:
            return
        self._update_preview_photo()

    def _get_preview_viewport_size(self) -> Tuple[int, int]:
        if not hasattr(self, "preview_canvas"):
            return (0, 0)
        width = self.preview_canvas.winfo_width()
        height = self.preview_canvas.winfo_height()
        if width <= 1 or height <= 1:
            self.preview_canvas.update_idletasks()
            width = self.preview_canvas.winfo_width()
            height = self.preview_canvas.winfo_height()
        width = max(width - 16, 1)
        height = max(height - 16, 1)
        return width, height

    def _update_preview_photo(self, reset_zoom: bool = False) -> None:
        if not (PIL_IMAGETK_AVAILABLE and self.preview_display_base is not None):
            return

        viewport_width, viewport_height = self._get_preview_viewport_size()
        base_width, base_height = self.preview_display_base.size

        if base_width <= 0 or base_height <= 0:
            return

        fit_scale = 1.0
        if viewport_width > 0 and viewport_height > 0:
            fit_scale = min(viewport_width / base_width, viewport_height / base_height, 1.0)

        if reset_zoom or self._fit_scale is None:
            self._fit_scale = fit_scale
            self._current_zoom = 1.0
        else:
            effective_scale = (self._fit_scale or 1.0) * self._current_zoom
            if fit_scale > 0:
                self._fit_scale = fit_scale
                adjusted_zoom = effective_scale / fit_scale
                self._current_zoom = max(0.1, min(adjusted_zoom, 10.0))
            else:
                fit_scale = self._fit_scale or 1.0

        effective_scale = max((self._fit_scale or 1.0) * self._current_zoom, 0.05)
        target_width = max(1, int(round(base_width * effective_scale)))
        target_height = max(1, int(round(base_height * effective_scale)))

        if target_width != base_width or target_height != base_height:
            display_image = self.preview_display_base.resize(
                (target_width, target_height), Image.LANCZOS
            )
        else:
            display_image = self.preview_display_base

        photo = _prepare_photo_image(display_image)
        self._ensure_single_preview_mode()
        self.preview_label.configure(image=photo, text="")
        self.preview_label.image = photo
        self.preview_image = photo
        self._update_preview_scrollregion(reset_position=reset_zoom)

    def _apply_zoom_steps(self, steps: int) -> None:
        if self.preview_display_base is None:
            return
        if steps == 0:
            return
        new_zoom = self._current_zoom * (1.1 ** steps)
        new_zoom = max(0.1, min(new_zoom, 10.0))
        if math.isclose(new_zoom, self._current_zoom, rel_tol=1e-4):
            return
        self._current_zoom = new_zoom
        self._update_preview_photo()

    def _entries_for_folder(self, node_id: str) -> List[Dict[str, object]]:
        path_tuple = self.node_to_path.get(node_id, ())
        if path_tuple and path_tuple[0] in CATEGORY_LABELS:
            path_segments = path_tuple[1:]
        else:
            path_segments = path_tuple
        prefix = "/".join(path_segments)
        if prefix:
            prefix = prefix.rstrip("/") + "/"

        entries: List[Dict[str, object]] = []
        for entry in self.entries:
            relative_path = entry.get("relative_path")
            if not isinstance(relative_path, str):
                continue
            if prefix:
                if not relative_path.startswith(prefix):
                    continue
                remainder = relative_path[len(prefix) :]
            else:
                remainder = relative_path
            if not remainder or "/" in remainder:
                continue
            entries.append(entry)
        return entries

    def _activate_grid_entry(self, entry: Dict[str, object]) -> None:
        relative_path = entry.get("relative_path")
        if not isinstance(relative_path, str):
            return
        node_id = self.entry_nodes.get(relative_path)
        if not node_id:
            return
        try:
            self.tree.selection_set((node_id,))
            self.tree.focus(node_id)
            self.tree.see(node_id)
        except tk.TclError:
            return
        self._on_entry_selected(None)

    def _show_folder_grid(self, node_id: str) -> None:
        if not (PIL_AVAILABLE and PIL_IMAGETK_AVAILABLE):
            self._clear_preview("Install Pillow to preview textures")
            self._set_status(
                "Grid view unavailable: install Pillow with ImageTk support"
            )
            return

        if self.archive_bytes is None:
            return

        entries = [
            entry
            for entry in self._entries_for_folder(node_id)
            if is_image_entry(entry.get("relative_path", ""))
        ]
        self._cancel_pending_preview()
        self._pending_channel_state = None
        self.preview_source_image = None
        self.preview_display_base = None
        self.preview_image = None
        self._set_channel_controls_state("disabled")
        self._ensure_grid_mode()
        self._clear_thumbnail_grid()

        if not entries:
            message = "This folder does not contain visible textures"
            label = ttk.Label(
                self.preview_grid_frame,
                text=message,
                anchor="center",
                justify="center",
                style="Modern.TLabel",
            )
            label.grid(row=0, column=0, padx=12, pady=12)
            self._set_status(message)
            if hasattr(self, "preview_info_label") and hasattr(
                self.preview_info_label, "configure"
            ):
                self.preview_info_label.configure(text="")
            self._update_preview_scrollregion(reset_position=True)
            return

        columns = 3
        for column in range(columns):
            self.preview_grid_frame.columnconfigure(column, weight=1)

        for index, entry in enumerate(entries):
            payload = self.archive_bytes[entry["offset"] : entry["offset"] + entry["size"]]
            replacement_value = self.replacements.get(entry["relative_path"])
            replacement_payload = _extract_replacement_payload(replacement_value)
            if replacement_payload is not None:
                payload = replacement_payload

            try:
                preview_data = _prepare_preview_image(payload)
            except Exception:
                continue

            display_image = preview_data.get("display_image")
            if display_image is None:
                continue

            thumb = display_image.copy()
            thumb.thumbnail((192, 192), Image.LANCZOS)
            try:
                photo = _prepare_photo_image(thumb)
            except Exception:
                continue

            self.thumbnail_images.append(photo)
            tile = ttk.Button(
                self.preview_grid_frame,
                image=photo,
                text=Path(entry["relative_path"]).name,
                compound="top",
                command=lambda e=entry: self._activate_grid_entry(e),
                style="Accent.TButton",
            )
            tile.grid(row=index // columns, column=index % columns, padx=8, pady=8, sticky="nsew")
            tile.bind(
                "<Double-Button-1>", lambda _event, e=entry: self._activate_grid_entry(e)
            )

        for row in range((len(entries) + columns - 1) // columns):
            self.preview_grid_frame.rowconfigure(row, weight=1)

        path_tuple = self.node_to_path.get(node_id, ())
        if path_tuple:
            category_key = path_tuple[0]
            label = CATEGORY_LABELS.get(category_key, category_key.title())
            if len(path_tuple) > 1:
                display_segments = [label, *path_tuple[1:]]
            else:
                display_segments = [label]
            folder_path = "/".join(display_segments)
        else:
            folder_path = "root"
        status = f"{len(entries)} textura(s) en {folder_path}"
        self._set_status(status)
        if hasattr(self, "preview_info_label") and hasattr(self.preview_info_label, "configure"):
            self.preview_info_label.configure(
                text="Haz doble clic en una miniatura para abrir la textura"
            )
        self._update_preview_scrollregion(reset_position=True)

    def _invalidate_preview_request(self) -> int:
        """Bump the preview request identifier and return the new value."""

        self._preview_request_id += 1
        return self._preview_request_id

    def _cancel_pending_preview(self) -> None:
        if self._preview_task is not None:
            self._invalidate_preview_request()
            with contextlib.suppress(Exception):
                self._preview_task.cancel()
        self._preview_task = None
        self._stop_audio_preview()

    def _update_audio_controls(self) -> None:
        if not hasattr(self, "audio_play_button"):
            return
        preview_ready = self._audio_preview_data is not None
        play_state = "disabled"
        backend_ready = _sounddevice_backend_available()
        if preview_ready and backend_ready and not self._audio_is_playing:
            play_state = "normal"
        stop_state = "normal" if preview_ready and self._audio_is_playing else "disabled"
        with contextlib.suppress(tk.TclError):
            self.audio_play_button.configure(state=play_state)
            self.audio_stop_button.configure(state=stop_state)

    def _stop_audio_preview(self) -> None:
        if self._audio_poll_job is not None and hasattr(self, "root"):
            with contextlib.suppress(tk.TclError):
                self.root.after_cancel(self._audio_poll_job)
        self._audio_poll_job = None
        using_sounddevice = isinstance(self._audio_play_obj, threading.Thread)
        if using_sounddevice and _sounddevice_backend_available():
            self._sounddevice_stop_requested = True
            with contextlib.suppress(Exception):  # pragma: no cover - depends on backend.
                sounddevice.stop()  # type: ignore[union-attr]
        if not using_sounddevice:
            self._audio_play_obj = None
        self._audio_is_playing = False
        self._update_audio_controls()

    def _poll_audio_playback(self) -> None:
        if self._audio_play_obj is None:
            self._audio_poll_job = None
            self._audio_is_playing = False
            self._update_audio_controls()
            return
        if isinstance(self._audio_play_obj, threading.Thread):
            if self._audio_play_obj.is_alive():
                if hasattr(self, "root"):
                    self._audio_poll_job = self.root.after(150, self._poll_audio_playback)
                return
            self._audio_poll_job = None
            return
        is_playing = False
        poll_method = getattr(self._audio_play_obj, "is_playing", None)
        if callable(poll_method):
            with contextlib.suppress(Exception):
                is_playing = bool(poll_method())
        if is_playing:
            if hasattr(self, "root"):
                self._audio_poll_job = self.root.after(150, self._poll_audio_playback)
            return
        self._audio_play_obj = None
        self._audio_is_playing = False
        self._audio_poll_job = None
        self._update_audio_controls()
        if self._audio_preview_data and hasattr(self, "status"):
            duration = self._audio_preview_data.get("duration")
            if isinstance(duration, (int, float)) and duration:
                self._set_status(
                    f"Playback finished ({duration:.2f} s)"
                )

    def _on_sounddevice_playback_complete(
        self, thread: threading.Thread, error: Exception | None
    ) -> None:
        if self._audio_play_obj is not thread:
            return
        self._audio_play_obj = None
        was_stopped = self._sounddevice_stop_requested
        self._sounddevice_stop_requested = False
        self._audio_is_playing = False
        self._audio_poll_job = None
        if error is not None:
            self.audio_status_var.set(f"Unable to start playback: {error}")
            self._set_status(f"Unable to start audio playback: {error}")
        elif was_stopped:
            self.audio_status_var.set("Playback stopped")
            self._set_status("Audio preview stopped")
        else:
            self.audio_status_var.set("Ready to play")
            if self._audio_preview_data:
                duration = self._audio_preview_data.get("duration")
                if isinstance(duration, (int, float)) and duration:
                    self._set_status(f"Playback finished ({duration:.2f} s)")
                else:
                    self._set_status("Playback finished")
        self._update_audio_controls()

    def _play_audio_preview(self) -> None:
        if not self._audio_preview_data:
            return
        sounddevice_ready = _sounddevice_backend_available()
        if not sounddevice_ready:
            self.audio_status_var.set(
                "Audio playback unavailable: install sounddevice (pip install sounddevice numpy)."
            )
            self._set_status(
                "Audio playback unavailable: install sounddevice (pip install sounddevice numpy)."
            )
            self._update_audio_controls()
            return

        pcm = self._audio_preview_data.get("pcm")
        sample_rate = int(self._audio_preview_data.get("sample_rate", 0))
        sample_width = int(self._audio_preview_data.get("sample_width", 0))
        channels = int(self._audio_preview_data.get("channels", 0))
        if (
            not isinstance(pcm, (bytes, bytearray))
            or sample_rate <= 0
            or sample_width <= 0
            or channels <= 0
        ):
            self.audio_status_var.set("Audio preview is missing required metadata.")
            self._update_audio_controls()
            return

        try:
            audio_array = _pcm_to_float32(bytes(pcm), sample_width, channels)
        except Exception as exc:
            self.audio_status_var.set(f"Unable to start playback: {exc}")
            self._set_status(f"Unable to start audio playback: {exc}")
            self._update_audio_controls()
            return

        self._stop_audio_preview()
        self._sounddevice_stop_requested = False

        def _play_with_sounddevice() -> None:
            error: Exception | None = None
            try:
                sounddevice.stop()  # type: ignore[union-attr]
                sounddevice.play(audio_array, sample_rate, blocking=True)  # type: ignore[union-attr]
            except Exception as playback_exc:  # pragma: no cover - backend specific.
                error = playback_exc
            finally:
                if hasattr(self, "root"):
                    self.root.after(
                        0,
                        self._on_sounddevice_playback_complete,
                        threading.current_thread(),
                        error,
                    )

        playback_thread = threading.Thread(
            target=_play_with_sounddevice,
            name="rsfl-sounddevice-playback",
            daemon=True,
        )
        self._audio_play_obj = playback_thread
        self._audio_is_playing = True
        self.audio_status_var.set("Playing…")
        self._set_status("Playing audio preview…")
        self._update_audio_controls()
        playback_thread.start()
    def _clear_preview(self, message: str | None = None) -> None:
        self._cancel_pending_preview()
        self._pending_channel_state = None
        if message is None:
            message = "Select a texture or audio clip to preview"
        self._ensure_single_preview_mode()
        self._clear_thumbnail_grid()
        self._stop_audio_preview()
        self._audio_preview_data = None
        self._audio_is_playing = False
        self.audio_metadata_var.set("")
        self.audio_status_var.set("")
        if hasattr(self, "audio_controls_frame"):
            self.audio_controls_frame.grid_remove()
        if hasattr(self.preview_label, "configure"):
            self.preview_label.configure(image="", text=message)
            self.preview_label.image = None
        if hasattr(self, "preview_info_label") and hasattr(
            self.preview_info_label, "configure"
        ):
            self.preview_info_label.configure(text="")
        self._update_preview_scrollregion(reset_position=True)
        self.preview_image = None
        self.preview_source_image = None
        self.preview_display_base = None
        self._fit_scale = None
        self._current_zoom = 1.0
        self._set_channel_controls_state("disabled")
        self.last_activated_path = None
        self._preview_task_kind = "image"

    def _shutdown(self) -> None:
        self._cancel_pending_preview()
        self._cancel_pending_tree_insert()
        cancel_event = self._folder_import_cancel_event
        if cancel_event is not None and not cancel_event.is_set():
            cancel_event.set()
        if self._folder_import_thread and self._folder_import_thread.is_alive():
            with contextlib.suppress(Exception):
                self._folder_import_thread.join(timeout=0.5)
        with contextlib.suppress(Exception):
            self._preview_executor.shutdown(wait=False)
        with contextlib.suppress(Exception):
            self._tree_executor.shutdown(wait=False)
        _release_archive_buffer(self.archive_bytes)
        self.archive_bytes = None
        self._cleanup_drag_tempdir()
        self.root.destroy()

    def _remember_last_active(self, event: object) -> None:
        if not hasattr(event, "y"):
            return
        if not hasattr(self, "tree"):
            return
        try:
            node_id = self.tree.identify_row(event.y)
        except tk.TclError:
            return
        if node_id and node_id in self.node_to_entry:
            entry = self.node_to_entry[node_id]
            self.last_activated_path = entry["relative_path"]

    def _show_tree_context_menu(self, event: object) -> None:
        if not hasattr(self, "tree"):
            return
        menu = getattr(self, "tree_menu", None)
        if menu is None:
            return
        try:
            node_id = self.tree.identify_row(getattr(event, "y", 0))
        except tk.TclError:
            node_id = ""
        if node_id:
            try:
                current_selection = self.tree.selection()
            except tk.TclError:
                current_selection = ()
            if node_id not in current_selection:
                try:
                    self.tree.selection_set(node_id)
                    self.tree.focus(node_id)
                except tk.TclError:
                    pass
        try:
            menu.tk_popup(getattr(event, "x_root", 0), getattr(event, "y_root", 0))
        finally:
            with contextlib.suppress(Exception):
                menu.grab_release()

    def _expand_all_nodes(self) -> None:
        for node_id, path in list(self.node_to_path.items()):
            if not path or node_id in self.node_to_entry:
                continue
            try:
                self.tree.item(node_id, open=True)
            except tk.TclError:
                continue

    def _collapse_all_nodes(self) -> None:
        for node_id, path in list(self.node_to_path.items()):
            if not path or node_id in self.node_to_entry:
                continue
            try:
                self.tree.item(node_id, open=False)
            except tk.TclError:
                continue

    def _export_entries(
        self,
        destination_path: Path,
        entries: Sequence[Dict[str, object]],
        *,
        format_override: str | None = None,
    ) -> Tuple[int, List[Tuple[str, str]], List[str]]:
        count = 0
        renamed: List[Tuple[str, str]] = []
        errors: List[str] = []
        source_archive = self.archive_path.name if self.archive_path else None
        for entry in entries:
            payload = self.archive_bytes[entry["offset"] : entry["offset"] + entry["size"]]
            relative_path = entry["relative_path"]
            replacement_value = self.replacements.get(relative_path)
            replacement_payload = _extract_replacement_payload(replacement_value)
            if replacement_payload is not None:
                payload = replacement_payload
            export_relative: str | None
            original_relative: str | None
            output_bytes: bytes = payload
            metadata_extra: Dict[str, object] = {}
            is_texture = is_image_entry(relative_path)

            dds_info = _parse_dds_header(payload)
            if dds_info is not None:
                dds_metadata: Dict[str, object] = {}
                compression_value = dds_info.get("compression")
                if compression_value:
                    dds_metadata["compression"] = compression_value
                fourcc_value = dds_info.get("fourcc")
                if fourcc_value:
                    dds_metadata["fourcc"] = fourcc_value
                dxgi_value = dds_info.get("dxgi_format")
                if isinstance(dxgi_value, int):
                    dds_metadata["dxgi_format"] = dxgi_value
                detected_mipmaps = dds_info.get("mipmaps")
                requested_mipmaps = 10
                if isinstance(detected_mipmaps, int) and detected_mipmaps > 0:
                    dds_metadata["detected_mipmaps"] = detected_mipmaps
                    requested_mipmaps = max(requested_mipmaps, detected_mipmaps)
                dds_metadata["mipmaps"] = requested_mipmaps
                if dds_metadata:
                    metadata_extra["dds"] = dds_metadata

            if format_override == "png" and is_texture:
                if not PIL_AVAILABLE or Image is None:
                    errors.append(
                        f"{relative_path}: install Pillow to export textures as PNG"
                    )
                    continue
                try:
                    preview_data = _prepare_preview_image(payload)
                    image = preview_data["image"]
                except Exception as exc:  # pragma: no cover - depends on payload
                    errors.append(
                        f"{relative_path}: unable to convert texture to PNG ({exc})"
                    )
                    continue
                if image.mode != "RGBA":
                    try:
                        image = image.convert("RGBA")
                    except Exception as exc:  # pragma: no cover - defensive
                        errors.append(
                            f"{relative_path}: unable to normalise texture channels ({exc})"
                        )
                        continue
                buffer = io.BytesIO()
                try:
                    image.save(buffer, format="PNG")
                except Exception as exc:  # pragma: no cover - depends on Pillow backend
                    errors.append(
                        f"{relative_path}: Pillow could not encode the texture as PNG ({exc})"
                    )
                    continue
                output_bytes = buffer.getvalue()
                relative_target_path = (
                    Path(*relative_path.split("/")) if relative_path else Path()
                ).with_suffix(".png")
                export_relative = relative_target_path.as_posix()
                original_relative = relative_path
                metadata_extra["export_format"] = "png"
                metadata_extra["export_size"] = len(output_bytes)
            elif format_override == "dds" and is_texture:
                relative_target_path = (
                    Path(*relative_path.split("/")) if relative_path else Path()
                ).with_suffix(".dds")
                export_relative = relative_target_path.as_posix()
                original_relative = (
                    relative_path if export_relative != relative_path else None
                )
                metadata_extra["export_format"] = "dds"
                if payload.startswith(b"DDS "):
                    metadata_extra["export_size"] = len(output_bytes)
                else:
                    if not PIL_AVAILABLE or Image is None:
                        errors.append(
                            f"{relative_path}: install Pillow to export textures as DDS"
                        )
                        continue
                    try:
                        preview_data = _prepare_preview_image(payload)
                        image = preview_data["image"]
                    except Exception as exc:  # pragma: no cover - depends on payload
                        errors.append(
                            f"{relative_path}: unable to convert texture to DDS ({exc})"
                        )
                        continue
                    try:
                        output_bytes = _encode_image_to_dds(image)
                    except Exception as exc:  # pragma: no cover - depends on Pillow backend
                        errors.append(
                            f"{relative_path}: Pillow could not encode the texture as DDS ({exc})"
                        )
                        continue
                    metadata_extra["export_size"] = len(output_bytes)
            else:
                export_relative, original_relative = resolve_export_relative_path(
                    relative_path, payload
                )
                relative_target_path = (
                    Path(*export_relative.split("/")) if export_relative else Path()
                )

            target = destination_path / relative_target_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(output_bytes)
            metadata_offset = int(entry.get("offset", 0))
            metadata_size = int(entry.get("size", 0))
            if replacement_payload is not None:
                metadata_size = len(replacement_payload)
                replacement_offset: int | None = None
                if isinstance(replacement_value, dict):
                    offset_value = replacement_value.get("offset")
                    if isinstance(offset_value, int):
                        replacement_offset = offset_value
                    else:
                        source_info = replacement_value.get("source_info")
                        if isinstance(source_info, dict):
                            metadata_info = source_info.get("metadata")
                            if isinstance(metadata_info, dict):
                                offset_value = metadata_info.get("offset")
                                if isinstance(offset_value, int):
                                    replacement_offset = offset_value
                if replacement_offset is not None:
                    metadata_offset = replacement_offset

            metadata = {
                "relative_path": relative_path,
                "offset": metadata_offset,
                "size": metadata_size,
            }
            if metadata_extra:
                metadata.update(metadata_extra)
            audio_metadata = detect_wav_metadata(payload)
            if audio_metadata is not None:
                metadata["audio"] = audio_metadata
            if source_archive:
                metadata["source_archive"] = source_archive
            metadata_path = target.with_name(target.name + ".rsmeta")
            metadata_path.write_text(json.dumps(metadata, indent=2))
            if original_relative is not None:
                renamed.append((original_relative, export_relative))
            count += 1
        return count, renamed, errors

    def _set_channel_controls_state(self, state: str) -> None:
        for button in self.channel_buttons.values():
            button.configure(state=state)
        if state == "disabled":
            for channel, var in self.channel_vars.items():
                default = self._channel_default_state.get(channel, 1)
                var.set(default)

    def _update_channel_preview(self, reset_zoom: bool = False) -> None:
        if self.preview_source_image is None or not PIL_IMAGETK_AVAILABLE:
            return

        rgba = self.preview_source_image.split()
        if len(rgba) != 4:
            # Defensive: ensure we always have 4 channels to merge.
            if hasattr(self.preview_label, "configure"):
                self.preview_label.configure(image="", text="Unsupported image mode")
                self.preview_label.image = None
            self.preview_display_base = None
            self.preview_image = None
            self._update_preview_scrollregion()
            return

        r, g, b, a = rgba
        if not self.channel_vars["R"].get():
            r = Image.new("L", self.preview_source_image.size, 0)
        if not self.channel_vars["G"].get():
            g = Image.new("L", self.preview_source_image.size, 0)
        if not self.channel_vars["B"].get():
            b = Image.new("L", self.preview_source_image.size, 0)
        if not self.channel_vars["A"].get():
            a = Image.new("L", self.preview_source_image.size, 255)

        composed = Image.merge("RGBA", (r, g, b, a))
        display_image = _composite_transparency_preview(composed)
        self.preview_display_base = display_image
        if reset_zoom:
            self._fit_scale = None
            self._current_zoom = 1.0
        self._update_preview_photo(reset_zoom=reset_zoom)
        self.preview_image = getattr(self.preview_label, "image", None)

    def _start_preview_task(
        self, entry: Dict[str, object], payload: bytes
    ) -> None:
        relative_path = entry.get("relative_path")
        preview_kind = "image"
        if isinstance(relative_path, str) and is_audio_entry(relative_path):
            preview_kind = "audio"
        if preview_kind == "image" and (
            not PIL_AVAILABLE or not PIL_IMAGETK_AVAILABLE
        ):
            return

        self._preview_task_kind = preview_kind
        self._cancel_pending_preview()
        request_id = self._invalidate_preview_request()

        try:
            if preview_kind == "audio":
                future = self._preview_executor.submit(
                    _prepare_preview_audio, payload
                )
            else:
                future = self._preview_executor.submit(
                    _prepare_preview_image, payload
                )
        except RuntimeError as exc:
            self._set_status(
                f"Unable to schedule preview for {entry['relative_path']}: {exc}"
            )
            return
        self._preview_task = future

        def _deliver_result(completed: Future[Dict[str, object]]) -> None:
            self.root.after(
                0, self._on_preview_task_complete, request_id, entry, completed
            )

        future.add_done_callback(_deliver_result)

    def _on_preview_task_complete(
        self,
        request_id: int,
        entry: Dict[str, object],
        future: Future[Dict[str, object]],
    ) -> None:
        if request_id != self._preview_request_id:
            return

        self._preview_task = None
        if future.cancelled():
            return

        try:
            result = future.result()
        except UnidentifiedImageError as exc:
            self._clear_preview("Unsupported texture format")
            self._set_status(
                f"Unsupported texture format for {entry['relative_path']}: {exc}"
            )
            return
        except Exception as exc:  # pragma: no cover - depends on payload contents
            if self._preview_task_kind == "audio":
                self._clear_preview("Unable to preview audio clip")
                self._set_status(
                    f"Failed to prepare audio preview for {entry['relative_path']}: {exc}".strip()
                )
            else:
                self._clear_preview("Unable to preview texture")
                self._set_status(
                    f"Failed to render {entry['relative_path']}: {exc}".strip()
                )
            return

        if self._preview_task_kind == "audio":
            self._handle_audio_preview_result(entry, result)
            return

        image = result.get("image")
        display_image = result.get("display_image")
        format_hint = result.get("format_hint")
        detected_format = result.get("detected_format")
        dds_info = result.get("dds_info")

        if image is None or display_image is None:
            self._clear_preview("Unable to preview texture")
            self._set_status(
                f"Failed to render {entry['relative_path']}: missing image data"
            )
            return

        self.preview_source_image = image
        self.preview_display_base = display_image
        self._fit_scale = None
        self._current_zoom = 1.0
        self._set_channel_controls_state("normal")
        channel_state = self._pending_channel_state or {}
        self._pending_channel_state = None
        for channel, value in channel_state.items():
            if channel in self.channel_vars:
                self.channel_vars[channel].set(1 if value else 0)

        all_channels_enabled = all(
            self.channel_vars[channel].get() for channel in self.channel_vars
        )

        if all_channels_enabled and PIL_IMAGETK_AVAILABLE:
            self._update_preview_photo(reset_zoom=True)
            self.preview_image = getattr(self.preview_label, "image", None)
        else:
            self._update_channel_preview(reset_zoom=True)

        format_display, dds_label_text = _describe_preview_format(
            format_hint, detected_format, dds_info
        )
        if hasattr(self, "preview_info_label") and hasattr(
            self.preview_info_label, "configure"
        ):
            self.preview_info_label.configure(text=dds_label_text)
        status_message = f"Previewing {entry['relative_path']}"
        if format_display:
            status_message += f" ({format_display})"
        self._set_status(status_message)

    def _handle_audio_preview_result(
        self, entry: Dict[str, object], result: Dict[str, object]
    ) -> None:
        pcm = result.get("pcm")
        sample_rate = result.get("sample_rate")
        channels = result.get("channels")
        sample_width = result.get("sample_width")
        duration = result.get("duration")
        waveform = result.get("waveform")
        frame_count = result.get("frame_count")

        if not isinstance(pcm, (bytes, bytearray)):
            self._clear_preview("Unable to preview audio clip")
            self._set_status(
                f"Audio preview failed for {entry['relative_path']}: missing PCM data"
            )
            return

        rate_value = (
            int(sample_rate)
            if isinstance(sample_rate, int)
            else int(sample_rate or 0)
        )
        channel_value = (
            int(channels)
            if isinstance(channels, int)
            else int(channels or 0)
        )
        width_value = (
            int(sample_width)
            if isinstance(sample_width, int)
            else int(sample_width or 0)
        )
        duration_value = (
            float(duration)
            if isinstance(duration, (int, float))
            else 0.0
        )
        frame_value = (
            int(frame_count)
            if isinstance(frame_count, int)
            else int(frame_count or 0)
        )

        self._audio_preview_data = {
            "pcm": bytes(pcm),
            "sample_rate": rate_value,
            "channels": channel_value,
            "sample_width": width_value,
            "duration": duration_value,
            "frame_count": frame_value,
            "waveform": waveform if isinstance(waveform, list) else None,
        }

        self._stop_audio_preview()
        self.preview_source_image = None
        self.preview_display_base = None
        self.preview_image = None
        self._fit_scale = None
        self._current_zoom = 1.0
        self._set_channel_controls_state("disabled")
        self._ensure_single_preview_mode()

        sounddevice_ready = _sounddevice_backend_available()
        if sounddevice_ready:
            playback_message = "Audio preview ready. Use Play to listen to the clip."
        else:
            playback_message = (
                "Audio playback requires the optional sounddevice+numpy modules.\n"
                "Install them to enable in-app playback."
            )

        if hasattr(self.preview_label, "configure"):
            self.preview_label.configure(image="", text=playback_message)
            self.preview_label.image = None

        waveform_count = len(waveform) if isinstance(waveform, list) else 0
        info_text = (
            f"Waveform samples: {waveform_count} (downsampled)"
            if waveform_count
            else ""
        )
        if hasattr(self.preview_info_label, "configure"):
            self.preview_info_label.configure(text=info_text)

        bits = width_value * 8 if width_value else 0
        channel_text = "channel" if channel_value == 1 else "channels"
        if rate_value and channel_value and width_value:
            metadata_text = (
                f"{channel_value} {channel_text} • {rate_value} Hz "
                f"• {bits}-bit • {duration_value:.2f} s"
            )
        else:
            metadata_text = "Audio metadata unavailable"
        self.audio_metadata_var.set(metadata_text)

        if sounddevice_ready:
            self.audio_status_var.set("Ready to play")
        else:
            self.audio_status_var.set(
                "Audio playback unavailable: install sounddevice (pip install sounddevice numpy) to enable Play/Stop."
            )

        self.audio_controls_frame.grid()
        self._audio_is_playing = False
        self._update_audio_controls()
        self._update_preview_scrollregion(reset_position=True)

        status_message = f"Previewing {entry['relative_path']} (WAV audio)"
        if not sounddevice_ready:
            status_message += (
                " – audio playback unavailable (install sounddevice with numpy)"
            )
        self._set_status(status_message)

    def _on_entry_selected(self, _event: object) -> None:
        if self.archive_bytes is None or not self.entries:
            self._clear_preview("Open an archive to preview entries")
            return

        try:
            selected_nodes = list(self.tree.selection())
        except tk.TclError:
            selected_nodes = []
        if not selected_nodes:
            self._clear_preview()
            return

        try:
            focus_node = self.tree.focus()
        except tk.TclError:
            focus_node = ""
        if focus_node not in selected_nodes:
            focus_node = None

        if focus_node is None and self.last_activated_path is not None:
            candidate = self.entry_nodes.get(self.last_activated_path)
            if candidate in selected_nodes:
                focus_node = candidate

        if focus_node is None:
            focus_node = selected_nodes[-1]

        if focus_node in self.node_to_path and focus_node not in self.node_to_entry:
            with contextlib.suppress(tk.TclError):
                self.tree.focus(focus_node)
                self.tree.see(focus_node)
            self.last_activated_path = None
            self._show_folder_grid(focus_node)
            return

        entry_node = focus_node if focus_node in self.node_to_entry else None
        if entry_node is None:
            for node_id in selected_nodes:
                if node_id in self.node_to_entry:
                    entry_node = node_id
                    break
        if entry_node is None:
            self._clear_preview()
            return

        with contextlib.suppress(tk.TclError):
            self.tree.focus(entry_node)
            self.tree.see(entry_node)

        entry = self.node_to_entry[entry_node]
        relative_path_value = entry.get("relative_path")
        if isinstance(relative_path_value, str):
            self.last_activated_path = relative_path_value
            relative_path = relative_path_value
        else:
            self.last_activated_path = None
            relative_path = ""
        is_audio_preview = bool(relative_path) and is_audio_entry(relative_path)
        if not is_audio_preview:
            if not PIL_AVAILABLE:
                self._clear_preview("Pillow is not installed; preview unavailable")
                self._set_status(
                    "Preview unavailable: install Pillow to enable texture previews"
                )
                return

            if not PIL_IMAGETK_AVAILABLE:
                self._clear_preview("Install Pillow's ImageTk support to preview textures")
                self._set_status(
                    "Preview unavailable: install pillow[tk] or python3-tk for ImageTk"
                )
                return
            self._stop_audio_preview()
            self._audio_preview_data = None
            self._audio_is_playing = False
            self.audio_metadata_var.set("")
            self.audio_status_var.set("")
            if hasattr(self, "audio_controls_frame"):
                self.audio_controls_frame.grid_remove()
        entry_path = entry.get("relative_path", "")
        if is_audio_entry(entry_path):
            self._clear_preview("Audio preview not available")
            self.last_activated_path = entry_path
            self._set_status(f"Selected audio entry {entry_path}")
            return

        if not PIL_AVAILABLE:
            self._clear_preview("Pillow is not installed; preview unavailable")
            self._set_status(
                "Preview unavailable: install Pillow to enable texture previews"
            )
            return

        if not PIL_IMAGETK_AVAILABLE:
            self._clear_preview("Install Pillow's ImageTk support to preview textures")
            self._set_status(
                "Preview unavailable: install pillow[tk] or python3-tk for ImageTk"
            )
            return
        self.last_activated_path = entry["relative_path"]
        self._ensure_single_preview_mode()
        self._clear_thumbnail_grid()
        replacement = self.replacements.get(entry["relative_path"])
        payload = None
        if replacement is not None:
            payload = _extract_replacement_payload(replacement)
            if payload is None:
                self._clear_preview("Replacement missing payload; unable to preview")
                self._set_status(
                    f"Replacement for {entry['relative_path']} is missing payload bytes"
                )
                return

        if payload is None:
            payload = self.archive_bytes[entry["offset"] : entry["offset"] + entry["size"]]

        if not payload:
            self._clear_preview("Empty payload; nothing to preview")
            self._set_status(f"Entry {entry['relative_path']} has no data to preview")
            return

        if is_audio_preview:
            self._pending_channel_state = None
            self._stop_audio_preview()
            self._audio_preview_data = None
            self._audio_is_playing = False
            self.audio_metadata_var.set("")
            self.audio_status_var.set("")
            if hasattr(self, "audio_controls_frame"):
                self.audio_controls_frame.grid_remove()
            self.preview_source_image = None
            self.preview_display_base = None
            self.preview_image = None
            self._fit_scale = None
            self._current_zoom = 1.0
            self._set_channel_controls_state("disabled")
            if hasattr(self.preview_label, "configure"):
                self.preview_label.configure(
                    image="", text="Preparing audio preview…"
                )
                self.preview_label.image = None
            if hasattr(self.preview_info_label, "configure"):
                self.preview_info_label.configure(text="")
            self._update_preview_scrollregion(reset_position=True)
            status_message = f"Preparing audio preview for {entry['relative_path']}…"
            self._set_status(status_message)
            self._start_preview_task(entry, payload)
            return

        format_hint = None
        detected_format = None
        dds_info: Dict[str, object] | None = None
        preview_image: "Image.Image" | None = None
        display_image: "Image.Image" | None = None
        fallback_reason: str | None = None
        try:
            with contextlib.closing(io.BytesIO(payload)) as stream:
                if payload.startswith(b"DDS "):
                    format_hint = "DDS"
                    dds_info = _parse_dds_header(payload)
                image = Image.open(stream)
                image.load()
                detected_format = image.format
                image = image.convert("RGBA")
                display_image = _composite_transparency_preview(image)
                preview_image = image
        except (
            UnidentifiedImageError,
            OSError,
            ValueError,
            NotImplementedError,
        ) as exc:
            fallback_reason = str(exc)
        except Exception as exc:  # pragma: no cover - depends on payload contents
            self._clear_preview("Unable to preview texture")
            self._set_status(
                f"Failed to render {entry['relative_path']}: {exc}".strip()
            )
            return

        if (
            fallback_reason is None
            and preview_image is not None
            and display_image is not None
        ):
            self.preview_source_image = preview_image
            self._set_channel_controls_state("normal")
            self.preview_display_base = display_image
            self._fit_scale = None
            self._current_zoom = 1.0
            self._update_preview_photo(reset_zoom=True)
            self.preview_image = getattr(self.preview_label, "image", None)
            if hasattr(self, "preview_info_label") and hasattr(
                self.preview_info_label, "configure"
            ):
                self.preview_info_label.configure(text="")
        else:
            self.preview_source_image = None
            self.preview_display_base = None
            if hasattr(self.preview_label, "configure"):
                self.preview_label.configure(
                    image="",
                    text="Decoding preview using fallback pipeline…",
                )
                self.preview_label.image = None
            if hasattr(self, "preview_info_label") and hasattr(
                self.preview_info_label, "configure"
            ):
                self.preview_info_label.configure(text="")
            self.preview_image = None
            self._update_preview_scrollregion(reset_position=True)

        self._pending_channel_state = {
            channel: var.get() for channel, var in self.channel_vars.items()
        }
        self._set_channel_controls_state("disabled")
        self._update_preview_scrollregion(reset_position=True)
        status_message = f"Rendering preview for {entry['relative_path']}…"
        if fallback_reason:
            status_message += f" (fallback decoder: {fallback_reason})"
        self._set_status(status_message)
        self._start_preview_task(entry, payload)

    # ------------------------------------------------------------- callbacks --
    def open_archive(self) -> None:
        filename = filedialog.askopenfilename(
            title="Select Asura archive",
            filetypes=[
                (
                    "Asura archives",
                    "*.asr *.gui *.pc *.es *.en *.fr *.de *.sp *.it *.ge *.pl",
                ),
                ("All files", "*.*"),
            ],
        )
        if not filename:
            return

        previous_buffer = self.archive_bytes

        try:
            data, layout_info, entries, wrapper = load_archive(Path(filename))
        except Exception as exc:  # pragma: no cover - GUI path
            self._show_error("Unable to open archive", str(exc))
            return

        all_entries = copy.deepcopy(entries)
        image_entries = [
            entry for entry in all_entries if is_image_entry(entry["relative_path"])
        ]
        audio_entries = [
            entry for entry in all_entries if is_audio_entry(entry["relative_path"])
        ]
        previewable_entries = [
            entry
            for entry in all_entries
            if is_image_entry(entry["relative_path"]) or is_audio_entry(entry["relative_path"])
        ]
        media_entries = [
            entry for entry in all_entries if is_supported_entry(entry["relative_path"])
        ]
        if not image_entries:
            if audio_entries:
                self._show_info(
                    "No textures found",
                    (
                        "The selected archive does not contain recognised image entries.\n"
                        "Audio clips will still be listed for preview."
                    ),
                )
            else:
                self._show_info(
                    "No textures found",
                    "The selected archive does not contain recognised image entries.",
                )
        if not media_entries:
            self._show_info(
                "No supported entries found",
                "The selected archive does not contain recognised texture or audio entries.",
            )

        _release_archive_buffer(previous_buffer)
        self.archive_path = Path(filename)
        self.archive_bytes = data
        self.all_entries = all_entries
        self.image_entries = image_entries
        self.audio_entries = audio_entries
        self.previewable_entries = previewable_entries
        self.supported_entries = media_entries
        self.entries = list(self.previewable_entries)
        self.replacements.clear()
        self.wrapper_info = wrapper
        self.layout_info = layout_info
        self._expand_all_on_refresh = True
        self._refresh_list()
        self._clear_preview()
        status_parts = [
            f"Loaded {len(self.image_entries)} image entries"
        ]
        if self.audio_entries:
            status_parts.append(f"{len(self.audio_entries)} audio clip(s)")
        status_message = " and ".join(status_parts)
        status_message += f" from {self.archive_path.name}"
        self._set_status(status_message)

    def export_selected(self) -> None:
        self._export_selected_with_format(None)

    def export_selected_as_dds(self) -> None:
        self._export_selected_with_format("dds")

    def export_selected_as_png(self) -> None:
        self._export_selected_with_format("png")

    def _export_selected_with_format(self, format_override: str | None) -> None:
        if self.archive_bytes is None:
            self._show_warning("No archive", "Open an archive before exporting.")
            return

        try:
            node_ids = list(self.tree.selection())
        except tk.TclError:
            node_ids = []

        if not node_ids:
            self._show_info("Nothing selected", "Select one or more entries to export.")
            return

        destination = filedialog.askdirectory(title="Select export directory")
        if not destination:
            return

        destination_path = Path(destination)
        entries = self._collect_entries_from_nodes(node_ids)
        if not entries:
            self._show_info(
                "No entries",
                "The selected items do not contain any texture or audio entries to export.",
            )
            return
        count, renamed, errors = self._export_entries(
            destination_path, entries, format_override=format_override
        )
        rename_note = (
            f"; adjusted extensions for {len(renamed)} file(s)"
            if renamed
            else ""
        )
        metadata_note = "; created .rsmeta metadata files to re-import without browsing the tree"
        format_note = (
            f"; saved textures as {format_override.upper()}" if format_override else ""
        )
        self._set_status(
            (
                f"Exported {count} file(s) to {destination_path}"
                f"{rename_note}{metadata_note}{format_note}"
            ).strip()
        )
        if errors:
            preview = "\n".join(errors[:10])
            if len(errors) > 10:
                preview += "\n…"
            self._show_warning(
                "Some entries skipped",
                preview or "One or more entries could not be exported.",
            )

    def export_all(self) -> None:
        if self.archive_bytes is None:
            self._show_warning("No archive", "Open an archive before exporting.")
            return
        if not self.entries:
            self._show_info(
                "No entries", "Open an archive with textures or audio before exporting."
            )
            return

        destination = filedialog.askdirectory(title="Select export directory")
        if not destination:
            return

        destination_path = Path(destination)
        count, renamed, errors = self._export_entries(destination_path, self.entries)
        rename_note = (
            f"; adjusted extensions for {len(renamed)} file(s)"
            if renamed
            else ""
        )
        metadata_note = "; created .rsmeta metadata files to re-import without browsing the tree"
        self._set_status(
            f"Exported {count} file(s) to {destination_path}{rename_note}{metadata_note}"
        )
        if errors:
            preview = "\n".join(errors[:10])
            if len(errors) > 10:
                preview += "\n…"
            self._show_warning(
                "Some entries skipped",
                preview or "One or more entries could not be exported.",
            )

    def import_replacement(self) -> None:
        if self.archive_bytes is None:
            self._show_warning(
                "No archive", "Open an archive before importing replacements."
            )
            return

        entry = self._current_entry_selection()
        if entry is None:
            self._show_info(
                "Select an entry",
                "Choose a single texture or audio entry before importing a replacement.",
            )
            return

        filename = filedialog.askopenfilename(
            title="Select replacement file",
            filetypes=[
                (
                    "Supported files",
                    "*.dds *.png *.tga *.bmp *.jpg *.jpeg *.gif *.tif *.tiff *.wav",
                ),
                ("Image files", "*.dds *.png *.tga *.bmp *.jpg *.jpeg *.gif *.tif *.tiff"),
                ("Audio files", "*.wav"),
                ("All files", "*.*"),
            ],
        )
        if not filename:
            return

        self._import_replacement_from_path(entry, Path(filename))

    def _import_replacements_from_directory(
        self,
        directory: Path,
        metadata_files: Sequence[Path] | None = None,
        *,
        progress_callback: Callable[[int, int], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> Tuple[int, int, List[str]]:
        files: List[Path]
        if metadata_files is None:
            files = sorted(directory.rglob("*.rsmeta"))
        else:
            files = list(metadata_files)

        if not files:
            return 0, 0, []

        entry_index = self._build_entry_index()
        imported = 0
        errors: List[str] = []

        for index, metadata_path in enumerate(files, start=1):
            if cancel_event is not None and cancel_event.is_set():
                break

            try:
                metadata = json.loads(metadata_path.read_text())
            except Exception as exc:
                errors.append(
                    f"{metadata_path}: unable to read metadata ({exc})."
                )
                if progress_callback is not None:
                    progress_callback(index, imported)
                continue

            relative_path = metadata.get("relative_path") if isinstance(metadata, dict) else None
            if not isinstance(relative_path, str) or not relative_path:
                errors.append(
                    f"{metadata_path}: metadata does not include a valid 'relative_path'."
                )
                if progress_callback is not None:
                    progress_callback(index, imported)
                continue

            entry = entry_index.get(relative_path)
            if entry is None:
                errors.append(
                    f"{metadata_path}: {relative_path} is not present in the opened archive."
                )
                if progress_callback is not None:
                    progress_callback(index, imported)
                continue

            payload_path = metadata_path.with_name(metadata_path.name[:-7])
            if not payload_path.exists():
                errors.append(
                    f"{metadata_path}: could not locate the exported file next to the metadata."
                )
                if progress_callback is not None:
                    progress_callback(index, imported)
                continue

            success = self._import_replacement_from_path(
                entry, payload_path, set_focus=False
            )
            if success:
                imported += 1
            else:
                errors.append(f"{payload_path}: import cancelled or failed.")

            if progress_callback is not None:
                progress_callback(index, imported)

        return imported, len(files), errors

    def _start_folder_import(self, directory_path: Path) -> bool:
        if self.archive_bytes is None:
            self._show_warning(
                "No archive", "Open an archive before importing replacements."
            )
            return False

        if not directory_path.exists():
            self._show_warning(
                "Folder not found",
                f"The directory {directory_path} does not exist.",
            )
            return False

        if self._folder_import_thread and self._folder_import_thread.is_alive():
            self._show_info(
                "Import in progress",
                "Wait for the current folder import to finish before starting another.",
            )
            return False

        directory_path = Path(directory_path)
        metadata_files = sorted(directory_path.rglob("*.rsmeta"))
        total_metadata = len(metadata_files)
        if total_metadata == 0:
            self._show_info(
                "No metadata",
                "No .rsmeta files were found in the selected folder.",
            )
            self._set_status(
                f"No .rsmeta metadata found in {directory_path}".strip()
            )
            return False

        progress_window = tk.Toplevel(self.root)
        progress_window.title("Importing replacements")
        progress_window.transient(self.root)
        progress_window.resizable(False, False)

        progress_message = tk.StringVar(value="Preparing import...")
        tk.Label(progress_window, textvariable=progress_message).pack(
            padx=20, pady=(15, 5)
        )

        progress_var = tk.DoubleVar(value=0)
        progress_bar = ttk.Progressbar(
            progress_window,
            maximum=total_metadata,
            variable=progress_var,
            length=320,
            mode="determinate",
        )
        progress_bar.pack(padx=20, pady=5, fill="x")

        cancel_event = threading.Event()
        self._folder_import_cancel_event = cancel_event

        def request_cancel() -> None:
            if cancel_event.is_set():
                return
            cancel_event.set()
            progress_message.set("Cancelling...")
            try:
                cancel_button.configure(state="disabled")
            except tk.TclError:
                pass

        cancel_button = tk.Button(progress_window, text="Cancel", command=request_cancel)
        cancel_button.pack(padx=20, pady=(5, 15))

        progress_window.protocol("WM_DELETE_WINDOW", request_cancel)
        progress_window.focus_set()
        progress_window.grab_set()

        def update_progress(processed: int, imported_count: int) -> None:
            progress_var.set(min(processed, total_metadata))
            progress_message.set(
                f"Importing replacements... {processed}/{total_metadata}"
                f" (imported {imported_count})"
            )

        def finalize(imported_count: int, errors: List[str]) -> None:
            self._folder_import_thread = None
            self._folder_import_cancel_event = None
            cancelled = cancel_event.is_set()
            if progress_window.winfo_exists():
                try:
                    progress_window.grab_release()
                except tk.TclError:
                    pass
                progress_window.destroy()

            if cancelled:
                self._set_status("Folder import cancelled")
                if imported_count:
                    self._show_info(
                        "Import cancelled",
                        (
                            "The import was cancelled after applying "
                            f"{imported_count} replacement(s)."
                        ),
                    )
                else:
                    self._show_info(
                        "Import cancelled",
                        "Folder import was cancelled before any replacements were applied.",
                    )
                return

            if imported_count:
                self._set_status(
                    f"Imported {imported_count} replacement(s) from {directory_path}".strip()
                )
            else:
                self._set_status(
                    f"No replacements imported from {directory_path}".strip()
                )

            if errors:
                preview = "\n".join(errors[:10])
                if len(errors) > 10:
                    preview += "\n…"
                self._show_warning(
                    "Some entries skipped",
                    preview or "Some replacements could not be imported.",
                )

        def report_progress(processed: int, imported_count: int) -> None:
            self.root.after(0, update_progress, processed, imported_count)

        def background_import() -> None:
            try:
                imported_count, _, errors = self._import_replacements_from_directory(
                    directory_path,
                    metadata_files,
                    progress_callback=report_progress,
                    cancel_event=cancel_event,
                )
            except Exception as exc:  # pragma: no cover - background thread
                self.root.after(0, finalize, 0, [str(exc)])
                return
            self.root.after(0, finalize, imported_count, errors)

        thread = threading.Thread(target=background_import, daemon=True)
        self._folder_import_thread = thread
        thread.start()
        return True

    def import_folder(self) -> None:
        if self.archive_bytes is None:
            self._show_warning(
                "No archive", "Open an archive before importing replacements."
            )
            return

        directory = filedialog.askdirectory(title="Select folder with replacements")
        if not directory:
            return

        self._start_folder_import(Path(directory))

    def load_replacement_log(self) -> None:
        if self.archive_bytes is None:
            self._show_warning(
                "No archive", "Open an archive before loading a modification log."
            )
            return

        filename = filedialog.askopenfilename(
            title="Select modification log",
            filetypes=[("Modification logs", "*.replacements.json *.json"), ("All files", "*.*")],
        )
        if not filename:
            return

        log_path = Path(filename)
        try:
            log_data = json.loads(log_path.read_text())
        except Exception as exc:  # pragma: no cover - GUI path
            self._show_error("Unable to read log", str(exc))
            return

        replacements_info = log_data.get("replacements")
        if not isinstance(replacements_info, list) or not replacements_info:
            self._show_info(
                "No replacements", "The selected log does not include any replacements."
            )
            return

        entry_index = self._build_entry_index()

        loaded_paths: List[str] = []
        errors: List[str] = []

        for info in replacements_info:
            if not isinstance(info, dict):
                continue
            relative_path = info.get("relative_path")
            if not isinstance(relative_path, str) or not relative_path:
                continue

            entry = entry_index.get(relative_path)
            if entry is None:
                errors.append(f"{relative_path}: entry not present in the opened archive")
                continue

            source = info.get("source") or {}
            source_path_value = source.get("path") if isinstance(source, dict) else None
            if not isinstance(source_path_value, str) or not source_path_value:
                errors.append(f"{relative_path}: no source path recorded in the log")
                continue

            source_path = Path(source_path_value)
            if not source_path.exists():
                errors.append(f"{relative_path}: {source_path} does not exist")
                continue

            try:
                payload = source_path.read_bytes()
            except Exception as exc:  # pragma: no cover - GUI path
                errors.append(f"{relative_path}: unable to read {source_path}: {exc}")
                continue

            metadata: Dict[str, object] | None = None
            metadata_path_value = None
            if isinstance(source, dict):
                metadata_path_candidate = source.get("metadata_path")
                if isinstance(metadata_path_candidate, str) and metadata_path_candidate:
                    metadata_path_value = metadata_path_candidate
            if isinstance(info.get("metadata"), dict):
                metadata = info["metadata"]  # type: ignore[assignment]
            elif metadata_path_value:
                metadata_path = Path(metadata_path_value)
                if metadata_path.exists():
                    try:
                        metadata = json.loads(metadata_path.read_text())
                    except Exception:  # pragma: no cover - GUI path
                        metadata = None

            replacement_record: Dict[str, object] = {
                "payload": payload,
                "source_info": {"path": str(source_path)},
            }
            if metadata_path_value:
                replacement_record["source_info"]["metadata_path"] = metadata_path_value
            if isinstance(metadata, dict):
                replacement_record["source_info"]["metadata"] = metadata

            for numeric_key in ("offset", "size"):
                numeric_value = info.get(numeric_key)
                if isinstance(numeric_value, int):
                    replacement_record[numeric_key] = int(numeric_value)

            self.replacements[relative_path] = replacement_record
            loaded_paths.append(relative_path)

        if not loaded_paths:
            self._show_info(
                "No replacements loaded",
                "None of the logged replacements could be imported.",
            )
            if errors:
                self._show_warning(
                    "Replacements skipped",
                    "\n".join(errors[:10]) + ("\n…" if len(errors) > 10 else ""),
                )
            return

        self._refresh_list()

        first_path = loaded_paths[0]
        node_id = self.entry_nodes.get(first_path)
        if node_id is not None:
            self.tree.selection_set(node_id)
            self.tree.focus(node_id)
            self.tree.see(node_id)
            self.last_activated_path = first_path
            self._on_entry_selected(None)

        self._set_status(
            f"Loaded {len(loaded_paths)} replacement(s) from {log_path.name}"
        )

        if errors:
            self._show_warning(
                "Some replacements skipped",
                "\n".join(errors[:10]) + ("\n…" if len(errors) > 10 else ""),
            )

    def _ask_patch_destination(
        self,
        archive_output: Path,
        *,
        require_confirmation: bool = True,
    ) -> Path | None:
        """Return the destination path for an optional patch archive."""

        if self.layout_info is None:
            return None

        preferred_suffix = _normalise_archive_suffix(archive_output.suffix)
        default_patch = archive_output.with_name(
            f"{archive_output.stem}.patch{preferred_suffix}"
        )
        if require_confirmation:
            try:
                confirm = self._ask_yes_no(
                    "Create patch archive?",
                    "Would you like to generate a separate patch archive containing only the modified files?",
                )
            except tk.TclError:
                confirm = False

            if not confirm:
                return None

        filename = filedialog.asksaveasfilename(
            title="Save patch archive",
            defaultextension=preferred_suffix,
            initialfile=default_patch.name,
            filetypes=_build_archive_filetypes(preferred_suffix),
        )
        if not filename:
            return None
        return Path(filename)

    def _create_patch_archive(
        self,
        patch_path: Path,
        replacements: Dict[str, object],
        *,
        archive_bytes: bytes | mmap.mmap | None = None,
        entries: Sequence[Dict[str, object]] | None = None,
    ) -> List[str]:
        """Write a patch archive reflecting *replacements* to *patch_path*."""

        archive_bytes = archive_bytes if archive_bytes is not None else self.archive_bytes

        if entries is None:
            # ``TextureManagerGUI`` normally initialises ``all_entries`` in ``__init__``
            # but the GUI helper is also exercised by the test-suite using a manually
            # constructed instance (``object.__new__``) to avoid spinning up a full
            # Tkinter environment.  Merge refactors introduced an unconditional
            # reference to ``self.all_entries`` here, which breaks that test harness
            # and any other callers providing only ``entries``.  Fall back gracefully
            # so the helper continues to operate with just ``self.entries`` available.
            entries = getattr(self, "all_entries", None)
            if entries is None:
                entries = getattr(self, "entries", None)

        if archive_bytes is None or self.layout_info is None or entries is None:
            raise RSFLParsingError("open an archive before creating a patch")

        manifest = {
            "archive": self.archive_path.name if self.archive_path else "",
            "rsfl": copy.deepcopy(self.layout_info),
            "files": [copy.deepcopy(entry) for entry in entries],
        }

        patch_bytes, written = generate_patch_archive(
            manifest,
            replacements,
            archive_bytes=archive_bytes,
            original_entries=entries,
        )

        if not written:
            raise RSFLParsingError("no replacements require a patch archive")

        patch_path.parent.mkdir(parents=True, exist_ok=True)
        patch_path.write_bytes(patch_bytes)
        return written

    def create_patch_archive(self) -> None:
        if self.archive_bytes is None or self.archive_path is None:
            self._show_warning(
                "No archive", "Open an archive before creating a patch."
            )
            return
        if not self.replacements:
            self._show_info(
                "No replacements", "Import at least one texture or audio entry first."
            )
            return

        patch_destination = self._ask_patch_destination(
            self.archive_path, require_confirmation=False
        )
        if patch_destination is None:
            return

        pending_replacements = dict(self.replacements)

        try:
            written = self._create_patch_archive(patch_destination, pending_replacements)
        except RSFLParsingError as exc:  # pragma: no cover - GUI path
            self._show_info("Patch archive not created", str(exc))
            return
        except Exception as exc:  # pragma: no cover - GUI path
            self._show_error("Unable to create patch archive", str(exc))
            return

        count = len(written)
        logged_replacements = {
            rel_path: pending_replacements[rel_path]
            for rel_path in written
            if rel_path in pending_replacements
        }
        log_path = None
        log_error = None
        if logged_replacements:
            try:
                log_path = write_replacement_log(
                    patch_destination,
                    logged_replacements,
                    archive_path=self.archive_path,
                )
            except Exception as exc:  # pragma: no cover - GUI path
                log_error = str(exc)

        status_parts = [
            f"Patch archive written to {patch_destination} ({count} file(s))"
        ]
        if log_path is not None:
            status_parts.append(f"log saved to {log_path.name}")
        base_status = "; ".join(status_parts)
        self._set_status(base_status)

        message_lines = [status_parts[0]]
        if log_path is not None:
            message_lines.append(f"Modification log saved to {log_path}")
        if log_error is not None:
            message_lines.append(f"Could not save modification log: {log_error}")

        self._show_info("Patch archive saved", "\n".join(message_lines))

        if log_error is not None:
            self._show_warning("Modification log not saved", log_error)

        lst_status_note = None
        lst_prompt_error = None
        try:
            should_update_lst = self._ask_yes_no(
                "Update patch list",
                (
                    "Patch archive created successfully.\n\n"
                    "Would you like to append this patch to a .lst file?"
                ),
            )
        except Exception as exc:
            should_update_lst = False
            lst_prompt_error = str(exc)

        if should_update_lst:
            lst_path = filedialog.askopenfilename(
                title="Select .lst file to update",
                filetypes=[("Patch list", "*.lst"), ("All files", "*.*")],
            )

            if lst_path:
                lst_file = Path(lst_path)
                try:
                    existing_text = lst_file.read_text(encoding="utf-8")
                except Exception as exc:  # pragma: no cover - filesystem errors are environment specific
                    self._show_error(
                        "Unable to read list",
                        f"Could not read {lst_file.name}: {exc}",
                    )
                    lst_status_note = f"failed to read {lst_file.name}"
                else:
                    if existing_text and not existing_text.endswith("\n"):
                        existing_text += "\n"

                    prefix_path: PureWindowsPath | None = None
                    for raw_line in existing_text.splitlines():
                        stripped = raw_line.strip()
                        if not stripped:
                            continue
                        candidate = PureWindowsPath(stripped).parent
                        if str(candidate) == ".":
                            candidate = None
                        prefix_path = candidate
                        if prefix_path is not None:
                            break

                    if prefix_path is None and self.archive_path is not None:
                        archive_parent = self.archive_path.parent
                        archive_parts = list(archive_parent.parts)
                        relevant_parts: List[str] = []
                        for index, part in enumerate(archive_parts):
                            if part.lower() == "envs":
                                relevant_parts = archive_parts[index:]
                                break
                        if not relevant_parts and archive_parent.name:
                            relevant_parts = [archive_parent.name]
                        if relevant_parts:
                            prefix_path = PureWindowsPath(*relevant_parts)

                    if prefix_path is None:
                        new_entry = PureWindowsPath(patch_destination.name)
                    else:
                        new_entry = prefix_path / patch_destination.name

                    updated_text = existing_text + str(new_entry) + "\n"
                    try:
                        lst_file.write_text(updated_text, encoding="utf-8")
                    except Exception as exc:  # pragma: no cover - filesystem errors are environment specific
                        self._show_error(
                            "Unable to write list",
                            f"Could not update {lst_file.name}: {exc}",
                        )
                        lst_status_note = f"failed to update {lst_file.name}"
                    else:
                        self._show_info(
                            "Patch list updated",
                            (f"Added {new_entry} to {lst_file}"),
                        )
                        lst_status_note = f"patch list updated ({lst_file.name})"
            else:
                lst_status_note = "patch list update cancelled"
        elif should_update_lst is False:
            if lst_prompt_error is not None:
                lst_status_note = "patch list prompt failed"
            else:
                lst_status_note = "patch list update skipped"

        if lst_status_note:
            self._set_status(f"{base_status}; {lst_status_note}")

    def save_patched_archive(self) -> None:
        if self.archive_bytes is None or self.archive_path is None:
            self._show_warning("No archive", "Open an archive before saving.")
            return
        if not self.replacements:
            self._show_info(
                "No replacements", "Import at least one texture or audio entry first."
            )
            return

        preferred_suffix = _normalise_archive_suffix(self.archive_path.suffix)
        filename = filedialog.asksaveasfilename(
            title="Save patched archive",
            defaultextension=preferred_suffix,
            initialfile=self.archive_path.with_suffix(preferred_suffix).name,
            filetypes=_build_archive_filetypes(preferred_suffix),
        )
        if not filename:
            return

        previous_buffer = self.archive_bytes
        staged_entries = copy.deepcopy(self.all_entries)
        pending_replacements = dict(self.replacements)

        try:
            updated, updated_entries = _apply_replacements(
                previous_buffer, staged_entries, self.replacements
            )
        except Exception as exc:  # pragma: no cover - GUI path
            self._show_error("Unable to apply replacements", str(exc))
            return

        new_archive_bytes = bytes(updated)
        wrapped = _wrap_asura_container(new_archive_bytes, self.wrapper_info or {"kind": "raw"})
        output_path = Path(filename)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        total_size = len(wrapped)
        maximum = max(total_size, 1)
        self._set_status("Saving patched archive...")

        progress_window = tk.Toplevel(self.root)
        progress_window.title("Saving patched archive")
        progress_window.transient(self.root)
        progress_window.resizable(False, False)

        progress_message = tk.StringVar(value="Preparing to save archive...")
        tk.Label(progress_window, textvariable=progress_message).pack(
            padx=20, pady=(15, 5)
        )

        progress_var = tk.DoubleVar(value=0)
        progress_bar = ttk.Progressbar(
            progress_window,
            maximum=maximum,
            variable=progress_var,
            length=320,
            mode="determinate",
        )
        progress_bar.pack(padx=20, pady=5, fill="x")

        cancel_event = threading.Event()

        def request_cancel() -> None:
            if cancel_event.is_set():
                return
            cancel_event.set()
            progress_message.set("Cancelling...")
            try:
                cancel_button.configure(state="disabled")
            except tk.TclError:
                pass

        cancel_button = tk.Button(progress_window, text="Cancel", command=request_cancel)
        cancel_button.pack(padx=20, pady=(5, 15))

        progress_window.protocol("WM_DELETE_WINDOW", request_cancel)
        progress_window.focus_set()
        progress_window.grab_set()

        def update_progress(written: int) -> None:
            progress_var.set(min(written, maximum))
            if total_size:
                percent = min(100.0, (written / total_size) * 100)
            else:
                percent = 100.0
            progress_message.set(f"Saving archive... {percent:.0f}%")

        def finalize(status: str, error: Exception | None) -> None:
            if status == "success":
                update_progress(total_size)
            if progress_window.winfo_exists():
                try:
                    progress_window.grab_release()
                except tk.TclError:
                    pass
                progress_window.destroy()

            if status == "success":
                _release_archive_buffer(previous_buffer)
                self.archive_bytes = new_archive_bytes
                self.all_entries = updated_entries
                self.image_entries = [
                    entry
                    for entry in updated_entries
                    if is_image_entry(entry["relative_path"])
                ]
                self.supported_entries = [
                    entry
                    for entry in updated_entries
                    if is_supported_entry(entry["relative_path"])
                ]
                self.audio_entries = [
                    entry
                    for entry in updated_entries
                    if is_audio_entry(entry["relative_path"])
                ]
                self.previewable_entries = [
                    entry
                    for entry in updated_entries
                    if is_image_entry(entry["relative_path"])
                    or is_audio_entry(entry["relative_path"])
                ]
                self.entries = list(self.previewable_entries)
                self.replacements.clear()
                self._apply_search_filter()
                self._set_status(f"Saved patched archive to {output_path}")
                self._show_info(
                    "Archive saved", f"Patched archive written to {output_path}"
                )
            elif status == "cancelled":
                self._set_status("Archive save cancelled")
                self._show_info(
                    "Save cancelled", "Archive save was cancelled before completion."
                )
            else:
                self._set_status("Failed to save archive")
                detail = str(error) if error else "Unknown error while saving archive."
                self._show_error("Unable to save archive", detail)

        def report_progress(written: int) -> None:
            self.root.after(0, update_progress, written)

        def background_write() -> None:
            status, error = _write_bytes_in_chunks(
                output_path,
                wrapped,
                chunk_size=4 * 1024 * 1024,
                cancel_event=cancel_event,
                progress_callback=report_progress,
            )
            self.root.after(0, finalize, status, error)

        thread = threading.Thread(target=background_write, daemon=True)
        thread.start()

    # ----------------------------------------------------------------- public --
    def run(self) -> None:
        self.root.mainloop()

def launch_gui() -> None:
    """Launch the Tk based texture browser."""

    gui = TextureManagerGUI()
    gui.run()

