"""TOON (Token-Oriented Object Notation, spec v4.4) emitter for blender-axi.

Pure functions, stdlib only, no I/O.  Public surface (plan section 3.1):

    encode(value, *, indent=2, list_keys=frozenset({"help"})) -> str
    encode_json(value) -> str

Implemented encoder rules: 7.1 escapes, 7.2 value quoting, 7.3 key quoting, 2/3 numbers
(NaN and infinities become null), 8 objects, 9.1 inline primitive arrays and ``key: []``,
9.3 tabular arrays, 9.4 and 10 list form with objects as list items.

Deliberate scope limits (documented deviations from the full spec):
* Nested-uniform tabular columns (9.3) are NOT implemented: a column holding a dict or list
  disqualifies the array from tabular form and it is emitted in list form instead.
* Keyed tabular objects (9.5) are NOT implemented: objects always nest per section 8.
* Only the comma delimiter is supported.
* Host types: tuple is treated as a list, ``os.PathLike`` as ``str``, any other unknown
  value as null (section 3).

``list_keys`` (decision D1): a key named there whose value is a non-empty list of strings is
emitted in list form (``help[2]:`` then ``  - item`` lines) instead of the inline form, so each
hint stays on its own line.
"""

from __future__ import annotations

import json
import math
import os
import re
from typing import Any

JsonValue = Any  # None | bool | int | float | str | list | dict[str, JsonValue]

_UNQUOTED_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
_NUMERIC_LIKE = re.compile(r"^[+-]?[0-9]+(?:\.[0-9]+)?(?:e[+-]?[0-9]+)?$", re.IGNORECASE)
_RESERVED = frozenset({"true", "false", "null"})
_SPECIAL = set(':"\\[]{},')
_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def encode_json(value: JsonValue) -> str:
    """Return the `--json` rendering of `value`."""
    return json.dumps(value, ensure_ascii=False, indent=2)


def encode(
    value: JsonValue,
    *,
    indent: int = 2,
    list_keys: frozenset[str] = frozenset({"help"}),
) -> str:
    """Encode `value` as a TOON document (LF separated, no trailing newline, no blank lines)."""
    enc = _Encoder(indent, list_keys)
    value = _normalize(value)
    if isinstance(value, dict):
        lines = enc.object_lines(value, 0)
    elif isinstance(value, list):
        lines = enc.array_lines("", value, 0, None)
    else:
        text = enc.primitive(value)
        if isinstance(value, str) and value.startswith("﻿") and not text.startswith('"'):
            text = _quote(value)
        lines = [text]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Normalisation and scalar formatting
# ---------------------------------------------------------------------------


def _normalize(value: Any) -> Any:
    """Map host values onto the JSON model (spec section 3)."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {(k if isinstance(k, str) else str(k)): _normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    if isinstance(value, os.PathLike):
        return os.fspath(value) if isinstance(os.fspath(value), str) else None
    return None


def _check_surrogates(s: str) -> None:
    for ch in s:
        if "\ud800" <= ch <= "\udfff":
            raise ValueError("TOON cannot represent a string containing an unpaired surrogate")


def _escape(s: str) -> str:
    out = []
    for ch in s:
        if ch in _ESCAPES:
            out.append(_ESCAPES[ch])
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return "".join(out)


def _quote(s: str) -> str:
    _check_surrogates(s)
    return '"' + _escape(s) + '"'


def _needs_quote(s: str) -> bool:
    if s == "" or s != s.strip(" \t"):
        return True
    if s in _RESERVED or _NUMERIC_LIKE.match(s):
        return True
    if s[0] in "-#":
        return True
    return any(ch in _SPECIAL or ord(ch) < 0x20 for ch in s)


def _string(s: str) -> str:
    _check_surrogates(s)
    return _quote(s) if _needs_quote(s) else s


def _key(k: str) -> str:
    return k if _UNQUOTED_KEY.match(k) else _quote(k)


def _number(n: int | float) -> str:
    """Canonical number form (spec section 2)."""
    if isinstance(n, int):
        return str(n)
    if not math.isfinite(n):
        return "null"
    if n == 0:
        return "0"
    r = repr(n)
    if "e" in r:
        mant, exp = r.split("e")
        e = int(exp)
        if 1e-6 <= abs(n) < 1e21:
            # Expand without exponent, e.g. 1e-05 -> 0.00001, 1e+16 -> 10000000000000000.
            digits = mant.replace("-", "").replace(".", "")
            point = (mant.replace("-", "").index(".") if "." in mant else len(digits)) + e
            if point <= 0:
                body = "0." + "0" * (-point) + digits
            elif point >= len(digits):
                body = digits + "0" * (point - len(digits))
            else:
                body = digits[:point] + "." + digits[point:]
            if "." in body:
                body = body.rstrip("0").rstrip(".")
            return ("-" if n < 0 else "") + body
        if mant.endswith(".0"):
            mant = mant[:-2]
        return f"{mant}e{'+' if e >= 0 else '-'}{abs(e)}"
    if r.endswith(".0"):
        r = r[:-2]
    return r


def _is_prim(v: Any) -> bool:
    return v is None or isinstance(v, (bool, int, float, str))


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


class _Encoder:
    def __init__(self, indent: int, list_keys: frozenset[str]) -> None:
        self.size = max(1, int(indent))
        self.list_keys = list_keys

    def ind(self, depth: int) -> str:
        return " " * (self.size * depth)

    def primitive(self, v: Any) -> str:
        if v is None:
            return "null"
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return _number(v)
        return _string(v)

    # -- objects ------------------------------------------------------------

    def object_lines(self, obj: dict[str, Any], depth: int) -> list[str]:
        lines: list[str] = []
        for k, v in obj.items():
            lines.extend(self.field_lines(k, v, depth))
        return lines

    def field_lines(self, key: str, v: Any, depth: int) -> list[str]:
        ind = self.ind(depth)
        k = _key(key)
        if isinstance(v, dict):
            if not v:
                return [f"{ind}{k}:"]
            return [f"{ind}{k}:"] + self.object_lines(v, depth + 1)
        if isinstance(v, list):
            return self.array_lines(k, v, depth, key)
        return [f"{ind}{k}: {self.primitive(v)}"]

    # -- arrays under a key (or keyless at the root) -------------------------

    def array_lines(self, prefix: str, arr: list[Any], depth: int, raw_key: str | None) -> list[str]:
        ind = self.ind(depth)
        n = len(arr)
        if n == 0:
            return [f"{ind}{prefix}: []" if prefix else "[]"]
        if all(_is_prim(x) for x in arr):
            as_list = raw_key in self.list_keys and all(isinstance(x, str) for x in arr)
            if not as_list:
                cells = ",".join(self.primitive(x) for x in arr)
                return [f"{ind}{prefix}[{n}]: {cells}"]
        else:
            fields = self._tabular_fields(arr)
            if fields is not None:
                head = ",".join(_key(f) for f in fields)
                rows = [
                    self.ind(depth + 1) + ",".join(self.primitive(item[f]) for f in fields)
                    for item in arr
                ]
                return [f"{ind}{prefix}[{n}]{{{head}}}:"] + rows
        return [f"{ind}{prefix}[{n}]:"] + self.list_items(arr, depth + 1)

    @staticmethod
    def _tabular_fields(arr: list[Any]) -> list[str] | None:
        if not all(isinstance(x, dict) and x for x in arr):
            return None
        fields = list(arr[0].keys())
        keyset = set(fields)
        for item in arr:
            if set(item.keys()) != keyset or not all(_is_prim(v) for v in item.values()):
                return None
        return fields

    # -- list form (spec 9.4 and 10) ----------------------------------------

    def list_items(self, arr: list[Any], depth: int) -> list[str]:
        lines: list[str] = []
        for item in arr:
            ind = self.ind(depth)
            if isinstance(item, dict):
                if not item:
                    lines.append(f"{ind}-")
                    continue
                # First field rides on the hyphen line; its scope stays at depth + 2.
                body = self.object_lines(item, depth + 1)
                body[0] = f"{ind}- " + body[0][len(self.ind(depth + 1)):]
                lines.extend(body)
            elif isinstance(item, list):
                n = len(item)
                if n == 0:
                    lines.append(f"{ind}- [0]:")
                elif all(_is_prim(x) for x in item):
                    cells = ",".join(self.primitive(x) for x in item)
                    lines.append(f"{ind}- [{n}]: {cells}")
                else:
                    lines.append(f"{ind}- [{n}]:")
                    lines.extend(self.list_items(item, depth + 1))
            else:
                lines.append(f"{ind}- {self.primitive(item)}")
        return lines
