#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Idempotently injects host runtime hooks into the generated C++ translation units.

Uses nd_tu.tu_of_func (with static fallback TU filenames) to locate target
functions across codegen runs and safely rewrites files with read-only flag
clearing and short retries.
"""
from __future__ import annotations

import os
import stat
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

try:
    import nd_tu
except ImportError:
    nd_tu = None


@dataclass(frozen=True)
class HookSpec:
    """Definition of a single generated-code hook injection."""

    func_name: str
    fallback_tu: str
    marker: str
    target: str
    replacement: str


HOOKS: tuple[HookSpec, ...] = (
    HookSpec(
        func_name="sub_826B1F88",
        fallback_tu="neverdead_recomp.98.cpp",
        marker="NdOnDrawCall",
        target="DEFINE_REX_FUNC(sub_826B1F88) {\n\tREX_FUNC_PROLOGUE();",
        replacement=(
            'extern "C" void NdOnDrawCall();\n'
            "DEFINE_REX_FUNC(sub_826B1F88) {\n"
            "\tREX_FUNC_PROLOGUE();\n"
            "\tNdOnDrawCall();"
        ),
    ),
    HookSpec(
        func_name="sub_823ED208",
        fallback_tu="neverdead_recomp.67.cpp",
        marker="NdShouldSkipLumaResolve",
        target="DEFINE_REX_FUNC(sub_823ED208) {\n\tREX_FUNC_PROLOGUE();",
        replacement=(
            'extern "C" bool NdShouldSkipLumaResolve();\n'
            "DEFINE_REX_FUNC(sub_823ED208) {\n"
            "\tREX_FUNC_PROLOGUE();\n"
            "\tif (NdShouldSkipLumaResolve()) return;"
        ),
    ),
    HookSpec(
        func_name="sub_826D0820",
        fallback_tu="neverdead_recomp.39.cpp",
        marker="NdAutoAdvanceInput",
        target=(
            "DEFINE_REX_FUNC(sub_826D0820) {\n"
            "\tREX_FUNC_PROLOGUE();\n"
            "\t// mr r5,r4\n"
            "\tctx.r5.u64 = ctx.r4.u64;\n"
            "\t// li r4,1\n"
            "\tctx.r4.s64 = 1;\n"
            "\t// b 0x827bf7c4\n"
            "\t__imp__XamInputGetState(ctx, base);\n"
            "\treturn;"
        ),
        replacement=(
            "void NdAutoAdvanceInput(PPCContext& ctx, uint8_t* base, uint32_t guest_state);\n\n"
            "DEFINE_REX_FUNC(sub_826D0820) {\n"
            "\tREX_FUNC_PROLOGUE();\n"
            "\tconst uint32_t guest_state = ctx.r4.u32;\n"
            "\t// mr r5,r4\n"
            "\tctx.r5.u64 = ctx.r4.u64;\n"
            "\t// li r4,1\n"
            "\tctx.r4.s64 = 1;\n"
            "\t// b 0x827bf7c4\n"
            "\t__imp__XamInputGetState(ctx, base);\n"
            "\tNdAutoAdvanceInput(ctx, base, guest_state);\n"
            "\treturn;"
        ),
    ),
    HookSpec(
        func_name="sub_826B6B38",
        fallback_tu="neverdead_recomp.87.cpp",
        marker="NdResetXdkShadowStateOnBoundary",
        target="DEFINE_REX_FUNC(sub_826B6B38) {\n\tREX_FUNC_PROLOGUE();",
        replacement=(
            'extern "C" void NdResetXdkShadowStateOnBoundary();\n'
            "DEFINE_REX_FUNC(sub_826B6B38) {\n"
            "\tREX_FUNC_PROLOGUE();\n"
            "\tNdResetXdkShadowStateOnBoundary();"
        ),
    ),
)


def get_gen_dir() -> Path:
    """Resolve the generated C++ directory."""
    if nd_tu is not None:
        return nd_tu.gen_dir(ROOT)
    override = os.environ.get("NEVERDEAD_GENERATED_DIR")
    if override:
        p = Path(override)
        return p if p.is_absolute() else (ROOT / p)
    return ROOT / "generated" / "neverdead_default"


def _write_text_robust(path: Path, content: str) -> None:
    """Clear read-only attributes and retry briefly to avoid Windows file-lock races."""
    try:
        path.chmod(stat.S_IWRITE)
    except OSError:
        pass
    last_err: OSError | None = None
    for _ in range(5):
        try:
            path.write_text(content, encoding="utf-8", newline="\n")
            return
        except OSError as exc:
            last_err = exc
            time.sleep(0.2)
    assert last_err is not None
    raise last_err


def _resolve_tu(gen_dir: Path, spec: HookSpec) -> Path:
    tu = nd_tu.tu_of_func(gen_dir, spec.func_name) if nd_tu else None
    if tu is not None and tu.is_file():
        return tu
    return gen_dir / spec.fallback_tu


def patch_file(path: Path, spec: HookSpec) -> bool:
    """Inject a single hook into `path` if not already present."""
    if not path.is_file():
        print(f"[WARN] File not found: {path}")
        return False

    content = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
    if spec.marker in content:
        print(f"[SKIP] {path.name}: {spec.marker} already present.")
        return True

    if spec.target not in content:
        print(f"[FAIL] {path.name}: target pattern for {spec.func_name} not found.")
        return False

    content = content.replace(spec.target, spec.replacement, 1)
    _write_text_robust(path, content)
    print(f"[OK]   {path.name}: injected {spec.marker} into {spec.func_name}.")
    return True


def main() -> int:
    """Apply all configured hooks to the generated translation units."""
    gen_dir = get_gen_dir()
    print(f"[apply_generated_hooks] Target tree: {gen_dir}")
    if not gen_dir.is_dir():
        print(f"[ERROR] Generated directory not found: {gen_dir}", file=sys.stderr)
        return 1

    success = True
    for spec in HOOKS:
        tu_path = _resolve_tu(gen_dir, spec)
        if not patch_file(tu_path, spec):
            success = False

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
