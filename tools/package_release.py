#!/usr/bin/env python3
"""
Neverdeth - Release Packaging Script (v0.1.0-alpha)
Builds, validates, security-audits and packages the public release.
"""

import os
import sys
import shutil
import zipfile
import subprocess
import fnmatch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DIST_ROOT = os.path.join(ROOT, "dist")
PACKAGE_NAME = "Neverdeth-v0.1.0-alpha-win64"
STAGE_DIR = os.path.join(DIST_ROOT, PACKAGE_NAME)
ZIP_OUTPUT = os.path.join(DIST_ROOT, f"{PACKAGE_NAME}.zip")

# Archivos propietarios y de auditoria interna prohibidos en el paquete
FORBIDDEN_EXTENSIONS = {".xex", ".iso", ".data", ".asrbe", ".bik"}
FORBIDDEN_NAME_PATTERNS = [
    "*auditoria_*", "*informe_*", "*recap_*", "*dossier*"
]

# Palabras prohibidas en archivos de texto publicos (Anti-Echoing)
# NOTA: 'Gemini Argon' esta explicitamente autorizado en documentacion publica.
FORBIDDEN_TEXT_TERMS = [
    bytes.fromhex("475241464f5f4152474f4e").decode(),
    "Turno v12", "Turno v13",
    "hospital", "bench_ring", "sub_826B", "0x826B8DCC",
    "NdVdSwapPostHook", "NdRingProbe"
]

def compile_binary():
    print("[1/5] Compiling Neverdeth binary with ninja...")
    build_dir = os.path.join(ROOT, "build_clang")
    cmd = ["ninja", "-C", build_dir, "neverdead"]
    res = subprocess.run(cmd, cwd=ROOT)
    if res.returncode != 0:
        raise RuntimeError("Compilation failed with ninja!")
    exe_path = os.path.join(build_dir, "Neverdeth.exe")
    if not os.path.exists(exe_path):
        fallback_exe = os.path.join(build_dir, "neverdead.exe")
        if os.path.exists(fallback_exe):
            shutil.copy2(fallback_exe, exe_path)
        else:
            raise FileNotFoundError(f"Binary not found: {exe_path}")
    print("  >> Compilation succeeded.")

def prepare_staging():
    print("[2/5] Preparing staging directory...")
    if os.path.exists(STAGE_DIR):
        shutil.rmtree(STAGE_DIR)
    os.makedirs(STAGE_DIR, exist_ok=True)

    # Copiar binarios y DLLs requeridas
    build_dir = os.path.join(ROOT, "build_clang")
    binaries = [
        ("Neverdeth.exe", "Neverdeth.exe"),
        ("rexgpu-xenos.dll", "rexgpu-xenos.dll"),
        ("rexruntime.dll", "rexruntime.dll"),
    ]
    for src_name, dst_name in binaries:
        src = os.path.join(build_dir, src_name)
        if not os.path.exists(src):
            if src_name == "Neverdeth.exe":
                src = os.path.join(build_dir, "neverdead.exe")
            else:
                sdk_bin = os.path.join(ROOT, "..", "rexglue_sdk", "win-amd64", "bin", src_name)
                if os.path.exists(sdk_bin):
                    src = sdk_bin
                else:
                    raise FileNotFoundError(f"Required binary not found: {src_name}")
        shutil.copy2(src, os.path.join(STAGE_DIR, dst_name))
        print(f"  [COPIED] {dst_name} ({os.path.getsize(src):,} bytes)")

    # Copiar archivo de configuracion maestro neverdead.toml
    toml_path = os.path.join(ROOT, "neverdead.toml")
    if os.path.exists(toml_path):
        shutil.copy2(toml_path, os.path.join(STAGE_DIR, "neverdead.toml"))
        print(f"  [COPIED] neverdead.toml")

    # Copiar configuracion
    os.makedirs(os.path.join(STAGE_DIR, "config"), exist_ok=True)
    cfg_ini = os.path.join(ROOT, "config", "settings.ini")
    if os.path.exists(cfg_ini):
        shutil.copy2(cfg_ini, os.path.join(STAGE_DIR, "config", "settings.ini"))
        print(f"  [COPIED] config/settings.ini")
    cfg_ico = os.path.join(ROOT, "config", "neverdeth.ico")
    if os.path.exists(cfg_ico):
        shutil.copy2(cfg_ico, os.path.join(STAGE_DIR, "config", "neverdeth.ico"))
        print(f"  [COPIED] config/neverdeth.ico")

    # Copiar documentacion
    readme = os.path.join(ROOT, "README.md")
    if os.path.exists(readme):
        shutil.copy2(readme, os.path.join(STAGE_DIR, "README.md"))
        print(f"  [COPIED] README.md")

    readme_es = os.path.join(ROOT, "README.es.md")
    if os.path.exists(readme_es):
        shutil.copy2(readme_es, os.path.join(STAGE_DIR, "README.es.md"))
        print(f"  [COPIED] README.es.md")

    os.makedirs(os.path.join(STAGE_DIR, "docs"), exist_ok=True)
    rn = os.path.join(ROOT, "docs", "RELEASE_NOTES_v0.1.0-alpha.md")
    if os.path.exists(rn):
        shutil.copy2(rn, os.path.join(STAGE_DIR, "docs", "RELEASE_NOTES_v0.1.0-alpha.md"))
        print(f"  [COPIED] docs/RELEASE_NOTES_v0.1.0-alpha.md")

    banner = os.path.join(ROOT, "docs", "assets", "neverdeth_logo_banner.bmp")
    if os.path.exists(banner):
        os.makedirs(os.path.join(STAGE_DIR, "docs", "assets"), exist_ok=True)
        shutil.copy2(banner, os.path.join(STAGE_DIR, "docs", "assets", "neverdeth_logo_banner.bmp"))
        print(f"  [COPIED] docs/assets/neverdeth_logo_banner.bmp")

    # Crear carpeta assets vacia y copiar archivos portables de cache
    os.makedirs(os.path.join(STAGE_DIR, "assets"), exist_ok=True)
    os.makedirs(os.path.join(STAGE_DIR, "cache"), exist_ok=True)
    cache_src = os.path.join(ROOT, "cache")
    cache_dst = os.path.join(STAGE_DIR, "cache")
    if os.path.exists(cache_src):
        for root, dirs, files in os.walk(cache_src):
            for f in files:
                ext = os.path.splitext(f.lower())[1]
                if ext in {".bin", ".xpso", ".xsh"}:
                    rel_p = os.path.relpath(os.path.join(root, f), cache_src)
                    dst_p = os.path.join(cache_dst, rel_p)
                    os.makedirs(os.path.dirname(dst_p), exist_ok=True)
                    shutil.copy2(os.path.join(root, f), dst_p)
                    print(f"  [COPIED] cache/{rel_p}")
    print("  [PREPARED] assets/ and cache/ directories")

def audit_security(stage_dir):
    print("[3/5] Running Security Audit (Zero Proprietary Assets & Anti-Echoing)...")
    violations = []

    for root, dirs, files in os.walk(stage_dir):
        for f in files:
            full_path = os.path.join(root, f)
            rel_path = os.path.relpath(full_path, stage_dir)
            f_lower = f.lower()

            # 1. Extension check
            ext = os.path.splitext(f_lower)[1]
            if ext in FORBIDDEN_EXTENSIONS:
                violations.append(f"Forbidden extension '{ext}': {rel_path}")

            # 2. Pattern check
            for pat in FORBIDDEN_NAME_PATTERNS:
                if fnmatch.fnmatch(f_lower, pat):
                    violations.append(f"Forbidden filename pattern '{pat}': {rel_path}")

            # 3. Text content anti-echoing check (only on txt, md, ini, toml)
            if ext in {".txt", ".md", ".ini", ".toml"}:
                try:
                    with open(full_path, "r", encoding="utf-8", errors="ignore") as tf:
                        content = tf.read()
                        for term in FORBIDDEN_TEXT_TERMS:
                            if term in content:
                                violations.append(f"Forbidden term '{term}' found in text file: {rel_path}")
                except Exception as e:
                    print(f"  [WARN] Could not inspect {rel_path}: {e}")

    if violations:
        print("\n" + "!" * 70)
        print("SECURITY AUDIT FAILED! The following violations were detected:")
        for v in violations:
            print(f"  - {v}")
        print("!" * 70 + "\n")
        raise RuntimeError("Security audit rejected the package.")

    print("  >> Security Audit PASSED: 0 proprietary assets and 0 internal reports detected.")

def create_zip():
    print("[4/5] Creating release ZIP archive...")
    if os.path.exists(ZIP_OUTPUT):
        os.remove(ZIP_OUTPUT)

    with zipfile.ZipFile(ZIP_OUTPUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for root, dirs, files in os.walk(STAGE_DIR):
            for d in dirs:
                full_d = os.path.join(root, d)
                rel_d = os.path.relpath(full_d, STAGE_DIR).replace("\\", "/") + "/"
                # Ensure empty directory entries exist in zip
                zinfo = zipfile.ZipInfo(PACKAGE_NAME + "/" + rel_d)
                zf.writestr(zinfo, "")

            for f in files:
                full_f = os.path.join(root, f)
                rel_f = os.path.relpath(full_f, STAGE_DIR).replace("\\", "/")
                arcname = PACKAGE_NAME + "/" + rel_f
                zf.write(full_f, arcname)

    zip_size = os.path.getsize(ZIP_OUTPUT)
    print(f"  >> Package created: {ZIP_OUTPUT} ({zip_size / (1024*1024):.2f} MB / {zip_size:,} bytes)")

def verify_zip():
    print("[5/5] Verifying ZIP archive integrity...")
    with zipfile.ZipFile(ZIP_OUTPUT, "r") as zf:
        namelist = zf.namelist()
        print(f"  Archive contains {len(namelist)} entries:")
        for name in sorted(namelist):
            info = zf.getinfo(name)
            is_dir = name.endswith("/")
            tag = "[DIR] " if is_dir else f"{info.file_size:>10,} B "
            print(f"    {tag} {name}")

        # Assert no forbidden file inside zip
        for name in namelist:
            name_lower = name.lower()
            for ext in FORBIDDEN_EXTENSIONS:
                if name_lower.endswith(ext):
                    raise RuntimeError(f"Forbidden asset found inside ZIP: {name}")
            for pat in FORBIDDEN_NAME_PATTERNS:
                if fnmatch.fnmatch(os.path.basename(name_lower), pat):
                    raise RuntimeError(f"Forbidden internal audit file found inside ZIP: {name}")

    print("  >> ZIP integrity and security verification PASSED.")

def main():
    print("=" * 70)
    print(f"Packaging Release: {PACKAGE_NAME}")
    print("=" * 70)
    compile_binary()
    prepare_staging()
    audit_security(STAGE_DIR)
    create_zip()
    verify_zip()
    print("=" * 70)
    print(f"SUCCESS: Release archive ready at:")
    print(f"  {ZIP_OUTPUT}")
    print("=" * 70)

if __name__ == "__main__":
    main()
