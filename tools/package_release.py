#!/usr/bin/env python3
"""
Neverdeth - Release Packaging Script (v0.1.1-alpha)
Builds, audits repository/binary hygiene, and packages the public release.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DIST_ROOT = os.path.join(ROOT, "dist")
PACKAGE_NAME = "Neverdeth-v0.1.1-alpha-win64"
STAGE_DIR = os.path.join(DIST_ROOT, PACKAGE_NAME)
ZIP_OUTPUT = os.path.join(DIST_ROOT, f"{PACKAGE_NAME}.zip")
LOCAL_RULES_PATH = os.path.join(ROOT, "_dev_archive", "hygiene_rules.json")

FORBIDDEN_EXTENSIONS = {
    ".xex", ".iso", ".data", ".asrbe", ".bik", ".xsh", ".xpso", ".pdb", ".log"
}
ZIP_FORBIDDEN_EXTENSIONS = FORBIDDEN_EXTENSIONS | {".bak"}

REPO_AUDIT_TARGETS = [
    "src", "config", "docs", "tools", "patches",
    "CMakeLists.txt", "CMakePresets.json", "neverdead.toml",
    "neverdead_manifest.toml", "LICENSE", ".gitignore",
    "README.md", "README.es.md",
]
TEXT_FILE_EXTS = {
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx",
    ".py", ".md", ".txt", ".ini", ".toml", ".cmake", ".rc",
    ".yml", ".yaml", ".json", ".ps1", ".bat", ".sh", "",
}
STAGED_TEXT_EXTS = {".txt", ".md", ".ini", ".toml"}

# Generic public hygiene checks (no personal identifiers or environment literals).
GENERIC_TEXT_PATTERNS = [
    r"[A-Za-z]:[/\\]Users[/\\]",
    r"v0\.1\.0",
]
GENERIC_BINARY_PATTERNS = [
    r"[A-Za-z]:[/\\]Users[/\\]",
    r"D:[/\\]",
    r"v0\.1\.0",
]


def _load_local_rules() -> dict:
    if not os.path.isfile(LOCAL_RULES_PATH):
        return {}
    try:
        with open(LOCAL_RULES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _compile_patterns(patterns: list[str]) -> re.Pattern[str] | None:
    filtered = [p for p in patterns if p]
    if not filtered:
        return None
    return re.compile("|".join(filtered), re.IGNORECASE)


_LOCAL_RULES = _load_local_rules()
FORBIDDEN_NAME_PATTERNS: list[str] = list(_LOCAL_RULES.get("forbidden_name_patterns", []))

_EXTRA_TEXT = list(_LOCAL_RULES.get("text_patterns", []))
_SESSION_TEXT = _LOCAL_RULES.get("session_tag_text_pattern", "")
_SESSION_BIN = _LOCAL_RULES.get("session_tag_bin_pattern", "")
_PUBLIC_DIAG = list(_LOCAL_RULES.get("public_diag_patterns", []))
_PPC_ALLOW_PAT = _LOCAL_RULES.get("ppc_reg_line_pattern", "")

REPO_COMMON_RX = _compile_patterns(
    GENERIC_TEXT_PATTERNS + _EXTRA_TEXT + ([_SESSION_TEXT] if _SESSION_TEXT else [])
)
DIAG_PUBLIC_RX = _compile_patterns(_PUBLIC_DIAG)
STAGE_TEXT_RX = _compile_patterns(
    GENERIC_TEXT_PATTERNS
    + _EXTRA_TEXT
    + ([_SESSION_TEXT] if _SESSION_TEXT else [])
    + _PUBLIC_DIAG
)
BIN_STRING_RX = _compile_patterns(
    GENERIC_BINARY_PATTERNS
    + _EXTRA_TEXT
    + ([_SESSION_BIN] if _SESSION_BIN else [])
    + _PUBLIC_DIAG
)
PPC_REG_LINE_RX = re.compile(_PPC_ALLOW_PAT) if _PPC_ALLOW_PAT else None


def resolve_build_dir(build_dir_arg: str) -> str:
    if os.path.isabs(build_dir_arg):
        return os.path.normpath(build_dir_arg)
    return os.path.normpath(os.path.join(ROOT, build_dir_arg))


def compile_binary(build_dir: str) -> None:
    print(f"[1/5] Compiling Neverdeth binary with ninja ({build_dir})...")
    cmd = ["ninja", "-C", build_dir, "neverdead"]
    res = subprocess.run(cmd, cwd=ROOT)
    if res.returncode != 0:
        raise RuntimeError("Compilation failed with ninja.")
    exe_path = os.path.join(build_dir, "Neverdeth.exe")
    if not os.path.exists(exe_path):
        raise FileNotFoundError(f"Binary not found: {exe_path}")
    print("  >> Compilation succeeded.")


def prepare_staging(build_dir: str) -> None:
    print("[2/5] Preparing staging directory...")
    if os.path.exists(STAGE_DIR):
        shutil.rmtree(STAGE_DIR)
    os.makedirs(STAGE_DIR, exist_ok=True)

    binaries = [
        ("Neverdeth.exe", "Neverdeth.exe"),
        ("rexgpu-xenos.dll", "rexgpu-xenos.dll"),
        ("rexruntime.dll", "rexruntime.dll"),
    ]
    for src_name, dst_name in binaries:
        src = os.path.join(build_dir, src_name)
        if not os.path.exists(src):
            if src_name == "Neverdeth.exe":
                raise FileNotFoundError(f"Required binary not found: {src}")
            sdk_bin = os.path.join(ROOT, "..", "rexglue_sdk", "win-amd64", "bin", src_name)
            if os.path.exists(sdk_bin):
                src = sdk_bin
            else:
                raise FileNotFoundError(f"Required binary not found: {src_name}")
        shutil.copy2(src, os.path.join(STAGE_DIR, dst_name))
        print(f"  [COPIED] {dst_name} ({os.path.getsize(src):,} bytes)")

    toml_path = os.path.join(ROOT, "neverdead.toml")
    if os.path.exists(toml_path):
        shutil.copy2(toml_path, os.path.join(STAGE_DIR, "neverdead.toml"))
        print("  [COPIED] neverdead.toml")

    os.makedirs(os.path.join(STAGE_DIR, "config"), exist_ok=True)
    for cfg_name in ("settings.ini", "neverdeth.ico"):
        cfg_src = os.path.join(ROOT, "config", cfg_name)
        if os.path.exists(cfg_src):
            shutil.copy2(cfg_src, os.path.join(STAGE_DIR, "config", cfg_name))
            print(f"  [COPIED] config/{cfg_name}")

    for doc_name in ("README.md", "README.es.md", "LICENSE"):
        doc_src = os.path.join(ROOT, doc_name)
        if os.path.exists(doc_src):
            shutil.copy2(doc_src, os.path.join(STAGE_DIR, doc_name))
            print(f"  [COPIED] {doc_name}")

    os.makedirs(os.path.join(STAGE_DIR, "docs"), exist_ok=True)
    rn = os.path.join(ROOT, "docs", "RELEASE_NOTES_v0.1.1-alpha.md")
    if os.path.exists(rn):
        shutil.copy2(rn, os.path.join(STAGE_DIR, "docs", "RELEASE_NOTES_v0.1.1-alpha.md"))
        print("  [COPIED] docs/RELEASE_NOTES_v0.1.1-alpha.md")

    for asset_name in ("neverdeth_logo_banner.bmp", "neverdeth_logo_transparent.png"):
        asset_path = os.path.join(ROOT, "docs", "assets", asset_name)
        if os.path.exists(asset_path):
            os.makedirs(os.path.join(STAGE_DIR, "docs", "assets"), exist_ok=True)
            shutil.copy2(asset_path, os.path.join(STAGE_DIR, "docs", "assets", asset_name))
            print(f"  [COPIED] docs/assets/{asset_name}")

    # Both assets/ and cache/ are populated at runtime; never ship local caches.
    os.makedirs(os.path.join(STAGE_DIR, "assets"), exist_ok=True)
    os.makedirs(os.path.join(STAGE_DIR, "cache"), exist_ok=True)
    print("  [PREPARED] empty assets/ and cache/ directories")


def _iter_repo_text_files():
    for target in REPO_AUDIT_TARGETS:
        full_target = os.path.join(ROOT, target)
        if os.path.isfile(full_target):
            yield full_target
            continue
        if not os.path.isdir(full_target):
            continue
        for cur_root, dirs, files in os.walk(full_target):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for fname in files:
                fpath = os.path.join(cur_root, fname)
                ext = os.path.splitext(fname)[1].lower()
                if ext in TEXT_FILE_EXTS:
                    yield fpath


def audit_repo_hygiene() -> list[str]:
    print("[HYGIENE] Scanning tracked repository files...")
    violations: list[str] = []
    ppc_ctx_rel = os.path.normcase(os.path.join("patches", "sdk", "rex", "ppc", "context.h"))

    for fpath in _iter_repo_text_files():
        rel_path = os.path.relpath(fpath, ROOT)
        rel_norm = os.path.normcase(rel_path)
        try:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.read().splitlines()
        except OSError as exc:
            print(f"  [WARN] Could not read {rel_path}: {exc}")
            continue

        is_public_doc = (
            rel_norm.startswith(os.path.normcase("docs" + os.sep))
            or rel_norm in {
                os.path.normcase("README.md"),
                os.path.normcase("README.es.md"),
                os.path.normcase(os.path.join("config", "settings.ini")),
            }
        )

        for lineno, line in enumerate(lines, 1):
            m = REPO_COMMON_RX.search(line) if REPO_COMMON_RX else None
            if m:
                if rel_norm == ppc_ctx_rel and PPC_REG_LINE_RX and PPC_REG_LINE_RX.search(line):
                    pass
                else:
                    violations.append(f"{rel_path}:{lineno}: forbidden term '{m.group(0)}'")

            if is_public_doc and DIAG_PUBLIC_RX:
                pm = DIAG_PUBLIC_RX.search(line)
                if pm:
                    violations.append(
                        f"{rel_path}:{lineno}: public diagnostic leak '{pm.group(0)}'"
                    )

    if violations:
        print(f"  [FAIL] Repository hygiene found {len(violations)} violation(s).")
    else:
        print("  >> Repository hygiene PASSED: 0 violations.")
    return violations


def audit_binary_strings(exe_path: str) -> list[str]:
    print(f"[BINARY] Scanning extracted strings in {os.path.relpath(exe_path, ROOT)}...")
    violations: list[str] = []
    if not os.path.exists(exe_path):
        violations.append(f"Binary missing for string audit: {exe_path}")
        return violations

    with open(exe_path, "rb") as f:
        data = f.read()

    seen: set[tuple[str, str]] = set()
    for pattern, encoding in (
        (rb"[\x20-\x7e]{4,}", "ascii"),
        (rb"(?:[\x20-\x7e]\x00){4,}", "utf-16-le"),
    ):
        for match in re.finditer(pattern, data):
            s = match.group(0).decode(encoding, errors="ignore")
            m = BIN_STRING_RX.search(s) if BIN_STRING_RX else None
            if m:
                key = (m.group(0).lower(), s[:80])
                if key not in seen:
                    seen.add(key)
                    violations.append(
                        f"Binary string match '{m.group(0)}' in {os.path.basename(exe_path)}: {s[:100]!r}"
                    )

    if violations:
        print(f"  [FAIL] Binary string audit found {len(violations)} violation(s).")
    else:
        print("  >> Binary string audit PASSED: 0 forbidden strings.")
    return violations


def audit_security(stage_dir: str, repo_violations: list[str] | None = None) -> None:
    print("[3/5] Running Security Audit (Zero Proprietary Assets & Hygiene)...")
    violations: list[str] = list(repo_violations) if repo_violations is not None else audit_repo_hygiene()

    for root, _dirs, files in os.walk(stage_dir):
        for fname in files:
            full_path = os.path.join(root, fname)
            rel_path = os.path.relpath(full_path, stage_dir)
            f_lower = fname.lower()
            ext = os.path.splitext(f_lower)[1]

            if ext in ZIP_FORBIDDEN_EXTENSIONS:
                violations.append(f"Forbidden extension '{ext}': {rel_path}")

            for pat in FORBIDDEN_NAME_PATTERNS:
                if fnmatch.fnmatch(f_lower, pat):
                    violations.append(f"Forbidden filename pattern '{pat}': {rel_path}")

            if ext in STAGED_TEXT_EXTS:
                try:
                    with open(full_path, "r", encoding="utf-8", errors="ignore") as tf:
                        for lineno, line in enumerate(tf, 1):
                            m = STAGE_TEXT_RX.search(line) if STAGE_TEXT_RX else None
                            if m:
                                violations.append(
                                    f"Staged text {rel_path}:{lineno}: forbidden term '{m.group(0)}'"
                                )
                except OSError as exc:
                    print(f"  [WARN] Could not inspect {rel_path}: {exc}")

    staged_exe = os.path.join(stage_dir, "Neverdeth.exe")
    if os.path.exists(staged_exe):
        violations.extend(audit_binary_strings(staged_exe))

    _raise_on_violations(violations)
    print("  >> Security Audit PASSED: 0 proprietary assets and 0 hygiene violations.")


def _raise_on_violations(violations: list[str]) -> None:
    if not violations:
        return
    print("\n" + "!" * 70)
    print("SECURITY AUDIT FAILED! The following violations were detected:")
    for v in violations:
        print(f"  - {v}")
    print("!" * 70 + "\n")
    raise RuntimeError(f"Security audit rejected the package ({len(violations)} violations).")


def create_zip() -> None:
    print("[4/5] Creating release ZIP archive...")
    if os.path.exists(ZIP_OUTPUT):
        os.remove(ZIP_OUTPUT)

    with zipfile.ZipFile(ZIP_OUTPUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for root, dirs, files in os.walk(STAGE_DIR):
            for d in dirs:
                full_d = os.path.join(root, d)
                rel_d = os.path.relpath(full_d, STAGE_DIR).replace("\\", "/") + "/"
                zinfo = zipfile.ZipInfo(PACKAGE_NAME + "/" + rel_d)
                zf.writestr(zinfo, "")

            for f in files:
                full_f = os.path.join(root, f)
                rel_f = os.path.relpath(full_f, STAGE_DIR).replace("\\", "/")
                arcname = PACKAGE_NAME + "/" + rel_f
                zf.write(full_f, arcname)

    zip_size = os.path.getsize(ZIP_OUTPUT)
    print(f"  >> Package created: {ZIP_OUTPUT} ({zip_size / (1024*1024):.2f} MB / {zip_size:,} bytes)")


def verify_zip() -> None:
    print("[5/5] Verifying ZIP archive integrity...")
    with zipfile.ZipFile(ZIP_OUTPUT, "r") as zf:
        namelist = zf.namelist()
        print(f"  Archive contains {len(namelist)} entries:")
        for name in sorted(namelist):
            info = zf.getinfo(name)
            is_dir = name.endswith("/")
            tag = "[DIR] " if is_dir else f"{info.file_size:>10,} B "
            print(f"    {tag} {name}")

        for name in namelist:
            name_lower = name.lower()
            for ext in ZIP_FORBIDDEN_EXTENSIONS:
                if name_lower.endswith(ext):
                    raise RuntimeError(f"Forbidden asset found inside ZIP: {name}")
            for pat in FORBIDDEN_NAME_PATTERNS:
                if fnmatch.fnmatch(os.path.basename(name_lower), pat):
                    raise RuntimeError(f"Forbidden internal file found inside ZIP: {name}")

    print("  >> ZIP integrity and security verification PASSED.")


def run_audit_only(build_dir: str) -> int:
    print("=" * 70)
    print(f"Running Hygiene Audit Only (build_dir={build_dir})")
    print("=" * 70)
    violations = audit_repo_hygiene()

    exe_path = os.path.join(build_dir, "Neverdeth.exe")
    if os.path.exists(exe_path):
        violations.extend(audit_binary_strings(exe_path))
    else:
        print(f"  [WARN] Binary not found for string scan: {exe_path}")

    if os.path.exists(STAGE_DIR):
        try:
            audit_security(STAGE_DIR, repo_violations=violations)
            return 0
        except RuntimeError:
            return 1

    if violations:
        _raise_on_violations(violations)
    print("  >> Hygiene Audit PASSED.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Build, audit, and package the Neverdeth public release ZIP."
    )
    ap.add_argument(
        "--build-dir",
        default="build_clang",
        help="Build directory containing Neverdeth.exe (default: build_clang)",
    )
    ap.add_argument(
        "--audit-only",
        action="store_true",
        help="Run repository and binary hygiene checks without rebuilding or zipping",
    )
    args = ap.parse_args()
    build_dir = resolve_build_dir(args.build_dir)

    if args.audit_only:
        try:
            return run_audit_only(build_dir)
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    print("=" * 70)
    print(f"Packaging Release: {PACKAGE_NAME}")
    print("=" * 70)
    repo_violations = audit_repo_hygiene()
    if repo_violations:
        _raise_on_violations(repo_violations)
    compile_binary(build_dir)
    prepare_staging(build_dir)
    audit_security(STAGE_DIR, repo_violations=[])
    create_zip()
    verify_zip()
    print("=" * 70)
    print("SUCCESS: Release archive ready at:")
    print(f"  {ZIP_OUTPUT}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
