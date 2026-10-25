"""`modifier`: add, configure or remove one modifier on one object in a .blend (or the open session with --live).

An upsert keyed by name (default = Blender's name for the kind, e.g. Bevel): the modifier is created when missing and
only the given settings are written, so rerunning a command changes nothing (`modified: false`, no file written).
Six kinds are typed (bevel, subdivision, array, mirror, boolean, solidify); any other Blender modifier type is reached
by its upper-case identifier plus `--prop key=value`. Built with the data API (see `op_modifier`); headless writes
need `--in-place` or `--out <copy.blend>` (`_mutate`); angles are in degrees.
"""

from __future__ import annotations

import math
import re

from ..blender_side.op_modifier import KINDS
from ..errors import AxiError
from ..spec import BatchSpec, CommandSpec, Flag, Invocation, Positional, Result
from . import _mutate
from ._common import Hints, fmt_num, invalid, names_summary, quote_name, translate_not_found
from .set_cmd import NO_CHANGE

IDENT = re.compile(r"^[A-Z][A-Z0-9_]*$")
PROP_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
LIMITS = ("NONE", "ANGLE", "WEIGHT")
OPERATIONS = ("DIFFERENCE", "UNION", "INTERSECT")
SOLVERS = ("EXACT", "FLOAT", "MANIFOLD")
AXES = ("x", "y", "z", "xy", "xz", "yz", "xyz")
NAMES = {blender: friendly for friendly, blender in KINDS.items()}

# flag key -> the Blender kinds it applies to
APPLIES: dict[str, tuple[str, ...]] = {
    "width": ("BEVEL",), "segments": ("BEVEL",), "limit": ("BEVEL",), "angle": ("BEVEL",),
    "levels": ("SUBSURF",), "render_levels": ("SUBSURF",), "simple": ("SUBSURF",),
    "count": ("ARRAY",), "offset": ("ARRAY", "SOLIDIFY"), "constant": ("ARRAY",),
    "axis": ("MIRROR",), "clip": ("MIRROR",), "object": ("MIRROR", "BOOLEAN"),
    "operation": ("BOOLEAN",), "solver": ("BOOLEAN",), "thickness": ("SOLIDIFY",), "even": ("SOLIDIFY",),
}


def _and(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _flag(key: str) -> str:
    return "--" + key.replace("_", "-")


def _normalize(inv: Invocation) -> None:
    """Accept `<object> <file> <kind>` as well as `<object> <kind> [<file>]` (the other commands put the file second)."""
    pos = inv.positionals
    kind, file = pos.get("kind"), pos.get("file")
    if kind and kind.lower().endswith(".blend"):
        if file:
            pos["kind"], pos["file"] = file, kind
        else:
            raise invalid(f"modifier needs a kind after the object; got only the file '{kind}'",
                          ["Kinds: " + ", ".join(KINDS), f"Example: {SPEC.examples[0]}"])


def _kind(raw: str) -> str:
    if raw.lower() in KINDS:
        return KINDS[raw.lower()]
    if IDENT.match(raw):
        return raw
    raise invalid(f"unknown modifier kind '{raw}'; one of {', '.join(KINDS)}, or a Blender identifier such as WIREFRAME",
                  ["Any other modifier type: give its upper-case identifier and set it with --prop key=value",
                   f"Example: {SPEC.examples[2]}"])


def _props(entries: list[str] | None) -> dict[str, str]:
    """`--prop a=1 --prop color=1,0,0`: the parser splits at commas, so a bare number continues the previous value."""
    props: dict[str, str] = {}
    last: str | None = None
    for entry in entries or []:
        key, eq, value = entry.partition("=")
        if eq and PROP_KEY.match(key.strip()):
            last = key.strip()
            props[last] = value.strip()
            continue
        try:
            float(entry)
            numeric = True
        except ValueError:
            numeric = False
        if last is not None and not eq and numeric:
            props[last] += "," + entry.strip()
            continue
        raise invalid(f"--prop expects key=value, got '{entry}'", ["Example: --prop thickness=0.05"])
    return props


def _check_ranges(inv: Invocation) -> None:
    f = inv.flags
    for key, low, high in (("width", 0.0, None), ("segments", 1, 1000), ("angle", 0, 180), ("levels", 0, 11),
                           ("render_levels", 0, 11), ("count", 1, 1000)):
        value = f.get(key)
        if value is None:
            continue
        if (key == "width" and not value > 0) or (key != "width" and not low <= value <= high):
            want = "greater than 0" if key == "width" else f"{low}-{high}"
            raise invalid(f"{_flag(key)} must be {want}, got {fmt_num(value)}", [f"Example: {_flag(key)} 2"])
    if f.get("thickness") is not None and not math.isfinite(f["thickness"]):
        raise invalid(f"--thickness must be a finite number, got {f['thickness']}", ["Example: --thickness 0.1"])
    if f.get("name") is not None and not str(f["name"]).strip():
        raise invalid("--name is empty", ["Example: --name Rim"])
    if f.get("object") is not None and not str(f["object"]).strip():
        raise invalid("--object is empty", ["Example: --object Cutter"])


def _offset(inv: Invocation, kind: str):
    raw = inv.flags.get("offset")
    if raw is None:
        return None
    if kind == "SOLIDIFY":
        try:
            value = float(raw)
        except ValueError:
            value = math.nan
        if not -1.0 <= value <= 1.0:
            raise invalid(f"--offset for solidify is a number from -1 to 1, got '{raw}'", ["Example: --offset 1"])
        return value
    return _mutate.parse_vec(SPEC, "--offset", raw)


def _settings(inv: Invocation, kind: str) -> dict:
    f = inv.flags
    s: dict = {}
    if kind == "BEVEL":
        s.update({"width": f.get("width"), "segments": f.get("segments"), "limit_method": f.get("limit")})
        if f.get("angle") is not None:
            s["angle_limit"] = math.radians(f["angle"])
    elif kind == "SUBSURF":
        s.update({"levels": f.get("levels"), "render_levels": f.get("render_levels")})
        if f.get("simple"):
            s["subdivision_type"] = "SIMPLE"
    elif kind == "ARRAY":
        s["count"] = f.get("count")
        offset, constant = _offset(inv, kind), _mutate.parse_vec(SPEC, "--constant", f.get("constant"))
        if offset is not None:
            s.update({"relative_offset_displace": offset, "use_relative_offset": True})
        if constant is not None:
            s.update({"constant_offset_displace": constant, "use_constant_offset": True})
            if offset is None:
                s["use_relative_offset"] = False
    elif kind == "MIRROR":
        if f.get("axis"):
            s["use_axis"] = [letter in f["axis"] for letter in "xyz"]
        if f.get("clip"):
            s["use_clip"] = True
        s["mirror_object"] = f.get("object")
    elif kind == "BOOLEAN":
        s.update({"object": f.get("object"), "operation": f.get("operation"), "solver": f.get("solver")})
    elif kind == "SOLIDIFY":
        s.update({"thickness": f.get("thickness"), "offset": _offset(inv, kind)})
        if f.get("even"):
            s["use_even_offset"] = True
    return {key: value for key, value in s.items() if value is not None}


def _params(inv: Invocation) -> dict:
    """Pure argument checks (no Blender, no filesystem); returns the op params."""
    _normalize(inv)
    kind = _kind(str(inv.positionals["kind"]))
    f = inv.flags
    given = [key for key in APPLIES if f.get(key) is not None and f.get(key) is not False]
    if f.get("remove") and (given or f.get("prop")):
        flags = [_flag(key) for key in given] + (["--prop"] if f.get("prop") else [])
        raise invalid(f"--remove cannot be combined with setting flags ({', '.join(flags)})",
                      ["--remove deletes the modifier; drop the setting flags", f"Example: {SPEC.examples[4]}"])
    for key in given:
        if kind not in APPLIES[key]:
            raise invalid(f"{_flag(key)} applies to {_and([NAMES[k] for k in APPLIES[key]])}",
                          [f"Flags for {NAMES.get(kind, kind.lower())}: " + (", ".join(
                              _flag(k) for k, kinds in APPLIES.items() if kind in kinds) or "--prop key=value only")])
    _check_ranges(inv)
    return {
        "object": inv.positionals["object"], "kind": kind, "name": f.get("name"), "remove": bool(f.get("remove")),
        "settings": {} if f.get("remove") else _settings(inv, kind), "props": _props(f.get("prop")),
    }


def _line(r: dict) -> str:
    what = "created" if r.get("created") else "removed" if r.get("removed") else \
        ", ".join(c["field"] for c in r["changed"]) or "no change"
    return f"{r['object']} <- {r['modifier']} ({r['type']}, {what})"


def _show(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ",".join(_show(v) for v in value)
    if isinstance(value, float):
        return f"{value:.4g}"
    return "none" if value is None else str(value)


def _describe(change: dict) -> str:
    return f"{change['field']}: {_show(change['from'])} -> {_show(change['to'])}"


def run(inv: Invocation) -> Result:
    params = _params(inv)
    try:
        target, outcome = _mutate.run_mutation(inv, SPEC, "modifier", params)
    except AxiError as err:
        if err.message.startswith("file not found"):
            raise
        given = inv.positionals.get("file")
        objects = f"objects {quote_name(given)}" if given and not inv.globals.live else "objects"
        raise translate_not_found(err, [f"Run `{Hints(inv).cmd(objects)}` to list object names"]) from err
    r = outcome.result
    data: dict = {"file": target.label, "object": r["object"], "modifier": r["modifier"], "type": r["type"]}
    if params["remove"]:
        data["removed"] = bool(r["removed"])
    else:
        data["created"] = bool(r["created"])
        data["changed"] = [_describe(c) for c in r["changed"]] or NO_CHANGE
    data["stack"] = names_summary(r["stack"])
    check = f"object {quote_name(r['object'])} <file>"
    hints = [f"Run `{_mutate.hint_cmd(inv, target, outcome, check)}` to check the result"]
    return _mutate.finish(inv, SPEC, target, outcome, data, hints)


SPEC = CommandSpec(
    name="modifier",
    summary="Add, configure or remove a modifier (bevel, subdivision, array, mirror, boolean, solidify, or any Blender type via --prop) on one object",
    positionals=(
        Positional("object", help="Object name (exact, case-sensitive)"),
        Positional("kind", help="One of: " + ", ".join(KINDS) + "; or a Blender identifier such as WIREFRAME (set it with --prop)"),
        Positional("file", required=False, help="The .blend file, before or after <kind> (default: the single .blend in the current directory)"),
    ),
    flags=(
        Flag("--name", "str", help="Modifier name (default: Blender's, e.g. Bevel); an existing modifier of that name is edited"),
        Flag("--remove", "bool", help="Delete the modifier (nothing happens when it does not exist)"),
        Flag("--width", "float", help="Bevel width in scene units"),
        Flag("--segments", "int", help="Bevel segments, 1-1000"),
        Flag("--limit", "str", choices=LIMITS, help="Bevel limit method (Blender's default is ANGLE)"),
        Flag("--angle", "float", help="Bevel angle limit in degrees (with --limit ANGLE)"),
        Flag("--levels", "int", help="Subdivision levels in the viewport, 0-11"),
        Flag("--render-levels", "int", help="Subdivision levels at render time, 0-11"),
        Flag("--simple", "bool", help="Simple instead of Catmull-Clark subdivision (back to Catmull-Clark: --prop subdivision_type=CATMULL_CLARK)"),
        Flag("--count", "int", help="Array copies, 1-1000"),
        Flag("--offset", "str", help="Array: relative offset x,y,z (in object sizes); solidify: offset, -1 to 1"),
        Flag("--constant", "str", help="Array: constant offset x,y,z in scene units (turns the relative offset off unless --offset is given)"),
        Flag("--axis", "str", choices=AXES, help="Mirror axes (x, y, z, xy, xz, yz or xyz)"),
        Flag("--clip", "bool", help="Mirror: keep vertices on the mirror plane (clipping)"),
        Flag("--object", "str", help="Mirror: mirror object; boolean: the cutter object (required to create a boolean)"),
        Flag("--operation", "str", choices=OPERATIONS, help="Boolean operation (Blender's default is DIFFERENCE)"),
        Flag("--solver", "str", choices=SOLVERS, help="Boolean solver (Blender's default is EXACT)"),
        Flag("--thickness", "float", help="Solidify thickness in scene units"),
        Flag("--even", "bool", help="Solidify: even thickness"),
        Flag("--prop", "list", help="key=value for any Blender property of the modifier (repeatable; arrays as 1,0,0); the only way to set other modifier types"),
        *_mutate.MUTATE_FLAGS,
    ),
    examples=(
        "blender-axi modifier Cube scene.blend bevel --width 0.05 --segments 3 --in-place",
        "blender-axi modifier Cube scene.blend boolean --object Cutter --operation DIFFERENCE --in-place",
        "blender-axi modifier Cube scene.blend WIREFRAME --prop thickness=0.05 --in-place",
        "blender-axi modifier Cube subdivision --levels 2 --out scene2.blend",
        "blender-axi modifier Cube bevel --remove --live",
    ),
    run=run,
)

BATCH = BatchSpec("modifier", _params, _line)
