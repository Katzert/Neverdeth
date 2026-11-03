#!/usr/bin/env python3
"""M6 worker (M6-C): bisect the watchdog logs to locate the healthy state.

For every log in build_clang/logs/neverdead_NNN.log extract, from the log
content ONLY (no fabricated data):
  * iteration / mtime
  * log lines
  * "Entering Asura main loop" (main loop)
  * "Frame #N" (frames)
  * "BD98 epilogue return" (the apply_bd98_fix.py stub, F-18)
  * "BBE8 terminate intercepted" (the neutralised CRT terminate)
  * the r31 drift of the BD34 entries (AC1)

Then print the healthy/broken partition with the boundary.
"""
import pathlib, re, sys, datetime

LOGS = pathlib.Path(r"C:\Users\katze\.gemini\antigravity-ide\scratch\vibe-recomp\neverdead_project\build_clang\logs")

RX_MAIN = re.compile(r"Entering Asura main loop")
RX_FRAME = re.compile(r"Frame #(\d+)")
RX_STUB = re.compile(r"BD98 epilogue return")
RX_BBE8 = re.compile(r"BBE8 terminate intercepted")
RX_DRIFT = re.compile(r"BD34-tail (\d+) lr=(0x[0-9A-Fa-f]+)")
RX_TRAP = re.compile(r"TRAP-OOB caller")

rows = []
for f in sorted(LOGS.glob("neverdead_*.log"), key=lambda p: int(p.stem.split("_")[1])):
    n = int(f.stem.split("_")[1])
    txt = f.read_text(encoding="utf-8", errors="replace")
    lines = txt.splitlines()
    drift = [int(m.group(1)) for m in RX_DRIFT.finditer(txt)]
    rows.append({
        "n": n,
        "mtime": datetime.datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        "lines": len(lines),
        "main": len(RX_MAIN.findall(txt)),
        "frames": len(RX_FRAME.findall(txt)),
        "stub": len(RX_STUB.findall(txt)),
        "bbe8": len(RX_BBE8.findall(txt)),
        "trap": len(RX_TRAP.findall(txt)),
        "drift": (max(drift) - min(drift)) if len(drift) > 1 else None,
        "bd34": len(drift),
    })

print(f"{'log':>5} {'mtime':>19} {'lines':>7} {'main':>5} {'frames':>6} {'stub':>5} {'bbe8':>5} {'trap':>6} {'drift':>7}")
for r in rows:
    print(f"{r['n']:>5} {r['mtime']:>19} {r['lines']:>7} {r['main']:>5} {r['frames']:>6} {r['stub']:>5} {r['bbe8']:>5} {r['trap']:>6} {str(r['drift']):>7}")

healthy = [r for r in rows if r["main"] > 0]
broken = [r for r in rows if r["main"] == 0]
print()
print(f"logs con main_loop>=1 : {len(healthy)}  -> {[r['n'] for r in healthy]}")
print(f"logs con main_loop==0 : {len(broken)}  -> {[r['n'] for r in broken]}")
if healthy:
    last = max(healthy, key=lambda r: r["n"])
    print(f"ULTIMO SANO: log {last['n']}  mtime {last['mtime']}  lines {last['lines']}  frames {last['frames']}  stub {last['stub']}  bbe8 {last['bbe8']}")
    after = [r for r in broken if r["n"] > last["n"]]
    if after:
        first = min(after, key=lambda r: r["n"])
        print(f"PRIMER ROTO: log {first['n']}  mtime {first['mtime']}  lines {first['lines']}  stub {first['stub']}  bbe8 {first['bbe8']}")
        print(f"VENTANA DE REGRESION: [{last['mtime']} .. {first['mtime']}]")
