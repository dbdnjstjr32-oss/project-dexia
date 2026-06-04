"""Regression: command_queue must survive a writer/reader race on Windows.

Reproduces the production bug where ``POST /api/sim/deploy`` (FastAPI
``append_command``) and the telemetry streamer (``drain_commands`` at ~10 Hz)
race on ``commands.json``, and ``os.replace`` fails with
``PermissionError: [WinError 5]`` (access denied) because the destination is
momentarily open by the other *process*.

This runs the writer and reader in SEPARATE OS processes (the only faithful
repro — a single-process threading test is serialized by the GIL/in-proc lock
and hides the cross-process collision). With the fix (shared cross-process file
lock + bounded os.replace retry) there must be zero errors and zero lost
commands.

Run:
    .venv312\\Scripts\\python.exe test_command_queue_concurrency.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import traceback
import uuid

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

CMD_PATH = os.path.join(HERE, "commands.concurrency_test.json")
PY = sys.executable
DURATION = 6.0          # seconds of sustained contention
N_WRITERS = 4           # mirrors the 4-drone E2E deploy burst


# --------------------------------------------------------------------------- #
# Child roles (each runs in its own process)
# --------------------------------------------------------------------------- #
def _run_writer(idx: int) -> int:
    from dexia.integrations.command_queue import append_command
    deadline = time.monotonic() + DURATION
    n = err = 0
    while time.monotonic() < deadline:
        cmd = {"id": "cmd_" + uuid.uuid4().hex[:14], "action": "spawn",
               "x": float(idx), "y": float(idx), "z": 0.3}
        try:
            append_command(cmd, path=CMD_PATH)
            n += 1
        except Exception:
            err += 1
            sys.stderr.write(f"WRITER{idx} ERROR:\n{traceback.format_exc()}\n")
        time.sleep(0.001)
    sys.stderr.write(f"WRITER{idx} DONE appended={n} errors={err}\n")
    return 1 if err else 0


def _run_reader() -> int:
    from dexia.integrations.command_queue import drain_commands
    deadline = time.monotonic() + DURATION + 0.5   # outlast the writers
    drained = err = 0
    while time.monotonic() < deadline:
        try:
            drained += len(drain_commands(path=CMD_PATH))
        except Exception:
            err += 1
            sys.stderr.write(f"READER ERROR:\n{traceback.format_exc()}\n")
        time.sleep(0.10)                            # ~10 Hz, matches --hz 10
    sys.stderr.write(f"READER DONE drained={drained} errors={err}\n")
    return 1 if err else 0


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
def main() -> int:
    print("=" * 74)
    print("COMMAND-QUEUE CONCURRENCY TEST — writer/reader race (cross-process)")
    print("=" * 74)

    for p in (CMD_PATH, CMD_PATH + ".lock"):
        if os.path.exists(p):
            os.remove(p)

    procs = [("writer", subprocess.Popen(
                  [PY, __file__, "writer", str(i)], stderr=subprocess.PIPE, text=True))
             for i in range(N_WRITERS)]
    procs.append(("reader", subprocess.Popen(
        [PY, __file__, "reader"], stderr=subprocess.PIPE, text=True)))

    rc_total = appended = drained = win5 = 0
    for _role, p in procs:
        _, err = p.communicate()
        rc_total += p.returncode
        if err:
            win5 += err.count("WinError 5") + err.count("PermissionError")
            for line in err.splitlines():
                if "appended=" in line:
                    appended += int(line.split("appended=")[1].split()[0])
                if "drained=" in line:
                    drained += int(line.split("drained=")[1].split()[0])
            for line in err.splitlines():
                if "ERROR" in line or "PermissionError" in line:
                    print("  " + line)

    print(f"\nwriters={N_WRITERS}  duration={DURATION}s  reader~10Hz")
    print(f"  appended (POST /deploy successes) : {appended}")
    print(f"  drained  (streamer consumed)      : {drained}")
    print(f"  access-denied / WinError5         : {win5}")

    no_errors = (rc_total == 0 and win5 == 0)
    no_loss = (appended > 0 and drained == appended)
    ok = no_errors and no_loss
    print(f"\n  no WinError 5 / exceptions        : {'PASS' if no_errors else 'FAIL'}")
    print(f"  no lost commands ({drained}=={appended}){'':<6}: "
          f"{'PASS' if no_loss else 'FAIL'}")

    for p in (CMD_PATH, CMD_PATH + ".lock"):
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass

    print("\n" + "=" * 74)
    print(f"COMMAND-QUEUE CONCURRENCY TEST: {'PASS' if ok else 'FAIL'}")
    print("=" * 74)
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "writer":
        raise SystemExit(_run_writer(int(sys.argv[2])))
    if len(sys.argv) >= 2 and sys.argv[1] == "reader":
        raise SystemExit(_run_reader())
    raise SystemExit(main())
