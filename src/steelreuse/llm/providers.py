"""Provider-agnostic LLM interface. Swap models with one line; default needs no API key.

Selection (``select_provider``):
  * ``GEMINI_API_KEY`` set            -> :class:`GeminiProvider` (free-tier friendly)
  * else ``STEELREUSE_LLM=ollama``    -> :class:`OllamaProvider` (local, offline)
  * else                               -> :class:`NullProvider` (deterministic, no model call)

Every provider only ever produces *prose*; numbers are injected by the report layer, and the LLM
output is screened for invented numbers before use.
"""

from __future__ import annotations

import os
from typing import Protocol


class LLMProvider(Protocol):
    name: str

    def complete(self, system: str, prompt: str) -> str: ...


class NullProvider:
    """No model. Returns empty string so the report uses its deterministic narrative."""

    name = "null"

    def complete(self, system: str, prompt: str) -> str:
        return ""


class GeminiProvider:
    """Google Gemini via the ``google-genai`` SDK (lazy import)."""

    name = "gemini"

    def __init__(self, model: str | None = None, api_key: str | None = None):
        # Model is overridable via GEMINI_MODEL so a quota-throttled model (free-tier 429s on
        # gemini-2.5-flash) can be swapped for a sibling with headroom (e.g. gemini-2.5-flash-lite)
        # without a code change. Falls back to the flash default when unset.
        self.model = model or os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")

    def complete(self, system: str, prompt: str) -> str:  # pragma: no cover - needs network/key
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.api_key)
        resp = client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(system_instruction=system, temperature=0.2),
        )
        return resp.text or ""


class OllamaProvider:
    """Local Ollama HTTP API (lazy, stdlib only)."""

    name = "ollama"

    def __init__(self, model: str = "llama3.1", host: str = "http://localhost:11434"):
        self.model = model
        self.host = host

    def complete(self, system: str, prompt: str) -> str:  # pragma: no cover - needs local server
        import json
        import urllib.request

        body = json.dumps({
            "model": self.model, "system": system, "prompt": prompt, "stream": False,
        }).encode()
        req = urllib.request.Request(f"{self.host}/api/generate", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read()).get("response", "")


def select_provider() -> LLMProvider:
    if os.environ.get("GEMINI_API_KEY"):
        return GeminiProvider()
    if os.environ.get("STEELREUSE_LLM", "").lower() == "ollama":
        return OllamaProvider()
    return NullProvider()
