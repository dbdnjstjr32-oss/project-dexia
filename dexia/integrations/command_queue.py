"""File-backed C2 command queue (HUD -> Python backend).

The Next.js ``/api/command`` route appends user actions (spawn/remove) to
``commands.json`` at the project root. The Python simulation loop polls and
consumes them each tick via :func:`drain_commands`, which removes only the
commands it processed — concurrent appends from the API are preserved.

Each command is a dict, e.g.::

    {"id": "...", "action": "spawn",  "x": 6.0, "y": 6.0, "z": 1.5,
     "lon": 126.97, "lat": 37.56, "profile": {...}}
    {"id": "...", "action": "remove", "agent_id": "agent_kami_5"}

Design goals: never block the sim loop, never raise on a malformed/locked file.

Concurrency hardening (Windows-safe)
------------------------------------
The writer (FastAPI ``append_command``) and the reader (streamer
``drain_commands``) touch the same file from different processes. On Windows
``os.replace`` raises ``PermissionError`` ("[WinError 5] access denied") if the
destination is *momentarily* open by the other side — there is no atomic
rename-over-open-file like POSIX. Under a 4-drone deploy burst this surfaced as
intermittent HTTP 500s on ``POST /api/sim/deploy``.

Two layers of defense, both kept because they cover different failure modes:

1. A lightweight cross-process **advisory file lock** (``msvcrt.locking`` on
   Windows, ``fcntl.flock`` elsewhere) on a sidecar ``commands.json.lock`` file,
   held across the whole read-modify-write of *both* producers and consumers.
   This serializes the Python writer and the Python reader so they never have
   the data file open at the same instant.
2. A bounded **retry-with-backoff** around ``os.replace`` itself. This catches
   the residual races the lock cannot cover — e.g. the Node ``/api/command``
   route (which does not share our lock), an antivirus scanner, or the Windows
   search indexer holding a transient handle. Retries span ~150 ms total, then
   re-raise so a genuinely stuck file still surfaces.

The Redis-backed queue is the longer-term fix; this keeps the zero-setup
JSON-file fallback robust in the meantime.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
import tempfile
import threading
import time
from typing import Any, Iterator

_log = logging.getLogger(__name__)

DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "commands.json",
)

# --------------------------------------------------------------------------- #
# Tunables
# --------------------------------------------------------------------------- #
_LOCK_SUFFIX = ".lock"
_LOCK_ACQUIRE_TIMEOUT = 2.0       # s: total wait for the cross-process lock
_REPLACE_RETRIES = 8             # attempts for os.replace before giving up
_REPLACE_BACKOFF = 0.01          # s: initial backoff, grows ~1.6x (caps ~30ms)

# In-process serialization (FastAPI/uvicorn may call from multiple threads):
# cheaper than busy-spinning on the OS lock when contention is same-process.
_PROC_LOCKS: dict[str, threading.Lock] = {}
_PROC_LOCKS_GUARD = threading.Lock()

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


def _proc_lock(path: str) -> threading.Lock:
    key = os.path.abspath(path)
    with _PROC_LOCKS_GUARD:
        lk = _PROC_LOCKS.get(key)
        if lk is None:
            lk = threading.Lock()
            _PROC_LOCKS[key] = lk
        return lk


# --------------------------------------------------------------------------- #
# Cross-process advisory lock on a sidecar <path>.lock file
# --------------------------------------------------------------------------- #
def _os_lock(fh) -> None:
    if sys.platform == "win32":
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _os_unlock(fh) -> None:
    try:
        if sys.platform == "win32":
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


@contextlib.contextmanager
def _file_lock(path: str) -> Iterator[None]:
    """Best-effort cross-process + in-process exclusive lock for ``path``.

    Acquires an in-process threading lock first, then an advisory OS lock on a
    sidecar ``<path>.lock`` file, retrying until ``_LOCK_ACQUIRE_TIMEOUT``. If
    the OS lock cannot be taken in time we proceed anyway (best-effort, never
    block the sim loop) — the ``os.replace`` retry below is the safety net.
    """
    proc_lock = _proc_lock(path)
    proc_lock.acquire()
    fh = None
    locked = False
    try:
        lock_path = path + _LOCK_SUFFIX
        try:
            fh = open(lock_path, "a+")
        except OSError:
            fh = None  # cannot open lock file; degrade to in-process lock only

        if fh is not None:
            deadline = time.monotonic() + _LOCK_ACQUIRE_TIMEOUT
            delay = 0.005
            while True:
                try:
                    _os_lock(fh)
                    locked = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        # Make the silent degradation observable: we proceed
                        # best-effort (the os.replace retry + drain re-read are
                        # the safety net), but a concurrent *cross-process*
                        # writer could now race the read-modify-write.
                        _log.warning(
                            "command_queue: cross-process lock on %s not acquired "
                            "within %.1fs — proceeding best-effort (os.replace "
                            "retry is the fallback).", lock_path, _LOCK_ACQUIRE_TIMEOUT)
                        break
                    time.sleep(delay)
                    delay = min(delay * 2, 0.05)
        yield
    finally:
        if fh is not None:
            if locked:
                _os_unlock(fh)
            try:
                fh.close()
            except OSError:
                pass
        proc_lock.release()


# --------------------------------------------------------------------------- #
# IO primitives
# --------------------------------------------------------------------------- #
def _read(path: str) -> list[dict]:
    """Read the command file.  Returns [] on any read/parse error.

    [결함 17] JSONDecodeError 를 bare ``except Exception`` 으로 무시하던 구현을
    개선.  파일이 손상됐을 때 경고 로그를 남기고, 정상적인 파일 미존재 케이스와
    구분하여 처리한다.  손상 파일은 *덮어쓰지 않고* 빈 리스트를 반환하므로
    drain_commands 는 조기 반환하여 데이터를 보존한다.
    """
    import logging as _logging
    _log = _logging.getLogger(__name__)
    try:
        if not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError as exc:
        _log.warning("command_queue: malformed JSON in %s (%s) — skipping drain", path, exc)
        return []
    except OSError:
        return []


def _replace_with_retry(src: str, dst: str) -> None:
    """``os.replace(src, dst)`` with bounded backoff on Windows access-denied.

    Windows raises ``PermissionError`` if ``dst`` is momentarily open by another
    process; retry a handful of times over ~150 ms, then re-raise so a truly
    stuck file still surfaces to the caller.
    """
    delay = _REPLACE_BACKOFF
    for attempt in range(_REPLACE_RETRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == _REPLACE_RETRIES - 1:
                raise
            time.sleep(delay)
            delay = min(delay * 1.6, 0.03)


def _atomic_write(path: str, items: list[Any]) -> None:
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(items, f)
        _replace_with_retry(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def append_command(cmd: dict, path: str = DEFAULT_PATH) -> None:
    """Append one command. Read-modify-write is serialized by the shared lock."""
    with _file_lock(path):
        items = _read(path)
        items.append(cmd)
        _atomic_write(path, items)


def drain_commands(path: str = DEFAULT_PATH) -> list[dict]:
    """Return all pending commands and remove exactly those from the file.

    The whole read-modify-write runs under the shared lock so it cannot collide
    with a concurrent :func:`append_command`. The re-read is retained as belt-
    and-suspenders: if the OS lock could not be acquired (degraded best-effort
    mode), any command appended after our snapshot is still preserved.
    """
    with _file_lock(path):
        pending = _read(path)
        if not pending:
            return []
        processed_ids = {c.get("id") for c in pending if isinstance(c, dict)}
        # Re-read; keep anything that arrived after our snapshot.
        current = _read(path)
        remaining = [c for c in current if c.get("id") not in processed_ids]
        _atomic_write(path, remaining)
    return [c for c in pending if isinstance(c, dict)]
