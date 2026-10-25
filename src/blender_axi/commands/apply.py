"""`apply`: run many modeling steps on a .blend (or the open session with --live) in ONE Blender start.

`apply <steps> [<file>]`: `<steps>` is a JSON file, or `-` for stdin: a list of steps, or `{"steps": [...]}`. A step is a
string (`"add cube --name Box"`, split like a shell; a leading `blender-axi` is tolerated) or an object
(`{"cmd": "add", "kind": "cube", "name": "Box", "location": [0, 0, 1]}`): `cmd`, then positional names and flag names
without dashes (`_` or `-`); a list becomes comma-separated, `true` a bare flag, `false` `--no-<flag>`, `null` is skipped.
A key that is both a positional and a flag (`set`'s `name`, `modifier`'s `object`) fills the positional; give the flag as
`"--name": ...` or through `"args": ["--object", "Cutter"]` (raw argv tokens appended after the generated ones). Steps cannot carry `<file>`, `--in-place/--out/--force`, `--new` or global flags: the file and the write
mode belong to `apply`.

Every step is parsed with its command's own spec and pure checks BEFORE Blender starts (`registry.BATCH` maps command to
`BatchSpec`), so a bad step exits 2 with the command's own message and `step N (cmd): ` in front, and nothing ran.
Headless the whole batch is all-or-nothing (a failing step raises, the bootstrap saves nothing); live it is best effort:
steps before the failing one stay applied and one undo step was pushed before step 1. `--check` stops after validation.
"""

from __future__ import annotations

import dataclasses
import importlib
import json
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path

from .. import flags as flagparser
from .. import registry
from ..errors import AxiError
from ..spec import BatchSpec, CommandSpec, Flag, GlobalOpts, Invocation, Positional, Result
from . import _mutate
from ._common import Hints, display_path, invalid, quote_name, resolve_blend

BATCH_TIMEOUT = 600.0  # a long recipe (imports, 20+ steps) can exceed the 120 s default
RESULT_LINES = 20
CANONICAL = ("add", "set", "delete", "material", "modifier", "configure", "import")
GUIDE = ("A steps file is a JSON list, e.g. [\"add cube --name Box\", {\"cmd\": \"set\", \"name\": \"Box\", "
         "\"location\": [0, 0, 1]}]")


@dataclass
class _Step:
    cmd: str
    argv: list[str]
    batch: BatchSpec
    params: dict


def _load(target: str):
    module, _, attr = target.partition(":")
    return getattr(importlib.import_module(module), attr)


def _supported() -> list[str]:
    names = list(registry.BATCH)
    return [n for n in CANONICAL if n in names] + [n for n in names if n not in CANONICAL]


def _fail(i: int, label: str, message: str, help_: list[str] | None = None) -> AxiError:
    return invalid(message, [*(help_ or []), f"Fix step {i} in {label} (nothing ran)"])


def _rewrap(i: int, cmd: str, label: str, err: AxiError) -> AxiError:
    """The command's own error with `step N (cmd): ` in front (same code and exit code)."""
    return AxiError(err.code, f"step {i} ({cmd}): {err.message}",
                    help=[*err.help, f"Fix step {i} in {label} (nothing ran)"], exit_code=err.exit_code, detail=err.detail)


# -- reading the document -------------------------------------------------------------------------

def _read_steps(inv: Invocation) -> tuple[list, str]:
    raw = inv.positionals["steps"]
    if raw == "-":
        stream = sys.stdin
        text = stream.buffer.read().decode("utf-8-sig") if hasattr(stream, "buffer") else stream.read()
        label = "stdin"
    else:
        path = Path(raw)
        if not path.is_file():
            raise AxiError("NOT_FOUND", f"steps file not found: {raw}",
                           help=["Pass a JSON file, or - to read the steps from stdin", GUIDE])
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as exc:
            raise invalid(f"cannot read {raw}: {exc}", [GUIDE]) from None
        label = display_path(raw)
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise invalid(f"steps: invalid JSON at line {exc.lineno} column {exc.colno}", [exc.msg, GUIDE]) from None
    steps = doc.get("steps") if isinstance(doc, dict) else doc
    if not isinstance(steps, list):
        raise invalid('steps: expected a list of steps or {"steps": [...]}', [GUIDE])
    if not steps:
        raise invalid("no steps", [GUIDE])
    return steps, label


# -- one step -------------------------------------------------------------------------------------

def _specs(i: int, label: str, cmd: str) -> tuple[CommandSpec, BatchSpec]:
    if cmd not in registry.BATCH or cmd not in registry.COMMANDS:
        raise _fail(i, label, f"step {i}: unknown command '{cmd}'; apply supports {', '.join(_supported())}",
                    ["Example step: add cube --name Box --location 0,0,1"])
    return _load(registry.COMMANDS[cmd]), _load(registry.BATCH[cmd])


def _text(i: int, label: str, key: str, value) -> str:
    """A step value as one argv token: strings as they are, numbers without a needless '.0', lists comma-joined."""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        parts = [_text(i, label, key, v) for v in value if not isinstance(v, (list, tuple, dict, bool)) and v is not None]
        if len(parts) != len(value):
            raise _fail(i, label, f"step {i}: the list for '{key}' may hold only strings and numbers")
        return ",".join(parts)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail(i, label, f"step {i}: the value of '{key}' must be a string, number or list")
    return str(int(value)) if isinstance(value, float) and value.is_integer() and abs(value) < 1e15 else str(value)


def _object_args(i: int, label: str, cmd: str, step: dict, spec: CommandSpec) -> list[str]:
    positional_names = [p.name for p in spec.positionals]
    given: dict[str, str] = {}
    flag_tokens: list[str] = []
    raw_args = step.get("args")
    if raw_args is not None and (not isinstance(raw_args, (list, tuple)) or any(
            isinstance(a, bool) or not isinstance(a, (str, int, float)) for a in raw_args)):
        raise _fail(i, label, f"step {i}: 'args' must be a list of strings and numbers",
                    ['Example: "args": ["--object", "Cutter"]'])
    for key, value in step.items():
        if key in ("cmd", "args") or value is None:
            continue
        explicit = str(key).startswith("-")
        name = str(key).lstrip("-").replace("_", "-")
        if not explicit and name == "file":
            raise _fail(i, label, f"step {i}: <file> belongs on apply, not on a step")
        if not explicit and name in positional_names:
            if isinstance(value, bool):
                raise _fail(i, label, f"step {i}: the value of '{key}' must be a string, number or list")
            given[name] = _text(i, label, str(key), value)
        elif value is True:
            flag_tokens.append(f"--{name}")
        elif value is False:
            flag_tokens.append(f"--no-{name}")
        else:
            text = _text(i, label, str(key), value)
            flag_tokens += [f"--{name}={text}"] if text.startswith("--") else [f"--{name}", text]
    for p in spec.positionals:  # a gap would shift the later positionals, so name the missing one here
        if p.required and p.name not in given:
            raise _fail(i, label, f"step {i} ({cmd}): missing required argument <{p.name}> for `{cmd}`",
                        [f"Add \"{p.name}\" to the step"])
    extra = [_text(i, label, "args", a) for a in raw_args or ()]  # raw argv tokens, appended last
    return [given[n] for n in positional_names if n in given] + flag_tokens + extra


def _check_allowed(i: int, label: str, cmd: str, inv: Invocation, wants_help: bool) -> None:
    if wants_help:
        raise _fail(i, label, f"step {i} ({cmd}): --help is not available inside a step",
                    [f"Run `blender-axi {cmd} --help` for the flags"])
    if inv.positionals.get("file"):
        raise _fail(i, label, f"step {i}: <file> belongs on apply, not on a step", ["Run `blender-axi apply --help`"])
    for key in ("in_place", "out", "force"):
        if inv.flags.get(key) not in (None, False):
            flag = "--" + key.replace("_", "-")
            raise _fail(i, label, f"step {i}: {flag} belongs on apply, not on a step", ["Run `blender-axi apply --help`"])
    if inv.flags.get("new"):
        raise _fail(i, label, f"step {i}: --new cannot be used in a step (apply works on an existing file)",
                    ["Create the file first: `blender-axi new <file.blend>`"])
    defaults = dataclasses.asdict(GlobalOpts())
    for key, value in dataclasses.asdict(inv.globals).items():
        if value != defaults[key]:
            flag = "--" + key.replace("_", "-")
            raise _fail(i, label, f"step {i}: {flag} belongs on apply, not on a step", ["Run `blender-axi apply --help`"])


def _prepare_one(i: int, label: str, step) -> _Step:
    if isinstance(step, str):
        try:
            argv = shlex.split(step, posix=True)
        except ValueError as exc:
            raise _fail(i, label, f"step {i}: cannot split the step ({exc})",
                        ["Quote values with spaces; on Windows use forward slashes in paths, or the object form"]) from None
        if argv and argv[0] == "blender-axi":
            argv = argv[1:]
        if not argv:
            raise _fail(i, label, f"step {i}: empty step")
        cmd, rest = argv[0], argv[1:]
        spec, batch = _specs(i, label, cmd)
    elif isinstance(step, dict):
        cmd = step.get("cmd")
        if not isinstance(cmd, str) or not cmd.strip():
            raise _fail(i, label, f'step {i}: an object step needs "cmd"', ['Example: {"cmd": "add", "kind": "cube"}'])
        cmd = cmd.strip()
        spec, batch = _specs(i, label, cmd)
        rest = _object_args(i, label, cmd, step, spec)
    else:
        raise _fail(i, label, f"step {i}: expected a string or an object, got {type(step).__name__}", [GUIDE])
    try:
        inv, wants_help = flagparser.parse(spec, rest)
    except AxiError as err:
        raise _rewrap(i, cmd, label, err) from err
    _check_allowed(i, label, cmd, inv, wants_help)
    try:
        params = batch.params(inv)
    except AxiError as err:
        raise _rewrap(i, cmd, label, err) from err
    return _Step(cmd, rest, batch, params)


# -- the command ----------------------------------------------------------------------------------

def _check_label(inv: Invocation) -> str:
    """The `file:` of a --check: never contacts Blender (so no ping in live mode)."""
    if inv.globals.live:
        if inv.positionals.get("file"):
            raise invalid("--live uses the file open in Blender; drop <file>", ["blender-axi apply <steps> --live"])
        return "(live session)"
    if inv.flags.get("in_place") or inv.flags.get("out"):
        return _mutate.resolve_target(inv, SPEC).label
    label = resolve_blend(inv)[1]
    assert label is not None
    return label


def _check(inv: Invocation, steps: list[_Step]) -> Result:
    data = {"file": _check_label(inv), "steps": len(steps),
            "plan": [f"{i} " + " ".join(quote_name(t) for t in (s.cmd, *s.argv)) for i, s in enumerate(steps, 1)],
            "status": "valid"}
    drop = ("--check", "--force", "--in-place", "--out")
    if inv.globals.live:
        help_ = [f"Run `{_mutate.replay(inv, SPEC, '', drop).rstrip()}` to apply them to the open session"]
    else:
        help_ = [f"Run `{_mutate.replay(inv, SPEC, '--in-place', drop)}` to apply them (a .blend1 backup is kept)",
                 f"Run `{_mutate.replay(inv, SPEC, '--out <copy.blend>', drop)}` to keep the original and write a copy"]
    return Result(data=data, help=help_)


def _explain(err: AxiError, inv: Invocation, label: str) -> AxiError:
    """Help for a step that failed inside Blender (its message starts `step N (cmd): `); other errors pass through."""
    if not err.message.startswith("step "):
        return err
    live = inv.globals.live
    help_: list[str] = []
    if err.code == "NOT_FOUND":
        given = inv.positionals.get("file")
        objects = f"objects {quote_name(given)}" if given and not live else "objects"
        help_.append(f"Run `{Hints(inv).cmd(objects)}` to list object names")
    if live:
        help_ += ["Steps before the failing one were applied to the open session; if Blender pushed an undo step, "
                  "one Ctrl+Z reverts them",
                  f"Fix the failing step in {label}, then apply only the steps after it"]
    else:
        help_ += ["Nothing was written: the file is unchanged (all-or-nothing)", f"Fix the step in {label} and rerun"]
    err.help = help_
    return err


def run(inv: Invocation) -> Result:
    raw_steps, label = _read_steps(inv)
    steps = [_prepare_one(i, label, s) for i, s in enumerate(raw_steps, 1)]
    if inv.flags.get("check"):
        return _check(inv, steps)
    target = _mutate.resolve_target(inv, SPEC)
    request = {"steps": [{"op": s.batch.op, "params": s.params} for s in steps]}
    try:
        outcome = inv.transport.run("batch", request, blend=target.blend, save=target.save,
                                    timeout=inv.globals.timeout or BATCH_TIMEOUT)
    except AxiError as err:
        raise _explain(err, inv, label) from err
    r = outcome.result
    lines = [f"{i} {s.cmd}: {s.batch.line(res['result'])}" for i, (s, res) in enumerate(zip(steps, r["results"]), 1)]
    if not inv.globals.full and len(lines) > RESULT_LINES:
        lines = [*lines[:RESULT_LINES], f"... ({len(lines) - RESULT_LINES} more; --full shows all)"]
    data: dict = {"file": target.label, "steps": len(steps), "applied": r["applied"], "results": lines}
    hints = ["Run `" + _mutate.hint_cmd(inv, target, outcome, "render <file> --engine WORKBENCH --out <path.png>")
             + "` to preview it"]
    return _mutate.finish(inv, SPEC, target, outcome, data, hints)


SPEC = CommandSpec(
    name="apply",
    summary="Run many add/set/delete/material/modifier/configure/import steps on a .blend in one Blender start (or the open session with --live)",
    positionals=(
        Positional("steps", help="JSON file with the steps, or - for stdin"),
        Positional("file", required=False, help="The .blend file (default: the single .blend in the current directory)"),
    ),
    flags=(
        Flag("--check", "bool", help="Validate every step and print the plan; Blender is not started and nothing is written"),
        *_mutate.MUTATE_FLAGS,
    ),
    examples=(
        "blender-axi apply steps.json scene.blend --in-place",
        "blender-axi apply steps.json scene.blend --out scene2.blend",
        "blender-axi apply steps.json scene.blend --check",
        "blender-axi apply - scene.blend --in-place",
        "blender-axi apply steps.json --live",
    ),
    run=run,
)
