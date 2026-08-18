"""k-LLM Gateway — Phase 7. Unified multi-model proxy + router + audit.

All LLM calls go through one interface. A use-case router picks the model
(high-precision tactical vs. fast summary vs. air-gapped local-only), every call
is logged to ``llm_audit.jsonl`` (model, token counts, latency), and providers
are pluggable so a cloud model (Claude/GPT) can be added by setting an API key —
without changing callers. Default + air-gap path is local Ollama (no internet).
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

# ---- Circuit-breaker / timeout constants ---------------------------------- #
_LLM_TIMEOUT: float = 25.0       # seconds; passed to ollama.chat(timeout=)
_CB_THRESHOLD: int = 3            # consecutive failures before opening
_CB_COOL_OFF: float = 60.0        # seconds the circuit stays open
# NB: the breaker COUNTERS live on each LLMGateway instance (see __init__), not
# as module globals — so one flaky gateway/session can't blind every other
# caller, and a tripped breaker doesn't bleed across independent runs/tests.

try:
    import ollama
except Exception:  # pragma: no cover
    ollama = None

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_AUDIT_PATH = os.path.join(_ROOT, "llm_audit.jsonl")

# use_case -> (provider, model). Cloud rows only fire if an API key is present;
# otherwise the gateway transparently falls back to the local Ollama model.
#
# Model tiering is hardware-driven (RTX 5070 Laptop, 8 GB VRAM / 32 GB RAM):
#   * LIVE paths (tactical, summary, airgap) are CONSOLIDATED onto ONE 7B model
#     so it stays VRAM-resident with zero swap. 8 GB holds exactly one q4 7-8B
#     model hot; routing two distinct 8B models would thrash (evict+reload, ~3-8s
#     per switch) every time a mission tick alternates tactical<->summary.
#     qwen2.5:7b wins the single slot: strong Korean (commander cards) + native
#     function-calling (COA tool use).
#   * doctrine (offline After-Action Review self-correction) is batch/async and
#     latency-insensitive, so it gets the heavyweight gpt-oss:20b (MoE ~13-14 GB,
#     ~3.6B active/token) — partially offloaded to the 32 GB RAM. Its stronger
#     reasoning is most valuable here, and the cost is hidden off the live path.
ROUTING = {
    "tactical": ("ollama", "qwen2.5:7b"),   # live COA tool-calling (VRAM-resident)
    "summary": ("ollama", "qwen2.5:7b"),    # live Korean report (same model -> no swap)
    "airgap": ("ollama", "qwen2.5:7b"),     # local-only (same resident model)
    "doctrine": ("ollama", "gpt-oss:20b"),  # async AAR self-correction (RAM-offloaded)
}
_FALLBACK = ("ollama", "qwen2.5:7b")


def _listed_model_names(listed: Any) -> list[str]:
    """Extract model name strings from an ``ollama.list()`` response, tolerating
    both the dict shape ({'models': [{'model': 'qwen2.5:7b', ...}]}) and the
    typed-object shape (objects exposing ``.model`` / ``.name``)."""
    models = listed.get("models", []) if isinstance(listed, dict) else getattr(listed, "models", None) or []
    out: list[str] = []
    for m in models:
        if isinstance(m, dict):
            name = m.get("model") or m.get("name")
        else:
            name = getattr(m, "model", None) or getattr(m, "name", None)
        if name:
            out.append(str(name))
    return out


class LLMGateway:
    def __init__(self, audit_path: str = DEFAULT_AUDIT_PATH, host: Optional[str] = None) -> None:
        self.audit_path = audit_path
        self._ollama = (ollama.Client(host=host) if (ollama and host) else
                        (ollama.Client() if ollama else None))
        # Per-instance circuit breaker (not shared module state).
        self._cb_failures = 0
        self._cb_open_until = 0.0

    def reset_circuit(self) -> None:
        """Force the breaker closed — e.g. at the start of a proof run so a
        failure from an earlier, unrelated call can't pre-open it."""
        self._cb_failures = 0
        self._cb_open_until = 0.0

    def available(self) -> bool:
        """True only if the local Ollama server is actually reachable AND the live
        tactical model is pulled.

        Importing the ``ollama`` package (``self._ollama is not None``) is NOT
        enough: the old check returned True with no server running and no model
        loaded, so an "honest live proof" gate could green-light against a dead
        backend. We now ping the server (``list``) and require the routed tactical
        model to be present (matched by base name to tolerate tag variants)."""
        if self._ollama is None:
            return False
        try:
            listed = self._ollama.list()
        except Exception:
            return False
        want = ROUTING.get("tactical", _FALLBACK)[1]
        want_base = want.split(":", 1)[0]
        for name in _listed_model_names(listed):
            if name == want or name.split(":", 1)[0] == want_base:
                return True
        return False

    # ------------------------------------------------------------------ #
    def _route(self, use_case: str, model: Optional[str]) -> tuple[str, str]:
        if model:
            return ("ollama", model)
        provider, mdl = ROUTING.get(use_case, _FALLBACK)
        # cloud provider requested but no key/SDK -> fall back to local
        if provider == "anthropic" and not os.environ.get("ANTHROPIC_API_KEY"):
            provider, mdl = _FALLBACK
        if provider == "openai" and not os.environ.get("OPENAI_API_KEY"):
            provider, mdl = _FALLBACK
        return provider, mdl

    def chat(self, messages: list[dict], *, use_case: str = "tactical",
             tools: Optional[list] = None, model: Optional[str] = None,
             temperature: Optional[float] = None) -> dict:
        provider, mdl = self._route(use_case, model)

        # ---- Circuit-breaker guard ---------------------------------------- #
        if time.time() < self._cb_open_until:
            remaining = round(self._cb_open_until - time.time(), 1)
            return {"ok": False, "error": "LLM_CIRCUIT_OPEN",
                    "detail": f"circuit open for {remaining}s more"}

        t0 = time.time()
        resp, err = None, None
        try:
            if provider == "ollama":
                if self._ollama is None:
                    raise RuntimeError("ollama client unavailable")
                kwargs: dict[str, Any] = {
                    "model": mdl,
                    "messages": messages,
                    "timeout": _LLM_TIMEOUT,
                }
                if tools:
                    kwargs["tools"] = tools
                if temperature is not None:
                    kwargs["options"] = {"temperature": float(temperature)}
                # gpt-oss is a harmony/reasoning model: enable the reasoning
                # channel so Ollama routes chain-of-thought into message.thinking
                # and keeps message.content as the clean final answer (the JSON
                # the doctrine AAR parses). Guarded for older ollama clients that
                # lack the `think` kwarg.
                if mdl.startswith("gpt-oss"):
                    kwargs["think"] = True
                try:
                    resp = self._ollama.chat(**kwargs)
                except TypeError:
                    # Older ollama client: strip unknown kwargs and retry once.
                    kwargs.pop("think", None)
                    kwargs.pop("timeout", None)
                    resp = self._ollama.chat(**kwargs)
            else:  # pragma: no cover - cloud stub (no keys in this env)
                raise RuntimeError(f"provider '{provider}' not configured")
        except Exception as e:
            raw = str(e)
            # Normalise timeout errors into a stable sentinel string.
            if any(kw in raw.lower() for kw in ("timeout", "timed out", "read timeout")):
                err = "LLM_TIMEOUT"
            else:
                err = raw
        latency_ms = (time.time() - t0) * 1000.0

        # ---- Update circuit-breaker state ---------------------------------- #
        if err:
            self._cb_failures += 1
            if self._cb_failures >= _CB_THRESHOLD:
                self._cb_open_until = time.time() + _CB_COOL_OFF
        else:
            self._cb_failures = 0  # success → reset

        self._audit(provider, mdl, use_case, resp, latency_ms, err)
        if err:
            return {"ok": False, "error": err}
        return {"ok": True, "provider": provider, "model": mdl, "response": resp}

    # ------------------------------------------------------------------ #
    def _audit(self, provider, model, use_case, resp, latency_ms, err) -> None:
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "provider": provider,
            "model": model,
            "use_case": use_case,
            "latency_ms": round(latency_ms, 1),
            "status": "error" if err else "ok",
        }
        if err:
            rec["error"] = err
        elif resp is not None:
            # Ollama returns token counts; handle dict / attr access
            def _g(o, k):
                return o.get(k) if isinstance(o, dict) else getattr(o, k, None)
            rec["prompt_tokens"] = _g(resp, "prompt_eval_count")
            rec["completion_tokens"] = _g(resp, "eval_count")
        try:
            with open(self.audit_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
        except Exception:
            pass


# shared default instance
_GATEWAY: LLMGateway | None = None


def get_gateway() -> LLMGateway:
    global _GATEWAY
    if _GATEWAY is None:
        _GATEWAY = LLMGateway()
    return _GATEWAY
