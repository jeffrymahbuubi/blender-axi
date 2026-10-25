"""bench.tokens: request shape, chars fallback, memoising, retry and degrade rules. No network, no key needed."""

import io
import json
import urllib.error

import pytest
from bench import tokens
from bench.tokens import DEFAULT_MODEL, TokenCounter, from_env

KEY = "fake-test-key-0123456789-SECRET"


class FakeResponse:
    def __init__(self, status=200, payload=None, headers=None):
        self.status = status
        self.headers = headers or {}
        self._body = json.dumps(payload if payload is not None else {}).encode("utf-8")

    def read(self):
        return self._body


class FakeOpener:
    """Replays a script of FakeResponse objects or exceptions; records every request."""

    def __init__(self, *script):
        self.script = list(script)
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append((request, timeout))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(tokens.time, "sleep", lambda s: None)


def _counter(*script, **kw):
    opener = FakeOpener(*script)
    return TokenCounter(api_key=KEY, opener=opener, **kw), opener


def test_request_body_is_exact():
    counter, _ = _counter()
    assert json.loads(counter.request_body("héllo")) == {
        "model": DEFAULT_MODEL,
        "messages": [{"role": "user", "content": "héllo"}],
    }


def test_request_headers_are_exactly_the_three():
    counter, _ = _counter()
    assert counter.request_headers() == {
        "x-api-key": KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }


def test_request_goes_to_count_tokens_url_with_post():
    counter, opener = _counter(FakeResponse(payload={"input_tokens": 3}), timeout=7.0)
    counter.count("x")
    request, timeout = opener.requests[0]
    assert request.full_url == tokens.COUNT_URL
    assert request.get_method() == "POST"
    assert timeout == 7.0


def test_key_never_in_repr_or_str():
    counter, _ = _counter()
    assert KEY not in repr(counter)
    assert KEY not in str(counter)
    assert "SECRET" not in repr(counter)


def test_no_key_is_chars_mode_and_never_calls_opener():
    opener = FakeOpener()
    counter = TokenCounter(opener=opener)
    assert counter.unit == "chars"
    assert counter.model is None
    assert counter.count("hello") is None
    assert opener.requests == []


def test_blank_key_is_chars_mode():
    assert TokenCounter(api_key="   ").unit == "chars"


def test_success_and_memoised():
    counter, opener = _counter(FakeResponse(payload={"input_tokens": 14}))
    assert counter.unit == "tokens"
    assert counter.model == DEFAULT_MODEL
    assert counter.count("same text") == 14
    assert counter.count("same text") == 14
    assert len(opener.requests) == 1


def test_different_texts_each_requested():
    counter, opener = _counter(FakeResponse(payload={"input_tokens": 1}), FakeResponse(payload={"input_tokens": 2}))
    assert (counter.count("a"), counter.count("b")) == (1, 2)
    assert len(opener.requests) == 2


def test_429_then_200_retries(capsys):
    counter, opener = _counter(FakeResponse(429, headers={"retry-after": "0"}), FakeResponse(payload={"input_tokens": 9}))
    assert counter.count("x") == 9
    assert len(opener.requests) == 2
    assert counter.unit == "tokens"
    assert capsys.readouterr().err == ""


def test_429_retry_after_is_capped(monkeypatch):
    slept = []
    monkeypatch.setattr(tokens.time, "sleep", slept.append)
    counter, _ = _counter(FakeResponse(429, headers={"retry-after": "999"}), FakeResponse(payload={"input_tokens": 1}))
    counter.count("x")
    assert slept == [10.0]


def test_429_gives_up_after_two_retries(capsys):
    counter, opener = _counter(*[FakeResponse(429, headers={"retry-after": "0"}) for _ in range(3)])
    assert counter.count("x") is None
    assert len(opener.requests) == 3
    assert counter.unit == "chars"
    assert "HTTP 429" in capsys.readouterr().err


def test_401_degrades_to_chars_with_one_note(capsys):
    err = urllib.error.HTTPError(tokens.COUNT_URL, 401, "Unauthorized", {}, io.BytesIO(b"{}"))
    counter, opener = _counter(err)
    assert counter.count("a") is None
    assert counter.count("b") is None
    assert counter.unit == "chars"
    assert counter.model is None
    assert len(opener.requests) == 1  # no more requests once degraded
    stderr = capsys.readouterr().err
    assert stderr.count("note: token counting unavailable") == 1
    assert "HTTP 401" in stderr
    assert stderr.endswith("reporting chars\n")


def test_non_200_response_object_degrades(capsys):
    counter, _ = _counter(FakeResponse(500))
    assert counter.count("a") is None
    assert "HTTP 500" in capsys.readouterr().err


def test_network_error_degrades_without_leaking_key(capsys):
    counter, _ = _counter(urllib.error.URLError(OSError("no route")))
    assert counter.count("a") is None
    err = capsys.readouterr().err
    assert "OSError" in err
    assert KEY not in err


def test_timeout_degrades(capsys):
    counter, _ = _counter(TimeoutError("timed out"))
    assert counter.count("a") is None
    assert "TimeoutError" in capsys.readouterr().err


@pytest.mark.parametrize("payload", [{}, {"input_tokens": "5"}, {"input_tokens": True}, []])
def test_unexpected_response_degrades(payload, capsys):
    counter, _ = _counter(FakeResponse(payload=payload))
    assert counter.count("a") is None
    assert "unexpected response" in capsys.readouterr().err


def test_from_env_prefers_dedicated_name():
    counter = from_env(environ={"BLENDER_AXI_ANTHROPIC_KEY": KEY, "ANTHROPIC_API_KEY": "other"})
    assert counter.unit == "tokens"
    assert counter.request_headers()["x-api-key"] == KEY


def test_from_env_falls_back_to_anthropic_key():
    counter = from_env(environ={"ANTHROPIC_API_KEY": "other"})
    assert counter.request_headers()["x-api-key"] == "other"


def test_from_env_without_key_is_chars():
    counter = from_env(environ={})
    assert counter.unit == "chars"
    assert counter.count("hello") is None


def test_from_env_default_environ_unset(monkeypatch):
    monkeypatch.delenv("BLENDER_AXI_ANTHROPIC_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert from_env().unit == "chars"


def test_from_env_model_passthrough():
    assert from_env(model="m-1", environ={"ANTHROPIC_API_KEY": KEY}).model == "m-1"
