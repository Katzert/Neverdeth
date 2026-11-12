#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nd_tu - Locate translation units (TUs) by function name or label in a recompiled tree.

Since codegen assigns functions across TUs dynamically by block size, patching scripts
resolve target TUs by content (DEFINE_REX_FUNC / label) rather than hardcoded TU indices.

API (returns absolute Paths):
    gen_dir(root)                 -> <root>/generated/neverdead_default
    tu_of_func(gen_dir, name)     -> Path | None     ("sub_82789768" or "82789768")
    tus_of_label(gen_dir, label)  -> list[Path]      ("loc_8278D4D0")
    tu_of_name(gen_dir, name)     -> Path | None     (any DEFINE_REX_FUNC(...))
"""
from __future__ import annotations

import os
import re
from pathlib import Path

FUNC_RE = re.compile(r"DEFINE_REX_FUNC\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)")
LABEL_RE = re.compile(r"^[ \t]*(loc_[0-9A-Fa-f]+):", re.M)

_CACHE: dict[str, tuple[dict[str, str], dict[str, list[str]]]] = {}


def gen_dir(root: Path) -> Path:
    """Return the generated source directory under `root` (overridable via environment)."""
    override = os.environ.get("NEVERDEAD_GENERATED_DIR")
    if override:
        p = Path(override)
        if not p.is_absolute():
            p = Path(root) / p
        return p
    return Path(root) / "generated" / "neverdead_default"


def _scan(gen_dir: Path) -> tuple[dict[str, str], dict[str, list[str]]]:
    key = str(gen_dir)
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    va2tu: dict[str, str] = {}
    label2tu: dict[str, list[str]] = {}
    for cpp in sorted(Path(gen_dir).glob("neverdead_recomp.*.cpp")):
        txt = cpp.read_text(encoding="utf-8", errors="replace")
        for m in FUNC_RE.finditer(txt):
            va2tu.setdefault(m.group(1), cpp.name)
        for m in LABEL_RE.finditer(txt):
            label2tu.setdefault(m.group(1), []).append(cpp.name)
    _CACHE[key] = (va2tu, label2tu)
    return va2tu, label2tu


def _norm(name: str) -> str:
    n = name.strip()
    if n.lower().startswith("sub_"):
        return n
    if n.lower().startswith("loc_"):
        return n
    return f"sub_{n.upper()}"


def tu_of_func(gen_dir: Path, name: str) -> Path | None:
    """Return the TU containing DEFINE_REX_FUNC(<name>). Accepts 'sub_82789768' or '82789768'."""
    va2tu, _ = _scan(gen_dir)
    key = _norm(name)
    hit = va2tu.get(key) or va2tu.get(name)
    return Path(gen_dir) / hit if hit else None


def tu_of_name(gen_dir: Path, name: str) -> Path | None:
    """Return the TU containing DEFINE_REX_FUNC(<name>) for any symbol (e.g. 'xstart')."""
    va2tu, _ = _scan(gen_dir)
    hit = va2tu.get(name)
    return Path(gen_dir) / hit if hit else None


def tu_containing(gen_dir: Path, needle: str) -> Path | None:
    """Return the first TU whose text contains `needle`."""
    for cpp in sorted(Path(gen_dir).glob("neverdead_recomp.*.cpp")):
        if needle in cpp.read_text(encoding="utf-8", errors="replace"):
            return cpp
    return None


def tus_of_label(gen_dir: Path, label: str) -> list[Path]:
    """Return all TUs containing the given label (e.g. 'loc_8278D4D0')."""
    _, label2tu = _scan(gen_dir)
    return [Path(gen_dir) / n for n in label2tu.get(label, [])]


def clear_cache() -> None:
    """Clear the cached TU symbol and label index."""
    _CACHE.clear()
