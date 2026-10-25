"""Helpers shared by the `.blend` commands (scene, objects, render, export, exec).

Two `.blend` resolvers live here because the commands' observable messages differ and must stay as they
were (see each docstring): `resolve_blend` (scene/objects, the canonical one) and `resolve_blend_inferred`
(render/export). `blend_candidates` and `quote_name` are shared by all of them, exec included.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..errors import AxiError
from ..spec import DEFAULT_LIVE_HOST, DEFAULT_LIVE_PORT, CommandSpec, Invocation

MAX_NAMES = 5


# -- .blend resolution ----------------------------------------------------------------------------

def blend_candidates(cwd: Path | None = None) -> list[Path]:
    cwd = cwd or Path.cwd()
    try:
        return sorted(p for p in cwd.iterdir() if p.suffix.lower() == ".blend" and p.is_file())
    except OSError:
        return []


def quote_name(value: str) -> str:
    return f'"{value}"' if (" " in value or not value) else value


def invalid(message: str, help: list[str]) -> AxiError:
    return AxiError("VALIDATION_ERROR", message, help=help, exit_code=2)


def resolve_blend(inv: Invocation, positional: str = "file") -> tuple[Path | None, str | None]:
    """Return (absolute path or None in live mode, label echoed as `file:`).

    Explicit positional wins; otherwise exactly one `.blend` in the cwd is used (decision D3);
    zero or several -> VALIDATION_ERROR (exit 2) with candidates. Used by scene, objects and object.
    """
    raw = inv.positionals.get(positional)
    cmd = inv.command
    if inv.globals.live:
        if raw:
            raise invalid(f"--live uses the file open in Blender; drop <{positional}>", [f"blender-axi {cmd} --live"])
        return None, None
    if raw:
        path = Path(raw)
        if path.suffix.lower() != ".blend":
            raise invalid(f"expected a .blend file, got '{raw}'", [f"blender-axi {cmd} --help"])
        if not path.is_file():
            cands = blend_candidates()
            help_ = ["Candidates in the current directory: " + ", ".join(p.name for p in cands[:MAX_NAMES])] if cands \
                else ["Run `blender-axi` to list .blend files in the current directory"]
            raise AxiError("NOT_FOUND", f"file not found: {raw}", help=help_)
        return path.resolve(), raw
    cands = blend_candidates()
    if len(cands) == 1:
        return cands[0].resolve(), cands[0].name
    if not cands:
        raise invalid(f"no <{positional}> given and no .blend file in {Path.cwd()}", [f"blender-axi {cmd} <file.blend>"])
    shown = cands[:MAX_NAMES]
    raise invalid(f"{len(cands)} .blend files in {Path.cwd()}; pass one as <{positional}>",
                  [f"blender-axi {cmd} {quote_name(p.name)}" for p in shown]
                  + ([f"... and {len(cands) - len(shown)} more"] if len(cands) > len(shown) else []))


def resolve_blend_inferred(inv: Invocation, cmd: str) -> tuple[Path | None, bool]:
    """Variant used by render and export: returns (path, inferred) and, unlike `resolve_blend`, lets an
    explicit file through under --live, checks existence before the suffix, and words its errors differently.
    """
    given = inv.positionals.get("file")
    if given:
        path = Path(given)
        if not path.is_file():
            raise AxiError("NOT_FOUND", f"file not found: {given}",
                           help=["Run `blender-axi` to list .blend files in the current directory"])
        if path.suffix.lower() != ".blend":
            raise invalid(f"expected a .blend file, got {given}", [f"blender-axi {cmd} --help"])
        return Path(os.path.abspath(path)), False
    if inv.globals.live:
        return None, False
    found = blend_candidates()
    if len(found) == 1:
        return Path.cwd() / found[0].name, True
    if not found:
        raise invalid("no <file> given and no .blend file in the current directory",
                      [f"blender-axi {cmd} <file.blend> ...", f"blender-axi {cmd} --help"])
    shown = [p.name for p in found[:MAX_NAMES]]
    raise invalid(
        f"no <file> given and {len(found)} .blend files in the current directory: " + ", ".join(shown)
        + (", ..." if len(found) > len(shown) else ""),
        [f"blender-axi {cmd} {quote_name(name)} ..." for name in shown[:3]])


# -- hints ----------------------------------------------------------------------------------------

def carry_suffix(inv: Invocation) -> str:
    """Flags that keep a hint on the same Blender: ` --live [--host h] [--port p] [--user-prefs]` or empty."""
    g = inv.globals
    out = ""
    if g.live:
        out += " --live"
        if g.host != DEFAULT_LIVE_HOST:
            out += f" --host {g.host}"
        if g.port != DEFAULT_LIVE_PORT:
            out += f" --port {g.port}"
    if g.user_prefs:
        out += " --user-prefs"
    return out


live_suffix = carry_suffix  # v1 name, still imported by render/export/exec


class Hints:
    """Builds `blender-axi ...` hint lines that carry --live/--host/--port/--user-prefs forward and drop <file> in live mode."""

    def __init__(self, inv: Invocation):
        self.live = inv.globals.live
        self.suffix = carry_suffix(inv)

    def cmd(self, text: str) -> str:
        if self.live:
            text = text.replace(" <file>", "")
        return f"blender-axi {text}{self.suffix}"


def display_path(path: str) -> str:
    """Path relative to the cwd when it lives under it, else absolute; always forward slashes."""
    try:
        rel = os.path.relpath(path, os.getcwd())
    except ValueError:  # different drive
        return path.replace("\\", "/")
    if rel.startswith(".."):
        return path.replace("\\", "/")
    return rel.replace("\\", "/")


def posix_path(path: str) -> str:
    """`path` with forward slashes (TOON would otherwise quote and double Windows backslashes)."""
    return Path(path).as_posix() if path else path


def replay_command(inv: Invocation, spec: CommandSpec, extra: str) -> str:
    """`blender-axi <cmd> [file] <flags as given> <extra>` for hints that say 'rerun with --force'."""
    parts = ["blender-axi", spec.name]
    given = inv.positionals.get("file")
    if given:
        parts.append(quote_name(given))
    for flag in spec.flags:
        value = inv.flags.get(flag.name)
        if value is None or value is False or value == flag.default or flag.name == "--force":
            continue
        parts.append(flag.name if value is True else f"{flag.name} {quote_name(str(value))}")
    return " ".join(parts) + carry_suffix(inv) + " " + extra


def refused(inv: Invocation, spec: CommandSpec, path: str,
            alternative: str = "Or choose another path with --out <path>") -> AxiError:
    """WRITE_REFUSED for an existing render/export/new output."""
    return AxiError("WRITE_REFUSED", f"output exists: {display_path(path)}", help=[
        f"Run `{replay_command(inv, spec, '--force')}` to overwrite", alternative])


# -- formatting -----------------------------------------------------------------------------------

def fmt_num(v: float) -> str:
    """3 dp, trailing zeros dropped, no '-0'."""
    s = f"{round(float(v), 3):.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def fmt_vec(values) -> str:
    return ",".join(fmt_num(v) for v in values)


def names_summary(names: list[str]) -> str:
    """`3 (A, B, C)` capped at MAX_NAMES names; `0` when empty."""
    if not names:
        return "0"
    shown = ", ".join(names[:MAX_NAMES])
    more = f", +{len(names) - MAX_NAMES} more" if len(names) > MAX_NAMES else ""
    return f"{len(names)} ({shown}{more})"


def with_warnings(data: dict, warnings: list[str]) -> dict:
    if warnings:
        data["warning"] = "; ".join(warnings)
    return data


def translate_not_found(err: AxiError, help_: list[str]) -> AxiError:
    """Replace the transport's generic help on a NOT_FOUND op error with something actionable."""
    if err.code == "NOT_FOUND":
        err.help = list(help_)
    return err
