#!/usr/bin/env python3
"""m3b_check_handoff.py - sanity check of the handoff encoding."""
import re
import sys
from pathlib import Path

p = Path(sys.argv[1] if len(sys.argv) > 1 else ".agents/teamwork/worker/handoff.md")
t = p.read_bytes().decode("utf-8")
print("bytes          :", len(p.read_bytes()))
print("chars          :", len(t))
print("BOM            :", p.read_bytes()[:3] == b"\xef\xbb\xbf")
bad = [(m.start(), m.group(0)) for m in re.finditer("[\u00c3\u00e2\u20ac]", t)]
print("suspect chars  :", len(bad))
for pos, ch in bad[:5]:
    print("   ...", repr(t[max(0, pos - 50):pos + 30]))
print("HITO sections  :")
for m in re.finditer(r"^# HITO .*$", t, re.M):
    print("   ", m.group(0)[:70])
for probe in ("sesión", "análisis", "—", "·", "⇒", "AC7"):
    print(f"contains {probe!r}:", probe in t)
