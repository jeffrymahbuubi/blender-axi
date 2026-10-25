"""Shared write/live/undo/backup logic of the typed modeling commands (`add`, `set`, `delete`, `material`).

Headless writes are explicit: `--in-place` (the file, with Blender's .blend1 backup) or `--out <copy.blend>`; neither is
an error (exit 2) that prints both full commands. With `--live` the change happens in the open session (no copy,
`--in-place` saves the open file), exactly as `exec --live`. The texts below are byte-identical to `exec_cmd`'s.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path

from ..errors import AxiError
from ..runner import RunOutcome, SaveSpec
from ..spec import CommandSpec, Flag, Invocation, Result
from ._common import Hints, carry_suffix, display_path, invalid, posix_path, quote_name, resolve_blend, with_warnings

NO_CHANGES = "none (no changes)"
LIVE_UNSAVED = "none (live session, unsaved)"
UNDO_TEXT = {"pushed": "pushed (Ctrl+Z in Blender reverts)", "unavailable": "unavailable (headless server)"}

MUTATE_FLAGS: tuple[Flag, ...] = (
    Flag("--in-place", "bool", help="Write the change into the file (Blender keeps a .blend1 backup); with --live, save the open file"),
    Flag("--out", "path", help="Write the changed file as a copy at this .blend path (not with --live)"),
    Flag("--force", "bool", help="Allow --out to overwrite an existing file"),
)


@dataclass
class Target:
    blend: Path | None  # None in live mode
    label: str  # the `file:` value: positional/cwd name headless; posix path of the open file or "(unsaved)" live
    save: SaveSpec
    backup_probe: tuple[Path, int | None] | None  # (<file>.blend1, mtime_ns before) when in_place


def replay(inv: Invocation, spec: CommandSpec, extra: str, drop: tuple[str, ...] = ("--force",)) -> str:
    """`blender-axi <cmd> <positionals as given> <flags as given> <extra>` (unlike `_common.replay_command`, keeps every
    positional); flags named in `drop` are left out."""
    parts = ["blender-axi", spec.name]
    for p in spec.positionals:
        given = inv.positionals.get(p.name)
        if given:
            parts.append(quote_name(given))
    for flag in spec.flags:
        value = inv.flags.get(flag.name)
        if value is None or value is False or value == flag.default or flag.name in drop:
            continue
        parts.append(flag.name if value is True else f"{flag.name} {quote_name(str(value))}")
    return " ".join(parts) + carry_suffix(inv) + " " + extra


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.exists() and b.exists() and os.path.samefile(a, b)
    except OSError:
        return False


def _live_target(inv: Invocation, spec: CommandSpec, in_place: bool, out) -> Target:
    live_examples = [e for e in spec.examples if "--live" in e][:1]
    if inv.positionals.get("file"):
        raise invalid("--live uses the file open in Blender; drop <file>", live_examples)
    if out is not None:
        raise invalid("live mode has no copy: changes stay in the open session; add --in-place to save the open file",
                      ["Keep the changes in the session, or save the open file with --in-place"])
    opened = str(inv.transport.run("ping", {}, blend=None, timeout=inv.globals.timeout).result.get("file") or "")
    if in_place and not opened:
        raise invalid("the open session has no file; save it in Blender first",
                      ["Save the session in Blender (File > Save), then rerun with --in-place",
                       "Or drop --in-place to keep the changes in the open session"])
    probe: tuple[Path, int | None] | None = None
    if in_place:
        bak = Path(opened + "1")
        probe = (bak, bak.stat().st_mtime_ns if bak.exists() else None)
    return Target(None, posix_path(opened) or "(unsaved)",
                  SaveSpec("in_place", opened) if in_place else SaveSpec("none"), probe)


def _headless_target(inv: Invocation, spec: CommandSpec, in_place: bool, out) -> Target:
    blend, label = resolve_blend(inv)
    assert blend is not None and label is not None
    if in_place and out is not None:
        raise invalid("--in-place and --out are mutually exclusive",
                      ["--in-place changes the original file; --out writes a copy"])
    if not in_place and out is None:
        raise invalid(f"{spec.name} changes {label}; say where to write it", [
            f"Run `{replay(inv, spec, '--in-place')}` to write the file (a .blend1 backup is kept)",
            f"Run `{replay(inv, spec, '--out <copy.blend>')}` to keep the original and write a copy"])
    if in_place:
        bak = blend.with_name(blend.name + "1")
        return Target(blend, label, SaveSpec("in_place", str(blend)), (bak, bak.stat().st_mtime_ns if bak.exists() else None))
    if not str(out).lower().endswith(".blend"):
        raise invalid(f"--out must be a .blend path, got {out}", ["Example: --out changed.blend"])
    target = Path(str(out)).expanduser().resolve()
    if target == blend or _same_file(target, blend):
        raise invalid("--out points at the input file; use --in-place",
                      [f"Run `{replay(inv, spec, '--in-place', ('--out', '--force'))}`"])
    if target.exists() and not inv.flags.get("force"):
        raise AxiError("WRITE_REFUSED", f"output exists: {display_path(str(target))}", help=[
            f"Run `{replay(inv, spec, '--force', ())}` to overwrite", "Or choose another path with --out <path>"])
    return Target(blend, label, SaveSpec("copy", str(target)), None)


def resolve_target(inv: Invocation, spec: CommandSpec) -> Target:
    """Where the change goes (see the module docstring); every failure is raised before Blender is started."""
    in_place, out = bool(inv.flags.get("in_place")), inv.flags.get("out")
    if inv.globals.live:
        return _live_target(inv, spec, in_place, out)
    return _headless_target(inv, spec, in_place, out)


def run_mutation(inv: Invocation, spec: CommandSpec, op: str, params: dict) -> tuple[Target, RunOutcome]:
    target = resolve_target(inv, spec)
    outcome = inv.transport.run(op, params, blend=target.blend, save=target.save, timeout=inv.globals.timeout)
    return target, outcome


def hint_cmd(inv: Invocation, target: Target, outcome: RunOutcome, text: str) -> str:
    """`blender-axi <text>` with `<file>` filled in (the written copy after --out; dropped under --live) and the carry flags."""
    if not inv.globals.live:
        shown = display_path(outcome.saved) if outcome.saved and target.save.mode == "copy" else target.label
        text = text.replace("<file>", quote_name(shown))
    return Hints(inv).cmd(text)


def finish(inv: Invocation, spec: CommandSpec, target: Target, outcome: RunOutcome, data: dict,
           hints: list[str]) -> Result:
    """Append the shared result tail to `data` (modified, saved, backup, undo, frame_changed, duration_ms, warning)."""
    live = inv.globals.live
    data["modified"] = outcome.modified
    if outcome.saved:
        data["saved"] = display_path(outcome.saved)
        if target.backup_probe is not None:
            bak, before = target.backup_probe
            if bak.exists() and (before is None or bak.stat().st_mtime_ns != before):
                data["backup"] = display_path(str(bak))
    else:
        data["saved"] = LIVE_UNSAVED if live and outcome.modified else NO_CHANGES
    if outcome.undo in UNDO_TEXT:
        data["undo"] = UNDO_TEXT[outcome.undo]
    if outcome.frame_changed:
        data["frame_changed"] = outcome.frame_changed
    data["duration_ms"] = outcome.duration_ms
    with_warnings(data, outcome.warnings)
    help_ = [f"Run `{hint_cmd(inv, target, outcome, 'objects <file>')}` to list the objects", *hints]
    if live and outcome.modified and not outcome.saved:
        help_.append("Changes are only in the open session: save in Blender, or rerun with --in-place to save the open file")
    return Result(data=data, help=help_)


def parse_vec(spec: CommandSpec, flag: str, raw: str | None, n: tuple[int, ...] = (3,)) -> list[float] | None:
    """`"1,2,3"` -> [1.0, 2.0, 3.0]; None stays None; wrong arity or a non-finite/non-number -> exit 2."""
    if raw is None:
        return None
    expects = "x,y,z" if n == (3,) else " or ".join(str(k) for k in n) + " comma-separated numbers"
    try:
        values = [float(part) for part in str(raw).split(",")]
    except ValueError:
        values = []
    if len(values) not in n or not all(math.isfinite(v) for v in values):
        raise invalid(f"{flag} expects {expects}, got '{raw}'", [f"Example: {flag} 1,2,3"])
    return values

