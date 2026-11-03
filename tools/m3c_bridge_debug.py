#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m3c_bridge_debug - depura la localizacion del ancla del puente."""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import apply_entrypoint_bridge as eb  # noqa: E402


def main(argv: list[str]) -> int:
    path = Path(argv[1])
    owner = argv[2] if len(argv) > 2 else "xstart"
    text = eb._read(path)
    start, end = eb._func_span(text, owner)
    span = text[start:end]
    print(f"file={path} owner={owner} span_len={len(span)}")
    lab = eb.LABEL_RE.search(span)
    print("LABEL_RE match:", lab.start() if lab else None)
    if lab:
        print("  label text:", repr(span[lab.start():lab.start() + 60]))
        region = span[lab.start():]
        print("  STUB_CALL in region :", eb.STUB_CALL in region)
        print("  BRIDGE_CALL in region:", eb.BRIDGE_CALL in region)
        print("  SEED_MARKER in region:", eb.SEED_MARKER in region)
    print("STUB_CALL_LINE_RE:", (eb.STUB_CALL_LINE_RE.search(span) or [None]) and
          (eb.STUB_CALL_LINE_RE.search(span).start() if eb.STUB_CALL_LINE_RE.search(span) else None))
    m = eb.BRIDGE_CALL_ANY_RE.search(span)
    print("BRIDGE_CALL_ANY_RE:", m.start() if m else None)
    print("SEED_MARKER in span:", eb.SEED_MARKER in span)
    # what does the span tail look like?
    print("--- tail of span ---")
    print(span[-1500:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
