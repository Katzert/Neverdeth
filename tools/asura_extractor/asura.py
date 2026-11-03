#!/usr/bin/env python3
"""Entry point and compatibility wrapper for the Asura tooling."""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from asura_core import *  # noqa: F401,F403
from asura_core import __all__ as _core_all
from asura_core import extract_archive, launch_gui

__all__ = list(_core_all) + ["_build_cli_parser", "_dispatch_cli", "main"]

_OPTIONAL_DEPENDENCIES: tuple[dict[str, object], ...] = (
    {
        "label": "Pillow",
        "module": "PIL",
        "reason": "Las vistas previas de texturas requieren Pillow.",
        "pip": ("Pillow",),
    },
    {
        "label": "Pillow ImageTk",
        "module": "PIL.ImageTk",
        "reason": "Instala pillow[tk] o el paquete del sistema python3-tk para habilitar ImageTk.",
        "pip": tuple(),
    },
    {
        "label": "sounddevice",
        "module": "sounddevice",
        "reason": "La reproducción de audio requiere sounddevice.",
        "pip": ("sounddevice",),
    },
    {
        "label": "NumPy",
        "module": "numpy",
        "reason": "La reproducción de audio requiere numpy para procesar los buffers.",
        "pip": ("numpy",),
    },
    {
        "label": "TkinterDnD2",
        "module": "tkinterdnd2",
        "reason": "Arrastrar y soltar usa tkinterdnd2.",
        "pip": ("tkinterdnd2",),
    },
)


def _gather_missing_optional_dependencies() -> list[dict[str, object]]:
    """Return metadata for optional dependencies that are not installed."""

    missing: list[dict[str, object]] = []
    for dependency in _OPTIONAL_DEPENDENCIES:
        module_name = dependency["module"]
        assert isinstance(module_name, str)
        try:
            spec = importlib.util.find_spec(module_name)
        except ModuleNotFoundError:
            spec = None
        if spec is None:
            missing.append(dependency)
    return missing


def _emit_dependency_report(missing: Sequence[dict[str, object]]) -> tuple[str, ...]:
    """Print guidance for missing optional dependencies and return pip packages."""

    if not missing:
        return tuple()

    print("Detectamos dependencias opcionales faltantes:\n")
    for dependency in missing:
        label = dependency["label"]
        reason = dependency["reason"]
        assert isinstance(label, str)
        assert isinstance(reason, str)
        print(f"- {label}: {reason}")

    pip_packages: set[str] = set()
    for dependency in missing:
        packages = dependency["pip"]
        assert isinstance(packages, tuple)
        pip_packages.update(packages)

    if pip_packages:
        command = f"{sys.executable} -m pip install {' '.join(sorted(pip_packages))}"
        print("\nPuedes instalarlas manualmente con:")
        print(f"  {command}")

    print()
    return tuple(sorted(pip_packages))


def _offer_dependency_installation(pip_packages: Sequence[str]) -> None:
    """Interactively offer to install missing optional dependencies via pip."""

    if not pip_packages or not sys.stdin.isatty():
        return

    prompt = "¿Quieres que intentemos instalarlas ahora? [s/N]: "
    try:
        answer = input(prompt)
    except EOFError:
        return

    if answer.strip().lower() not in {"s", "si", "sí"}:
        return

    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", *pip_packages],
        check=False,
    )
    if result.returncode == 0:
        print("Instalación completada. Reinicia la herramienta si los módulos siguen sin aparecer.\n")
    else:
        print("No se pudieron instalar todas las dependencias. Revisa el registro anterior para más detalles.\n")

def _build_cli_parser() -> argparse.ArgumentParser:
    """Return an argument parser for the lightweight CLI helpers."""

    parser = argparse.ArgumentParser(
        description="Inspect or extract Asura RS managed archives.",
        add_help=True,
    )
    subparsers = parser.add_subparsers(dest="command")

    extract_parser = subparsers.add_parser(
        "extract",
        help="Extract an archive without launching the GUI.",
        description="Write the contents of an Asura archive to the given directory.",
    )
    extract_parser.add_argument(
        "archive",
        type=Path,
        help="Path to the Asura archive (RS) file to extract.",
    )
    extract_parser.add_argument(
        "output",
        type=Path,
        help="Directory where the extracted files and manifest will be written.",
    )

    return parser

def _dispatch_cli(command: str | None, namespace: argparse.Namespace) -> bool:
    """Execute a CLI command if requested."""

    if command == "extract":
        archive_path: Path = namespace.archive
        output_dir: Path = namespace.output
        manifest_path = extract_archive(archive_path, output_dir)
        print(f"Extracted {archive_path} -> {output_dir}\nManifest written to {manifest_path}")
        return True

    return False

def main(argv: Sequence[str] | None = None) -> None:
    """Dispatch CLI commands or launch the GUI application."""

    missing = _gather_missing_optional_dependencies()
    pip_packages = _emit_dependency_report(missing)
    _offer_dependency_installation(pip_packages)

    parser = _build_cli_parser()
    namespace = parser.parse_args(argv)
    handled = _dispatch_cli(namespace.command, namespace)
    if not handled:
        launch_gui()

if __name__ == "__main__":  # pragma: no cover
    main(sys.argv[1:])
