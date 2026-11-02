"""Pluggable token counting interface for ACC-MCP."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


class TokenCounter(ABC):
    """Abstract interface for counting tokens in text."""

    @abstractmethod
    def count_tokens(self, text: str) -> int:
        """Return the number of tokens in the given text."""
        ...


class HeuristicTokenCounter(TokenCounter):
    """Fast offline token counter using tiktoken with character-ratio fallback."""

    def __init__(self, encoding_name: str = "cl100k_base") -> None:
        self.encoding_name = encoding_name
        self._encoder: Any = None
        self._init_encoder()

    def _init_encoder(self) -> None:
        try:
            import tiktoken

            self._encoder = tiktoken.get_encoding(self.encoding_name)
        except Exception as exc:
            logger.debug("tiktoken unavailable or failed to initialize (%s); using heuristic", exc)
            self._encoder = None

    def count_tokens(self, text: str) -> int:
        if not text:
            return 0
        if self._encoder is not None:
            try:
                return len(self._encoder.encode(text, disallowed_special=()))
            except Exception:
                pass
        # Fallback: standard code estimation ~3.8 chars per token
        return max(1, int(len(text) / 3.8))


class EndpointTokenCounter(TokenCounter):
    """Token counter querying an active local model runner (/tokenize endpoint)."""

    def __init__(
        self,
        endpoint_url: str = "http://127.0.0.1:8080/tokenize",
        model_name: str | None = None,
        timeout: float = 2.0,
        fallback: TokenCounter | None = None,
    ) -> None:
        self.endpoint_url = endpoint_url
        self.model_name = model_name
        self.timeout = timeout
        self.fallback = fallback or HeuristicTokenCounter()

    def count_tokens(self, text: str) -> int:
        if not text:
            return 0

        try:
            import httpx

            # llama-server endpoint: POST /tokenize {"content": text}
            # Ollama endpoint: POST /api/tokenize {"model": "...", "prompt": text}
            is_ollama = "/api/tokenize" in self.endpoint_url
            payload: dict[str, Any]
            if is_ollama:
                payload = {"model": self.model_name or "qwen2.5-coder", "prompt": text}
            else:
                payload = {"content": text}

            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(self.endpoint_url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    tokens = data.get("tokens")
                    if isinstance(tokens, list):
                        return len(tokens)
        except Exception as exc:
            logger.debug("Endpoint token counter query failed: %s; falling back", exc)

        return self.fallback.count_tokens(text)


def get_token_counter(endpoint_url: str | None = None, model_name: str | None = None) -> TokenCounter:
    """Convenience factory returning an endpoint or heuristic token counter."""
    if endpoint_url:
        return EndpointTokenCounter(endpoint_url=endpoint_url, model_name=model_name)
    return HeuristicTokenCounter()
