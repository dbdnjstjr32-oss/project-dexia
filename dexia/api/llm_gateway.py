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

try:
    import ollama
except Exception:  # pragma: no cover
    ollama = None

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_AUDIT_PATH = os.path.join(_ROOT, "llm_audit.jsonl")

# use_case -> (provider, model). Cloud rows only fire if an API key is present;
# otherwise the gateway transparently falls back to the local Ollama model.
ROUTING = {
    "tactical": ("ollama", "llama3.1:8b-instruct-q4_K_M"),  # high precision
    "summary": ("ollama", "qwen2.5:7b"),                    # fast situation report
    "airgap": ("ollama", "llama3.1:8b-instruct-q4_K_M"),    # local-only
}
_FALLBACK = ("ollama", "llama3.1:8b-instruct-q4_K_M")


class LLMGateway:
    def __init__(self, audit_path: str = DEFAULT_AUDIT_PATH, host: Optional[str] = None) -> None:
        self.audit_path = audit_path
        self._ollama = (ollama.Client(host=host) if (ollama and host) else
                        (ollama.Client() if ollama else None))

    def available(self) -> bool:
        return self._ollama is not None

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
        t0 = time.time()
        resp, err = None, None
        try:
            if provider == "ollama":
                if self._ollama is None:
                    raise RuntimeError("ollama client unavailable")
                kwargs: dict[str, Any] = {"model": mdl, "messages": messages}
                if tools:
                    kwargs["tools"] = tools
                if temperature is not None:
                    kwargs["options"] = {"temperature": float(temperature)}
                resp = self._ollama.chat(**kwargs)
            else:  # pragma: no cover - cloud stub (no keys in this env)
                raise RuntimeError(f"provider '{provider}' not configured")
        except Exception as e:
            err = str(e)
        latency_ms = (time.time() - t0) * 1000.0

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
