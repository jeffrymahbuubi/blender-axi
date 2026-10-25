"""`set`: rename, move, rotate, scale, reparent, re-collect, hide or show one object.

Built with the data API (see `op_set`), so it behaves the same headless, in the headless MCP server and in the GUI.
Headless writes need `--in-place` or `--out <copy.blend>` (`_mutate`); rotation is in degrees. Setting what is
already there changes nothing: `modified: false` and no file is written.
"""

from __future__ import annotations

from ..errors import AxiError
from ..spec import BatchSpec, CommandSpec, Flag, Invocation, Positional, Result
from . import _mutate
from ._common import Hints, fmt_vec, invalid, quote_name, translate_not_found

CHANGE_FLAGS = ("--name", "--location", "--rotation", "--scale", "--parent", "--collection", "--hide", "--show")
NO_CHANGE = "none (values already as requested)"


def _validate(inv: Invocation) -> None:
    """Pure argument checks (no Blender, no filesystem)."""
    f = inv.flags
    if f.get("hide") and f.get("show"):
        raise invalid("--hide and --show are mutually exclusive", ["--hide hides the object, --show shows it"])
    asked = [k for k in ("name", "location", "rotation", "scale", "parent", "collection") if f.get(k) is not None]
    if not (asked or f.get("hide") or f.get("show")):
        raise invalid("set needs at least one change flag",
                      ["Change flags: " + " ".join(CHANGE_FLAGS), f"Example: {SPEC.examples[0]}"])
    for key in ("name", "parent", "collection"):
        if f.get(key) is not None and not str(f[key]).strip():
            raise invalid(f"--{key} is empty", [f"Example: --{key} Name"])


def _describe(change: dict) -> str:
    def show(value) -> str:
        if isinstance(value, list):
            return fmt_vec(value)
        if isinstance(value, bool):
            return "true" if value else "false"
        return "none" if value is None else str(value)

    return f"{change['field']}: {show(change['from'])} -> {show(change['to'])}"


def _params(inv: Invocation) -> dict:
    """The pure checks plus the `set` op params (shared with `apply`)."""
    _validate(inv)
    f = inv.flags
    parent = f.get("parent")
    return {
        "name": inv.positionals["name"], "new_name": f.get("name"),
        "location": _mutate.parse_vec(SPEC, "--location", f.get("location")),
        "rotation_deg": _mutate.parse_vec(SPEC, "--rotation", f.get("rotation")),
        "scale": _mutate.parse_vec(SPEC, "--scale", f.get("scale")),
        "parent": "none" if parent is not None and str(parent).strip().lower() == "none" else parent,
        "collection": f.get("collection"),
        "hide": True if f.get("hide") else False if f.get("show") else None,
    }


def _line(r: dict) -> str:
    return f"{r['name']} ({', '.join(c['field'] for c in r['changed']) or 'no change'})"


def run(inv: Invocation) -> Result:
    params = _params(inv)
    try:
        target, outcome = _mutate.run_mutation(inv, SPEC, "set", params)
    except AxiError as err:
        if err.message.startswith("file not found"):
            raise
        given = inv.positionals.get("file")
        objects = f"objects {quote_name(given)}" if given and not inv.globals.live else "objects"
        raise translate_not_found(err, [f"Run `{Hints(inv).cmd(objects)}` to list object names"]) from err
    r = outcome.result
    changed = [_describe(c) for c in r["changed"]]
    data: dict = {"file": target.label, "object": r["name"], "changed": changed or NO_CHANGE}
    check = f"object {quote_name(r['name'])} <file>"
    hints = [f"Run `{_mutate.hint_cmd(inv, target, outcome, check)}` to check the result"]
    return _mutate.finish(inv, SPEC, target, outcome, data, hints)


SPEC = CommandSpec(
    name="set",
    summary="Rename, move, rotate, scale, reparent, re-collect, hide or show one object in a .blend (or the open session with --live)",
    positionals=(
        Positional("name", help="Object name (exact, case-sensitive)"),
        Positional("file", required=False, help="The .blend file (default: the single .blend in the current directory)"),
    ),
    flags=(
        Flag("--name", "str", help="Rename the object (a clash gets a .001 suffix)"),
        Flag("--location", "str", help="x,y,z in scene units"),
        Flag("--rotation", "str", help="x,y,z Euler rotation in degrees"),
        Flag("--scale", "str", help="x,y,z scale"),
        Flag("--parent", "str", help="Parent object name (it keeps its place in the world), or 'none' to unparent"),
        Flag("--collection", "str", help="Move it to this collection ('Scene Collection' for the root)"),
        Flag("--hide", "bool", help="Hide it in the viewport and in renders"),
        Flag("--show", "bool", help="Show it again"),
        *_mutate.MUTATE_FLAGS,
    ),
    examples=(
        "blender-axi set Cube scene.blend --location 1,2,3 --rotation 0,0,90 --in-place",
        "blender-axi set Cube scene.blend --parent Empty --out scene2.blend",
        "blender-axi set Cube scene.blend --name Crate --collection Props --in-place",
        "blender-axi set Cube --live --location 0,0,1",
    ),
    run=run,
)

BATCH = BatchSpec("set", _params, _line)
