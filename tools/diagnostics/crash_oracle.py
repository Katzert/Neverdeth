#!/usr/bin/env python3
"""NeverDead Crash Oracle & Forensic Capsule Generator.

Parses the most recent engine log and prints a tight 10-line forensic capsule
(<300 tokens) to prevent context pollution and accelerate triage.
"""

from __future__ import annotations

import glob
import os
import re
import sys
from pathlib import Path

RE_FATAL = re.compile(r"\[FATAL\]\s*(.*)")
RE_ERROR = re.compile(r"\[error\]\s*\[sys\]\s*\[t([0-9]+)\]\s*(.*)")
RE_LR = re.compile(r"lr=(0x[0-9A-Fa-f]+)")


def get_latest_log(logs_dir: Path) -> Path | None:
    logs = sorted(logs_dir.glob("neverdead_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    return logs[0] if logs else None


def analyze_log(log_path: Path) -> dict:
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    
    fatal_msg = None
    error_msg = None
    last_lr = None
    notable_events = []
    
    for line in lines[-100:]:
        m_fatal = RE_FATAL.search(line)
        if m_fatal:
            fatal_msg = m_fatal.group(1).strip()
        m_err = RE_ERROR.search(line)
        if m_err:
            error_msg = m_err.group(2).strip()
        m_lr = RE_LR.findall(line)
        if m_lr:
            last_lr = m_lr[-1]
        if any(k in line for k in ("intercepted", "clean exit", "PTD", "WINMAIN", "FATAL", "access violation", "unregistered")):
            # Extract just message portion
            idx = line.find("[core]")
            if idx == -1:
                idx = line.find("[sys]")
            notable_events.append(line[idx:] if idx != -1 else line)

    return {
        "log": log_path.name,
        "total_lines": len(lines),
        "fatal": fatal_msg,
        "error": error_msg,
        "last_lr": last_lr,
        "recent_tail": notable_events[-6:]
    }


def main():
    root = Path(__file__).resolve().parent
    logs_dir = root / "build_clang" / "logs"
    latest = get_latest_log(logs_dir)
    if not latest:
        print("[CRASH ORACLE] No logs found in build_clang/logs")
        return 1

    info = analyze_log(latest)
    print("=== FORENSIC CAPSULE ===")
    print(f"Log: {info['log']} ({info['total_lines']} lines)")
    if info['fatal']:
        print(f"FATAL: {info['fatal']}")
    if info['error']:
        print(f"ERROR: {info['error']}")
    if info['last_lr']:
        print(f"LAST_LR: {info['last_lr']}")
    print("KEY EVENTS:")
    for ev in info['recent_tail']:
        print(f"  * {ev.strip()}")
    print("========================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
