# DIAG(neverdead-resolve-map): resuelve direcciones de crash_diag.log contra
# neverdead.map (sin PDB). Uso:
#   python resolve_map.py build_clang/crash_diag.log
# Necesita base fija (link con /DYNAMICBASE:NO). Parsea "Address Publics by
# Value" y busca el simbolo con mayor direccion <= PC.

import re
import sys
from pathlib import Path

MAP = Path(__file__).resolve().parent / "build_clang" / "neverdead.map"

LINE_RE = re.compile(
    r"^\s+[0-9a-fA-F]{4}:[0-9a-fA-F]{8}\s+(\S+)\s+([0-9a-fA-F]{16})\s"
)
ADDR_RE = re.compile(r"(?:0x|0X)([0-9a-fA-F]{8,16})")


def load_symbols():
    syms = []
    for line in MAP.read_text(errors="replace").splitlines():
        m = LINE_RE.match(line)
        if m:
            name, addr = m.group(1), int(m.group(2), 16)
            syms.append((addr, name))
    syms.sort()
    return syms


def resolve(syms, addr):
    lo, hi = 0, len(syms) - 1
    best = None
    while lo <= hi:
        mid = (lo + hi) // 2
        if syms[mid][0] <= addr:
            best = syms[mid]
            lo = mid + 1
        else:
            hi = mid - 1
    if best is None:
        return None
    return best[0], best[1]


def main():
    log = Path(sys.argv[1]) if len(sys.argv) > 1 else (
        Path(__file__).resolve().parent / "build_clang" / "crash_diag.log"
    )
    syms = load_symbols()
    print(f"map: {len(syms)} simbolos")
    for line in log.read_text(errors="replace").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if "ACCESS VIOLATION" in stripped or "faulting" in stripped or stripped == "stack:":
            print(stripped)
            continue
        m = ADDR_RE.search(stripped.replace("(", " ").replace(")", " "))
        addrs = [int(a, 16) for a in ADDR_RE.findall(stripped.replace("(", " ").replace(")", " "))]
        if not addrs:
            continue
        # el ultimo hex de la linea suele ser la direccion; probar todos
        resolved = []
        for a in addrs:
            r = resolve(syms, a)
            if r:
                resolved.append(f"0x{a:X} -> {r[1]} +0x{a - r[0]:X}")
        if resolved:
            print(stripped)
            for r in resolved:
                print(f"      {r}")
        else:
            print(stripped)


if __name__ == "__main__":
    main()
