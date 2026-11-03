#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
m3c_runtime_loop.py - M3c O2: bucle de runtime que cierra las directiones de
invitado que el motor v2 no puede resolver.

Ciclo de cada ronda
-------------------
  1. watchdog 20 s sobre el exe v2  ->  log build_clang_v2/logs/neverdead_NNN.log
  2. tools/m3c_runtime_scan.py sobre el log  ->  VAs implicadas en el fallo
     ("No function registered at X", "[FATAL] Unresolved branch from X to Y",
      "TRAP-OOB caller lr=X", ...)
  3. las VAs NUEVAS se anaden al acumulador (tools/m3b_node_addresses.txt) con su
     procedencia
  4. se reconstruye la semilla v2 desde ground truth (.pdata + Capstone) con el
     acumulador (mismas reglas A/B/C y disjuntividad que M1/M3b)
  5. `rexglue codegen neverdead_manifest_v2.toml --ignore-stamp`  (ANALISIS REAL,
     comprobado con el marcador REANALYZED de tools/m3b_codegen_run.py)
  6. build de v2 (la cadena de apply_*.py + el fix de mapeo se reaplican)
  7. watchdog 20 s  ->  log nuevo
  8. medicion con tools/progress_meter.py --json

La ronda se detiene cuando:
  (a) main_loop_entered = true            (AC7)
  (b) aparece un hito Vd*                  (AC8)
  (c) dos rondas seguidas fallan con el MISMO error y sin VA nueva  (Plan B)

Uso:
    python tools/m3c_runtime_loop.py --rounds 12 [--start-round 3] [--json-out PATH]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from m3b_codegen_run import run_codegen  # noqa: E402

SDK = Path(r"C:\Users\katze\.gemini\antigravity-ide\scratch\vibe-recomp"
           r"\rexglue_sdk\win-amd64\bin\rexglue.exe")
SEED = ROOT / "nd_seed_overrides_v2.toml"
ACC = HERE / "m3b_node_addresses.txt"
MANIFEST = ROOT / "neverdead_manifest_v2.toml"
LOGDIR = ROOT / "build_clang_v2" / "logs"
BUILDER = HERE / "build_seed_from_pdata.py"
SCANNER = HERE / "m3c_runtime_scan.py"
METER = HERE / "progress_meter.py"
WATCHDOG = ROOT / "run_game_watchdog_v2.py"
BUILD = ROOT / ".agents" / "teamwork" / "worker" / "build_neverdead_v2.ps1"


def sh(cmd, cwd=ROOT, timeout=3600, env=None):
    e = dict(__import__("os").environ)
    if env:
        e.update(env)
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                          errors="replace", timeout=timeout, env=e)


def vd_hits(met: dict) -> list:
    """Hitos Vd*/shaders con status HIT en la salida de progress_meter."""
    hitos = met.get("hitos") or {}
    if isinstance(hitos, dict):
        return [k for k, v in hitos.items() if (v or {}).get("status") == "HIT"]
    return [h.get("name") for h in hitos if h.get("status") == "HIT"]


def newest_log() -> Path:
    logs = sorted(LOGDIR.glob("neverdead_*.log"), key=lambda p: p.stat().st_mtime)
    return logs[-1]


def run_watchdog() -> tuple[int, Path]:
    before = newest_log()
    r = sh([sys.executable, str(WATCHDOG), "20"], timeout=120)
    after = newest_log()
    return r.returncode, after


def scan_log(log: Path) -> dict:
    out = ROOT / ".agents" / "teamwork" / "worker" / "m3c_runtime_scan_tmp.json"
    r = sh([sys.executable, str(SCANNER), str(log), "--json", str(out)], timeout=120)
    if not out.is_file():
        return {"findings": [], "new_targets": []}
    return json.loads(out.read_text(encoding="utf-8"))


def known_addresses() -> set[int]:
    """Direcciones que la semilla ACTUAL ya declara (no el acumulador)."""
    if not SEED.is_file():
        return set()
    txt = SEED.read_text(encoding="utf-8", errors="replace")
    return {int(m, 16) for m in
            re.findall(r'\[functions\."0x([0-9A-Fa-f]+)"\]', txt)}


def finding_addresses(sc: dict) -> list[str]:
    out = set()
    for f in sc.get("findings", []):
        out.add(f["va"])
    return sorted(out)


def measure(log: Path) -> dict:
    r = sh([sys.executable, str(METER), "--log", str(log), "--json"], timeout=300)
    txt = r.stdout or ""
    try:
        i = txt.index("{")
        return json.loads(txt[i:])
    except Exception:
        return {}


def build_seed() -> dict:
    r = sh([sys.executable, str(BUILDER), "--out", str(SEED), "--emit",
            "--extra-nodes", str(ACC), "--json"], timeout=1800)
    try:
        return json.loads(r.stdout[r.stdout.index("{"):])
    except Exception:
        return {"error": (r.stdout + r.stderr)[-800:]}


def codegen(label: str) -> dict:
    return run_codegen(work_dir=ROOT, manifest=MANIFEST, seed=SEED,
                       out_dir=ROOT / "generated" / "neverdead_default_v2",
                       sdk=SDK, label=label, use_ignore_stamp=True,
                       log_path=ROOT / ".agents" / "teamwork" / "worker"
                       / f"m3c_codegen_{label}.log")


def build_v2(label: str) -> dict:
    log = ROOT / ".agents" / "teamwork" / "worker" / f"build_v2_{label}.log"
    r = sh(["powershell", "-ExecutionPolicy", "Bypass", "-File", str(BUILD)],
           timeout=3600, env={"NEVERDEAD_GENERATED_DIR": "generated/neverdead_default_v2"})
    log.write_text(r.stdout + "\n=== STDERR ===\n" + r.stderr, encoding="utf-8",
                   errors="replace")
    return {"exit": r.returncode, "log": str(log)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=12)
    ap.add_argument("--start-round", type=int, default=1)
    ap.add_argument("--json-out", type=Path, required=True)
    ap.add_argument("--force-rebuild", action="store_true",
                    help="reconstruye semilla + codegen + build aunque no haya VA nueva "
                         "(la semilla pudo cambiar fuera del bucle)")
    args = ap.parse_args(argv)

    history: list[dict] = []
    if args.json_out.is_file():
        try:
            history = json.loads(args.json_out.read_text(encoding="utf-8"))
        except Exception:
            history = []

    for rnd in range(args.start_round, args.start_round + args.rounds):
        t0 = time.time()
        print(f"===== RONDA {rnd} =====", flush=True)
        # --- 1. run ------------------------------------------------------------
        rc, log = run_watchdog()
        sc = scan_log(log)
        met = measure(log)
        seed_known = known_addresses()
        all_targets = finding_addresses(sc)
        new_targets = [t for t in all_targets if int(t, 16) not in seed_known]
        print(f"  watchdog rc={rc} log={log.name} lines={sc.get('lines')} "
              f"targets={all_targets} new(not in seed)={new_targets}", flush=True)
        row = {
            "round": rnd,
            "log": log.name,
            "log_lines": sc.get("lines"),
            "watchdog_exit": rc,
            "findings": sc.get("findings", []),
            "new_targets": new_targets,
            "main_loop_entered": met.get("main_loop_entered"),
            "frame_count": met.get("frame_count"),
            "translated_shaders": met.get("translated_shaders"),
            "unhandled_guest_violations": met.get("unhandled_guest_violations"),
            "fatal_rex_count": met.get("fatal_rex_count"),
            "release_failed_count": met.get("release_failed_count"),
            "heap_guard_hits": met.get("heap_guard_hits"),
            "bd34_calls": met.get("bd34_calls"),
            "vd_hits": vd_hits(met),
            "elapsed_s": None,
        }
        # --- 2. seed ----------------------------------------------------------
        if new_targets or args.force_rebuild:
            if new_targets:
                with ACC.open("a", encoding="utf-8") as fh:
                    for t in new_targets:
                        # formato del acumulador: UNA DIRECCION POR LINEA, sin
                        # comentarios (build_seed_from_pdata.py lo lee con int(l,16))
                        fh.write(f"{t}\n")
                print(f"  -> {len(new_targets)} direcciones anadidas al acumulador", flush=True)
            seed = build_seed()
            row["seed_entries"] = seed.get("disjoint_info", {}).get("final_entries")
            row["seed_overlaps"] = seed.get("overlapping_boundaries")
            cg = codegen(f"r{rnd}")
            row["codegen_exit"] = cg["exit"]
            row["codegen_reanalyzed"] = cg["REANALYZED"]
            row["codegen_elapsed_s"] = cg["elapsed_s"]
            row["unresolved_b_lines"] = cg["unresolved_b_lines"]
            row["unresolved_bl_lines"] = cg["unresolved_bl_lines"]
            print(f"  seed={row['seed_entries']} codegen exit={cg['exit']} "
                  f"reanalyzed={cg['REANALYZED']}", flush=True)
            if cg["exit"] != 0:
                row["stop"] = "codegen failed"
                history.append(row)
                args.json_out.write_text(json.dumps(history, indent=2), encoding="utf-8")
                return 2
            bl = build_v2(f"m3c_{rnd}")
            row["build_exit"] = bl["exit"]
            print(f"  build exit={bl['exit']}", flush=True)
            if bl["exit"] != 0:
                row["stop"] = "build failed"
                history.append(row)
                args.json_out.write_text(json.dumps(history, indent=2), encoding="utf-8")
                return 2
            rc, log2 = run_watchdog()
            sc2 = scan_log(log2)
            met2 = measure(log2)
            seed_known2 = known_addresses()
            all_targets2 = finding_addresses(sc2)
            row["log_after"] = log2.name
            row["log_lines_after"] = sc2.get("lines")
            row["findings_after"] = sc2.get("findings", [])
            row["targets_after"] = all_targets2
            row["new_targets_after"] = [t for t in all_targets2
                                        if int(t, 16) not in seed_known2]
            row["main_loop_entered_after"] = met2.get("main_loop_entered")
            row["frame_count_after"] = met2.get("frame_count")
            row["translated_shaders_after"] = met2.get("translated_shaders")
            row["fatal_rex_count_after"] = met2.get("fatal_rex_count")
            row["unhandled_guest_violations_after"] = met2.get("unhandled_guest_violations")
            row["vd_hits_after"] = vd_hits(met2)
            print(f"  -> tras la ronda: log={log2.name} lines={sc2.get('lines')} "
                  f"main_loop={row['main_loop_entered_after']} "
                  f"frames={row['frame_count_after']} "
                  f"vd={row['vd_hits_after']}", flush=True)
        else:
            row["stop"] = "no new target"
            row["elapsed_s"] = round(time.time() - t0, 1)
            history.append(row)
            args.json_out.write_text(json.dumps(history, indent=2), encoding="utf-8")
            # (c) dos rondas seguidas con el mismo error y sin VA nueva
            if len(history) >= 2 and not history[-1].get("new_targets") \
                    and not history[-2].get("new_targets"):
                print("STOP: dos rondas seguidas sin VA nueva (Plan B)", flush=True)
                return 3
            continue

        row["elapsed_s"] = round(time.time() - t0, 1)
        history.append(row)
        args.json_out.write_text(json.dumps(history, indent=2), encoding="utf-8")

        if row.get("main_loop_entered_after") or row.get("frame_count_after"):
            print("STOP: main loop alcanzado (AC7)", flush=True)
            return 0
        if row.get("vd_hits_after"):
            print("STOP: hito Vd* alcanzado (AC8)", flush=True)
            return 0
        if not row.get("new_targets_after"):
            print("STOP: sin VA nueva en la ronda posterior", flush=True)
            return 3
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
