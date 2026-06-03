"""Outbound internal webhook client (Dexia = Sender).

Fire-and-forget POST of simulation events to the Tactical Globe receiver. Fully
non-blocking: work is handed to a small daemon thread pool so the sim/render
loop returns immediately, and every failure mode (offline, timeout, DNS, 4xx/5xx)
is swallowed and logged — Dexia must never crash because the receiver is down.

Config via env (with safe defaults):
    TACTICAL_GLOBE_WEBHOOK_URL   (default http://127.0.0.1:3000/api/internal/webhook)
    INTERNAL_WEBHOOK_SECRET      (shared secret; if unset, sends are skipped)
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any

log = logging.getLogger("dexia.webhook")

_DEFAULT_URL = "http://127.0.0.1:3000/api/internal/webhook"
_TIMEOUT_S = 3.0

# Small daemon pool: callers never block, and a backlog can never wedge the sim.
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="dexia-webhook")


def _post(url: str, secret: str, body: dict[str, Any]) -> None:
    """Blocking POST run on a worker thread. Never raises."""
    try:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "x-internal-secret": secret,
            },
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            if resp.status >= 400:
                log.warning("webhook receiver returned HTTP %s", resp.status)
    except urllib.error.HTTPError as e:
        log.warning("webhook HTTP error %s: %s", e.code, e.reason)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        # Receiver offline / unreachable — expected, do not propagate.
        log.debug("webhook delivery skipped (receiver unreachable): %s", e)
    except Exception as e:  # absolute safety net
        log.debug("webhook unexpected error: %s", e)


def send_event(
    event_type: str,
    payload: dict[str, Any] | None = None,
    *,
    source: str = "dexia",
    url: str | None = None,
    secret: str | None = None,
    blocking: bool = False,
) -> None:
    """Queue an event for delivery to the Tactical Globe webhook.

    Returns immediately (non-blocking) by default. Safe to call from a hot loop.
    """
    secret = secret if secret is not None else os.environ.get("INTERNAL_WEBHOOK_SECRET", "")
    if not secret:
        log.debug("INTERNAL_WEBHOOK_SECRET unset; skipping webhook '%s'", event_type)
        return

    url = url or os.environ.get("TACTICAL_GLOBE_WEBHOOK_URL", _DEFAULT_URL)
    body = {"source": source, "event_type": event_type, **(payload or {})}

    if blocking:
        _post(url, secret, body)
    else:
        try:
            _executor.submit(_post, url, secret, body)
        except RuntimeError:
            # Executor shut down (e.g. during interpreter exit) — drop silently.
            pass


def shutdown(wait: bool = False) -> None:
    """Optional graceful drain on exit."""
    _executor.shutdown(wait=wait)
