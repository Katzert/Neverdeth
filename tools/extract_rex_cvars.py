#!/usr/bin/env python3
"""Extrae el inventario real de CVars del runtime ReXGlue.

Escanea los strings de los binarios (rexgpu-xenos.dll, rexruntime.dll,
neverdead.exe) y los cruza con las declaraciones REXCVAR_DEFINE_* del arbol
fuente del SDK para obtener nombre, tipo, default, categoria y descripcion.

Salida: docs/rex_gpu_cvars_list.txt
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PROJ = Path(r"C:\Users\katze\Downloads\_Proyectos_y_Backups\neverdead_project")
SDK_SRC = Path(r"C:\Users\katze\.gemini\antigravity-ide\scratch\vibe-recomp\rexglue_sdk_src")
BINARIES = {
    "rexgpu-xenos.dll": PROJ / "build_clang" / "rexgpu-xenos.dll",
    "rexruntime.dll": PROJ / "build_clang" / "rexruntime.dll",
    "neverdead.exe": PROJ / "build_clang" / "neverdead.exe",
}
TOML = PROJ / "neverdead.toml"
OUT = PROJ / "docs" / "rex_gpu_cvars_list.txt"

# ---------------------------------------------------------------------------
# 1. Strings ASCII/UTF-16 de cada binario
# ---------------------------------------------------------------------------
def extract_ascii(data: bytes, minlen: int = 4) -> set[str]:
    return {m.group().decode("ascii") for m in re.finditer(rb"[\x20-\x7e]{%d,}" % minlen, data)}


def extract_utf16le(data: bytes, minlen: int = 4) -> set[str]:
    # char ASCII \x00 repetido
    pat = rb"(?:[\x20-\x7e]\x00){%d,}" % minlen
    return {m.group().decode("utf-16-le") for m in re.finditer(pat, data)}


bin_strings: dict[str, set[str]] = {}
for label, path in BINARIES.items():
    if not path.exists():
        print(f"AVISO: {path} no existe", file=sys.stderr)
        bin_strings[label] = set()
        continue
    data = path.read_bytes()
    bin_strings[label] = extract_ascii(data) | extract_utf16le(data)

# ---------------------------------------------------------------------------
# 2. Declaraciones REXCVAR_DEFINE_* en el fuente del SDK
# ---------------------------------------------------------------------------
CVAR_RE = re.compile(
    r"REXCVAR_DEFINE_(BOOL|INT32|INT64|STRING|FLOAT|DOUBLE|UINT32)\(\s*"
    r"([a-z0-9_]+)\s*,\s*"
    r"([^,]+?),\s*"
    r'"([^"]*)"\s*,\s*'
    r'"([^"]*)"',
    re.S,
)
LIFECYCLE_RE = re.compile(r"lifecycle\(rex::cvar::Lifecycle::(\w+)\)")

cvars: dict[str, dict] = {}
for cpp in SDK_SRC.rglob("*.cpp"):
    try:
        text = cpp.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    for m in CVAR_RE.finditer(text):
        ctype, name, default, category, desc = m.groups()
        # lifecycle (opcional, en las ~200 chars siguientes)
        tail = text[m.end() : m.end() + 200]
        lm = LIFECYCLE_RE.search(tail)
        default = " ".join(default.split())
        cvars.setdefault(
            name,
            {
                "type": ctype,
                "default": default,
                "category": category,
                "desc": desc,
                "lifecycle": lm.group(1) if lm else "kHotReload",
                "sources": set(),
            },
        )
        rel = cpp.relative_to(SDK_SRC)
        cvars[name]["sources"].add(str(rel))

# ---------------------------------------------------------------------------
# 3. Claves activas en neverdead.toml
# ---------------------------------------------------------------------------
toml_keys: set[str] = set()
if TOML.exists():
    for line in TOML.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*([a-z0-9_]+)\s*=", line)
        if m:
            toml_keys.add(m.group(1))

# ---------------------------------------------------------------------------
# 4. Reporte
# ---------------------------------------------------------------------------
def where(name: str) -> list[str]:
    return [label for label, strs in bin_strings.items() if name in strs]


lines: list[str] = []
lines.append("=" * 100)
lines.append("INVENTARIO DE CVars DEL RUNTIME ReXGlue — rexgpu-xenos.dll / rexruntime.dll")
lines.append("=" * 100)
lines.append("")
lines.append("Fuente de verdad: declaraciones REXCVAR_DEFINE_* en rexglue_sdk_src (el")
lines.append("mismo codigo con el que se compilaron los binarios) + verificacion de que")
lines.append("el nombre existe como string en el binario (ASCII o UTF-16).")
lines.append("")
lines.append(f"CVars declaradas en el fuente : {len(cvars)}")
lines.append(f"Claves fijadas en neverdead.toml: {len(toml_keys)}")
en_bin = sum(1 for n in cvars if where(n))
lines.append(f"CVars con string en algun binario: {en_bin}")
lines.append("")

# --- por binario ---
for label in BINARIES:
    present = sorted(n for n in cvars if label in where(n))
    lines.append("-" * 100)
    lines.append(f"CVars presentes en {label}: {len(present)}")
    lines.append("-" * 100)
    for n in present:
        c = cvars[n]
        toml = "  [TOML]" if n in toml_keys else ""
        lc = f"  <{c['lifecycle']}>" if c["lifecycle"] != "kHotReload" else ""
        lines.append(
            f"  {n:<44} {c['type']:<7} = {c['default']:<12} "
            f"({c['category']}){toml}{lc}"
        )
    lines.append("")

# --- declaradas pero sin string en ningun binario (p.ej. solo codegen CLI) ---
nowhere = sorted(n for n in cvars if not where(n))
lines.append("-" * 100)
lines.append(f"Declaradas en el fuente SIN string en ningun binario: {len(nowhere)}")
lines.append("(compiladas fuera de estos binarios o eliminadas por el enlazador)")
lines.append("-" * 100)
for n in nowhere:
    c = cvars[n]
    lines.append(f"  {n:<44} {c['type']:<7} ({c['category']})  {sorted(c['sources'])[0]}")
lines.append("")

# --- claves del toml que NO son CVars ---
unknown = sorted(toml_keys - set(cvars))
lines.append("-" * 100)
lines.append(f"Claves de neverdead.toml que NO son CVars del runtime: {len(unknown)}")
lines.append("-" * 100)
for n in unknown:
    hits = where(n)
    lines.append(f"  {n:<44} en binarios: {', '.join(hits) if hits else 'NINGUNO'}")
lines.append("")

# --- sospechosas: snake_case en binarios que no son CVars conocidas ---
KNOWN_NOISE = {
    "windows", "undefined", "anonymous", "position", "location", "response",
}
candidates: dict[str, list[str]] = {}
for label, strs in bin_strings.items():
    for s in strs:
        if (
            re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+){1,4}", s)
            and 6 <= len(s) <= 60
            and s not in cvars
            and s not in KNOWN_NOISE
            and not any(x in s for x in ("release", "debug", "assert"))
        ):
            # filtrar nombres de archivo/codigo comunes
            candidates.setdefault(s, []).append(label)

# solo mostrar las que aparecen en el plugin de GPU (las relevantes para 720p)
gpu_only = {s: lbls for s, lbls in candidates.items() if "rexgpu-xenos.dll" in lbls}
lines.append("-" * 100)
lines.append(
    f"Sospechosas (snake_case en rexgpu-xenos.dll, no declaradas como CVar): {len(gpu_only)}"
)
lines.append("(posibles CVars de otra via, nombres de logs, o ruido de import/export)")
lines.append("-" * 100)
for s in sorted(gpu_only)[:120]:
    lines.append(f"  {s:<44} en: {', '.join(gpu_only[s])}")
lines.append("")
lines.append("NOTA: d3d12_tiled_shared_memory controla el mapeo del buffer de 512 MB de")
lines.append("memoria compartida del invitado (tiled/reserved resource vs committed), NO el")
lines.append("predicated tiling de render targets. Lifecycle: kRequiresRestart.")

OUT.write_text("\n".join(lines), encoding="utf-8")
print(f"escrito: {OUT} ({len(lines)} lineas)")
print(f"CVars fuente: {len(cvars)} | en binarios: {en_bin} | toml: {len(toml_keys)}")
