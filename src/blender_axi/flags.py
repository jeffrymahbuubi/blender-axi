"""Flag parser (plan section 3.3).

Accepts `--flag value`, `--flag=value`, repeated/comma-separated `list` flags, `--no-<bool>`,
positionals in order and `--` to end flag parsing. Global flags are accepted on every command.
`--help` anywhere short-circuits validation.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from .errors import AxiError
from .spec import GLOBAL_FLAGS, CommandSpec, Flag, GlobalOpts, Invocation

PROG = "blender-axi"
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def key_of(name: str) -> str:
    """'--name-contains' -> 'name_contains'."""
    return name.lstrip("-").replace("-", "_")


class FlagValues(dict):
    """Parsed flag values; lookups accept dashes or underscores (`v["name-contains"]` == `v["name_contains"]`)."""

    @staticmethod
    def _k(k):
        return k.lstrip("-").replace("-", "_") if isinstance(k, str) else k

    def __getitem__(self, k):
        return super().__getitem__(self._k(k))

    def __setitem__(self, k, v):
        super().__setitem__(self._k(k), v)

    def __contains__(self, k):
        return super().__contains__(self._k(k))

    def get(self, k, default=None):
        return super().get(self._k(k), default)


def _fail(spec: CommandSpec, message: str, help: list[str] | None = None) -> AxiError:
    return AxiError("VALIDATION_ERROR", message, help=(help or []) + [f"{PROG} {spec.name} --help"], exit_code=2)


def _valid_flags_line(spec: CommandSpec) -> str:
    names = [f.name for f in spec.flags if f.name not in _GLOBAL_NAMES]
    if not names:
        return f"`{spec.name}` takes no flags besides the global ones (--json, --full, --debug, ...)"
    return f"Valid flags for `{spec.name}`: " + ", ".join(names)


_GLOBAL_NAMES = {f.name for f in GLOBAL_FLAGS}
_GLOBAL_FIELDS = {f.name: key_of(f.name) for f in GLOBAL_FLAGS if key_of(f.name) in {x.name for x in dataclasses.fields(GlobalOpts)}}


def _convert_one(spec: CommandSpec, flag: Flag, raw: str) -> Any:
    kind = flag.kind
    if kind == "int":
        try:
            value: Any = int(raw)
        except ValueError:
            raise _fail(spec, f"{flag.name} expects an integer, got '{raw}'") from None
    elif kind == "float":
        try:
            value = float(raw)
        except ValueError:
            raise _fail(spec, f"{flag.name} expects a number, got '{raw}'") from None
    else:
        value = raw
    if flag.choices:
        for choice in flag.choices:
            if str(choice) == str(value) or str(choice).lower() == str(value).lower():
                return choice
        raise _fail(spec, f"{flag.name} must be one of {', '.join(flag.choices)}; got '{raw}'")
    return value


def _convert_bool(spec: CommandSpec, flag: Flag, raw: str) -> bool:
    low = raw.strip().lower()
    if low in _TRUE:
        return True
    if low in _FALSE:
        return False
    raise _fail(spec, f"{flag.name} expects true or false, got '{raw}'")


def _looks_like_flag(tok: str) -> bool:
    return tok.startswith("--") and len(tok) > 2


def _parse(spec: CommandSpec, args: list[str], strict: bool) -> Invocation:
    cmd_flags = {f.name: f for f in spec.flags if f.name not in _GLOBAL_NAMES}
    by_name: dict[str, Flag] = {**cmd_flags, **{f.name: f for f in GLOBAL_FLAGS}}
    renamed = {old: f.name for f in spec.flags for old in f.renamed_from}

    values = FlagValues()
    for f in cmd_flags.values():
        values[f.name] = False if (f.kind == "bool" and f.default is None) else f.default
    gvals: dict[str, Any] = {}
    seen: set[str] = set()
    positionals: list[str] = []

    def store(flag: Flag, value: Any) -> None:
        target = gvals if flag.name in _GLOBAL_NAMES else values
        k = _GLOBAL_FIELDS.get(flag.name) if flag.name in _GLOBAL_NAMES else flag.name
        if k is None:  # --help=...: handled by parse(), not stored
            return
        if flag.kind == "list":
            existing = target.get(k) if flag.name in seen else None
            target[k] = (existing or []) + value
        else:
            target[k] = value
        seen.add(flag.name)

    def step(i: int) -> int:
        tok = args[i]
        if not _looks_like_flag(tok):
            if tok.startswith("-") and tok != "-" and not _is_number(tok):
                raise _fail(spec, f"unknown flag {tok} for `{spec.name}`", [_valid_flags_line(spec)])
            positionals.append(tok)
            return i + 1
        name, eq, inline = tok.partition("=")
        flag = by_name.get(name)
        negated = False
        if flag is None and name.startswith("--no-"):
            base = by_name.get("--" + name[5:])
            if base is not None and base.kind == "bool" and not eq:
                flag, negated = base, True
        if flag is None:
            if name in renamed:
                raise _fail(spec, f"{name} was renamed; use {renamed[name]} instead", [_valid_flags_line(spec)])
            raise _fail(spec, f"unknown flag {name} for `{spec.name}`", [_valid_flags_line(spec)])
        if flag.kind == "bool":
            store(flag, False if negated else (_convert_bool(spec, flag, inline) if eq else True))
            return i + 1
        if eq:
            raw, nxt = inline, i + 1
        else:
            if i + 1 >= len(args) or _looks_like_flag(args[i + 1]):
                raise _fail(spec, f"{name} requires a value")
            raw, nxt = args[i + 1], i + 2
        if flag.kind == "list":
            items = [p.strip() for p in raw.split(",") if p.strip()]
            store(flag, [_convert_one(spec, dataclasses.replace(flag, kind="str"), p) for p in items])
        else:
            store(flag, _convert_one(spec, flag, raw))
        return nxt

    i = 0
    while i < len(args):
        if args[i] == "--":
            positionals.extend(args[i + 1:])
            break
        try:
            i = step(i)
        except AxiError:
            if strict:
                raise
            i += 1

    pos: dict[str, str] = {}
    for idx, p in enumerate(spec.positionals):
        if idx < len(positionals):
            pos[p.name] = positionals[idx]
        elif p.required and strict:
            raise _fail(spec, f"missing required argument <{p.name}> for `{spec.name}`", _example_hint(spec))
    if strict and len(positionals) > len(spec.positionals):
        extra = positionals[len(spec.positionals)]
        raise _fail(spec, f"unexpected argument '{extra}' for `{spec.name}`")
    if strict:
        for f in cmd_flags.values():
            if f.required and f.name not in seen:
                raise _fail(spec, f"missing required flag {f.name} for `{spec.name}`", _example_hint(spec))

    gopts = GlobalOpts(**gvals)
    return Invocation(command=spec.name, positionals=pos, flags=values, globals=gopts, transport=None)


def _is_number(tok: str) -> bool:
    try:
        float(tok)
    except ValueError:
        return False
    return True


def _example_hint(spec: CommandSpec) -> list[str]:
    return [f"Example: {spec.examples[0]}"] if spec.examples else []


def parse(spec: CommandSpec, argv: list[str]) -> tuple[Invocation, bool]:
    """Parse `argv` (args after the command name, global flags anywhere) -> (Invocation, wants_help).

    With --help anywhere (before a bare `--`) nothing is validated; globals are still filled (--json).
    """
    args = list(argv)
    scan = args[: args.index("--")] if "--" in args else args
    wants_help = any(a in ("--help", "-h") for a in scan)
    if wants_help:
        args = [a for a in args if a not in ("--help", "-h")]
        return _parse(spec, args, strict=False), True
    return _parse(spec, args, strict=True), False
