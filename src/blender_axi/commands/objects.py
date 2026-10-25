"""`objects` (filtered list) and `object` (one object's detail) commands (plan U4)."""

from __future__ import annotations

import json

from ..errors import AxiError
from ..spec import DEFAULT_LIMIT, CommandSpec, Flag, Invocation, Positional, Result
from ..text import truncate
from ._common import Hints, fmt_vec, posix_path, resolve_blend, translate_not_found, with_warnings

OBJECT_TYPES = (
    "MESH", "CURVE", "SURFACE", "META", "FONT", "CURVES", "POINTCLOUD", "VOLUME", "GREASEPENCIL", "GPENCIL",
    "ARMATURE", "LATTICE", "EMPTY", "LIGHT", "LIGHT_PROBE", "CAMERA", "SPEAKER",
)
FIELDS = ("location", "dimensions", "parent", "verts", "faces", "materials", "modifiers", "data")


def _coll_label(names: list[str]) -> str:
    return "|".join(names) if names else "none"


# -- objects --------------------------------------------------------------------------------------

def _filters(flags) -> list[str]:
    out = []
    if flags.get("type"):
        out.append(f"type={flags['type']}")
    if flags.get("collection"):
        out.append(f"collection={flags['collection']}")
    if flags.get("name_contains"):
        out.append(f"name_contains={flags['name_contains']}")
    return out


def _validated(inv: Invocation) -> tuple[int, list[str]]:
    limit = inv.flags.get("limit")
    limit = DEFAULT_LIMIT if limit is None else limit
    if limit < 1:
        raise AxiError("VALIDATION_ERROR", f"--limit must be at least 1, got {limit}",
                       help=["blender-axi objects --help"], exit_code=2)
    fields: list[str] = []
    for f in inv.flags.get("fields") or []:
        f = str(f).lower()
        if f not in FIELDS:
            raise AxiError("VALIDATION_ERROR", f"--fields: unknown field '{f}'",
                           help=["Valid fields: " + ", ".join(FIELDS), "blender-axi objects --help"], exit_code=2)
        if f not in fields:
            fields.append(f)
    return limit, fields


def _format_field(name: str, value):
    if name in ("location", "dimensions"):
        return fmt_vec(value)
    return value


def run_objects(inv: Invocation) -> Result:
    limit, fields = _validated(inv)
    blend, label = resolve_blend(inv)
    hints = Hints(inv)
    params = {"type": inv.flags.get("type"), "collection": inv.flags.get("collection"),
              "name_contains": inv.flags.get("name_contains"), "limit": limit, "fields": fields}
    try:
        out = inv.transport.run("objects_list", params, blend=blend, timeout=inv.globals.timeout)
    except AxiError as err:
        raise translate_not_found(err, [f"Run `{hints.cmd('scene <file>')}` to see the collections"]) from None
    r = out.result
    total, matched, rows = r["total"], r["matched"], r["rows"]
    shown = len(rows)
    filters = _filters(inv.flags)
    where = label if label is not None else (posix_path(r.get("filepath") or "") or "the live scene")

    data: dict = {"file": label if label is not None else (posix_path(r.get("filepath") or "") or "(unsaved)")}
    data["count"] = (f"{shown} of {matched} matching ({total} total)" if filters else f"{shown} of {total} total")
    help_: list[str] = []
    if not rows:
        data["objects"] = (f"0 objects matching {', '.join(filters)} in {where}" if filters
                           else f"0 objects in {where}")
        if filters and total:
            help_.append(f"Run `{hints.cmd('objects <file>')}` to list all {total} object{'s' if total != 1 else ''}")
        else:
            help_.append(f"Run `{hints.cmd('scene <file>')}` for the scene overview")
    else:
        table = []
        for row in rows:
            item = {"name": row["name"], "type": row["type"], "collection": _coll_label(row["collections"]),
                    "visible": row["visible"]}
            for f in fields:
                item[f] = _format_field(f, row.get(f))
            table.append(item)
        data["objects"] = table
        help_.append(f"Run `{hints.cmd('object <name> <file>')}` for one object's details")
        if not fields:
            help_.append(f"Run `{hints.cmd('objects <file> --fields location,dimensions')}` for more columns")
        if shown < matched:
            help_.append(f"Run `{hints.cmd('objects <file> --limit <n>')}` to see more of the {matched}")
    with_warnings(data, out.warnings)
    return Result(data=data, help=help_)


OBJECTS_SPEC = CommandSpec(
    name="objects",
    summary="List objects in a .blend file (name, type, collection, visible) with optional filters and columns",
    positionals=(Positional("file", False, "The .blend file (optional when the current directory has exactly one)"),),
    flags=(
        Flag("--type", "str", choices=OBJECT_TYPES, help="Only objects of this type"),
        Flag("--collection", "str", help="Only objects in this collection (including nested ones)"),
        Flag("--name-contains", "str", help="Only objects whose name contains this text (case-insensitive)"),
        Flag("--limit", "int", default=DEFAULT_LIMIT, help="Maximum rows to show"),
        Flag("--fields", "list", choices=FIELDS, help="Extra columns: " + ", ".join(FIELDS)),
    ),
    examples=(
        "blender-axi objects scene.blend",
        "blender-axi objects scene.blend --type MESH --fields verts,faces",
        "blender-axi objects scene.blend --collection Props --name-contains ball",
    ),
    run=run_objects,
)


# -- object ---------------------------------------------------------------------------------------

def _props_text(props: dict, full: bool) -> str:
    if not props:
        return "none"
    text = ", ".join(f"{k}={json.dumps(v, default=str, ensure_ascii=False)}" for k, v in props.items())
    shown, total, cut = truncate(text, full=full)
    return f"{shown}... (truncated, {total} chars total; use --full)" if cut else shown


def run_object(inv: Invocation) -> Result:
    blend, label = resolve_blend(inv)
    hints = Hints(inv)
    name = inv.positionals["name"]
    try:
        out = inv.transport.run("object_detail", {"name": name}, blend=blend, timeout=inv.globals.timeout)
    except AxiError as err:
        raise translate_not_found(err, [f"Run `{hints.cmd('objects <file>')}` to list object names"]) from None
    r = out.result
    data: dict = {
        "name": r["name"],
        "type": r["type"],
        "collection": _coll_label(r["collections"]),
        "parent": r["parent"] or "none",
        "children": r["children"] if r["children"] else "none",
        "location": fmt_vec(r["location"]),
        "rotation_deg": fmt_vec(r["rotation_deg"]),
        "scale": fmt_vec(r["scale"]),
        "dimensions": fmt_vec(r["dimensions"]),
        "visible": r["visible"],
    }
    if r.get("verts") is not None:
        data["verts"] = r["verts"]
        data["faces"] = r["faces"]
    data["materials"] = r["materials"] if r["materials"] else "none"
    data["modifiers"] = r["modifiers"] if r["modifiers"] else "none"
    data["custom_props"] = _props_text(r["custom_props"], inv.globals.full)
    with_warnings(data, out.warnings)
    return Result(data=data, help=[])


OBJECT_SPEC = CommandSpec(
    name="object",
    summary="Details of one object: transform, dimensions, parent/children, materials, modifiers, custom props",
    positionals=(
        Positional("name", True, "Object name (exact, case-sensitive)"),
        Positional("file", False, "The .blend file (optional when the current directory has exactly one)"),
    ),
    flags=(),
    examples=("blender-axi object Cube scene.blend", "blender-axi object Cube", "blender-axi object Cube --live"),
    run=run_object,
)

SPEC = OBJECTS_SPEC
