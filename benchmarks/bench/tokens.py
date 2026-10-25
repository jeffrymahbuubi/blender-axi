"""Token counting for the benchmark (plan section 4.3, decisions K5 and K6).

Exact counts come from the Anthropic `count_tokens` endpoint (free, no model call), reached with stdlib `urllib` only.
Without a key, or after any non-recoverable error, the counter drops to "chars" mode: `count()` returns None and the
report uses characters instead.

The API key is read from the environment only (`BLENDER_AXI_ANTHROPIC_KEY` first, then `ANTHROPIC_API_KEY`) and is
kept in one private attribute. It never appears in `repr`/`str`, notes, exceptions or logs: failure reasons are built
from HTTP status codes and exception class names, never from the request.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request

COUNT_URL = "https://api.anthropic.com/v1/messages/count_tokens"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-opus-5-5"
KEY_ENV_VARS = ("BLENDER_AXI_ANTHROPIC_KEY", "ANTHROPIC_API_KEY")  # dedicated name first (see plan K5)
MAX_RETRIES = 2  # after the first attempt, on HTTP 429 only
RETRY_AFTER_CAP = 10.0


class _Unavailable(Exception):
    """Internal: counting failed for a reason that switches the run to chars mode. Carries a key-free reason."""


def _header(headers, name: str):
    """Case-insensitive header lookup for http.client messages and plain dicts alike."""
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if getter is None:
        return None
    value = getter(name)
    if value is None:
        value = getter(name.title())
    return value


def _retry_delay(headers) -> float:
    try:
        return max(0.0, min(float(_header(headers, "retry-after")), RETRY_AFTER_CAP))
    except (TypeError, ValueError):
        return 1.0


class TokenCounter:
    def __init__(self, model: str = DEFAULT_MODEL, api_key: str | None = None, opener=None, timeout: float = 30.0):
        self._key = (api_key or "").strip() or None
        self._model = model
        self._opener = opener
        self._timeout = timeout
        self._cache: dict[str, int] = {}
        self._disabled = self._key is None

    def __repr__(self) -> str:
        return f"TokenCounter(unit={self.unit!r}, model={self.model!r})"

    __str__ = __repr__

    @property
    def unit(self) -> str:
        return "chars" if self._disabled else "tokens"

    @property
    def model(self) -> str | None:
        return None if self._disabled else self._model

    def request_body(self, text: str) -> bytes:
        payload = {"model": self._model, "messages": [{"role": "user", "content": text}]}
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def request_headers(self) -> dict[str, str]:
        return {"x-api-key": self._key or "", "anthropic-version": API_VERSION, "content-type": "application/json"}

    def count(self, text: str) -> int | None:
        if self._disabled:
            return None
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest in self._cache:
            return self._cache[digest]
        try:
            value = self._request(text)
        except _Unavailable as exc:
            self._disabled = True
            print(f"note: token counting unavailable ({exc}), reporting chars", file=sys.stderr)
            return None
        self._cache[digest] = value
        return value

    def _get_opener(self):
        if self._opener is None:
            self._opener = urllib.request.build_opener()
        return self._opener

    def _request(self, text: str) -> int:
        body = self.request_body(text)
        for attempt in range(MAX_RETRIES + 1):
            request = urllib.request.Request(COUNT_URL, data=body, headers=self.request_headers(), method="POST")
            status, headers, raw = self._send(request)
            if status == 429 and attempt < MAX_RETRIES:
                time.sleep(_retry_delay(headers))
                continue
            if status != 200:
                raise _Unavailable(f"HTTP {status}")
            return self._parse(raw)
        raise _Unavailable("HTTP 429")  # not reached; keeps the type checker honest

    def _send(self, request) -> tuple[int, object, bytes]:
        try:
            response = self._get_opener().open(request, timeout=self._timeout)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, b""
        except urllib.error.URLError as exc:
            raise _Unavailable(f"network error: {type(exc.reason).__name__}") from None
        except (OSError, ValueError) as exc:  # timeouts, resets, bad URL
            raise _Unavailable(f"network error: {type(exc).__name__}") from None
        try:
            return int(getattr(response, "status", 200)), getattr(response, "headers", None), response.read()
        except OSError as exc:
            raise _Unavailable(f"network error: {type(exc).__name__}") from None
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

    @staticmethod
    def _parse(raw: bytes) -> int:
        try:
            value = json.loads(raw.decode("utf-8"))["input_tokens"]
        except (ValueError, KeyError, TypeError, AttributeError):
            raise _Unavailable("unexpected response") from None
        if isinstance(value, bool) or not isinstance(value, int):
            raise _Unavailable("unexpected response")
        return value


def from_env(model: str = DEFAULT_MODEL, environ=os.environ) -> TokenCounter:
    for name in KEY_ENV_VARS:
        key = (environ.get(name) or "").strip()
        if key:
            return TokenCounter(model=model, api_key=key)
    return TokenCounter(model=model)
