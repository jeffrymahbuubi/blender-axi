"""`add`: put a primitive, camera, light or empty into a .blend (or the open session with --live).

Built with the data API (see `op_add`), so it behaves the same headless, in the headless MCP server and in the GUI.
Headless writes need `--in-place` or `--out <copy.blend>` (`_mutate`); rotation is in degrees.
"""

from __future__ import annotations

from ..blender_side.op_add import KINDS, LIGHT_TYPES
from ..errors import AxiError
from ..spec import CommandSpec, Flag, Invocation, Positional, Result
from . import _mutate
from ._common import Hints, fmt_vec, invalid, quote_name, translate_not_found

# flag key -> the kinds it applies to
APPLIES: dict[str, tuple[str, ...]] = {
    "size": ("cube", "plane"),
    "radius": ("sphere", "icosphere", "cylinder", "cone"),
    "segments": ("sphere", "icosphere", "cylinder", "cone"),
    "depth": ("cylinder", "cone"),
    "type": ("light",),
    "energy": ("light",),
}
COMMON = ("--name", "--location", "--rotation", "--scale", "--collection")


def _and(names: tuple[str, ...]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _positive(inv: Invocation, key: str) -> float | None:
    value = inv.flags.get(key)
    if value is not None and not value > 0:
        raise invalid(f"--{key} must be greater than 0, got {value:g}", [f"Example: --{key} 1"])
    return value


def _validate(inv: Invocation) -> str:
    """Pure argument checks (no Blender, no filesystem); returns the kind."""
    kind = str(inv.positionals["kind"]).lower()
    if kind not in KINDS:
        raise invalid(f"unknown kind '{inv.positionals['kind']}'; one of {', '.join(KINDS)}",
                      [f"Example: {SPEC.examples[0]}"])
    f = inv.flags
    for key, kinds in APPLIES.items():
        if f.get(key) is not None and kind not in kinds:
            allowed = [f"--{k}" for k, ks in APPLIES.items() if kind in ks]
            raise invalid(f"--{key} applies to {_and(kinds)}",
                          [f"Flags for {kind}: " + ", ".join([*allowed, *COMMON])])
    if f.get("name") is not None and not str(f["name"]).strip():
        raise invalid("--name is empty", ["Example: --name Box"])
    for key in ("size", "radius", "depth"):
        _positive(inv, key)
    if f.get("energy") is not None and f["energy"] < 0:
        raise invalid(f"--energy must be 0 or more, got {f['energy']:g}", ["Example: --energy 3"])
    if f.get("segments") is not None:
        low, high = (1, 6) if kind == "icosphere" else (3, 512)
        if not low <= f["segments"] <= high:
            raise invalid(f"--segments must be {low}-{high} for {kind}, got {f['segments']}",
                          ["--segments is the subdivision level for an icosphere"] if kind == "icosphere" else [])
    return kind


def run(inv: Invocation) -> Result:
    kind = _validate(inv)
    f = inv.flags
    params = {
        "kind": kind, "name": f.get("name"),
        "location": _mutate.parse_vec(SPEC, "--location", f.get("location")),
        "rotation_deg": _mutate.parse_vec(SPEC, "--rotation", f.get("rotation")),
        "scale": _mutate.parse_vec(SPEC, "--scale", f.get("scale")),
        "size": f.get("size"), "radius": f.get("radius"), "depth": f.get("depth"), "segments": f.get("segments"),
        "collection": f.get("collection"), "light_type": f.get("type"), "energy": f.get("energy"),
    }
    try:
        target, outcome = _mutate.run_mutation(inv, SPEC, "add", params)
    except AxiError as err:
        if err.message.startswith("file not found"):
            raise
        given = inv.positionals.get("file")
        scene = f"scene {quote_name(given)}" if given and not inv.globals.live else "scene"
        raise translate_not_found(err, [f"Run `{Hints(inv).cmd(scene)}` to see the collections"]) from err
    r = outcome.result
    data: dict = {
        "file": target.label, "added": r["name"], "type": r["type"], "collection": r["collection"],
        "location": fmt_vec(r["location"]), "dimensions": fmt_vec(r["dimensions"]),
    }
    if r.get("verts") is not None:
        data["verts"], data["faces"] = r["verts"], r["faces"]
    name = quote_name(r["name"])
    write = "" if inv.globals.live else " --in-place"
    hints = [f"Run `{_mutate.hint_cmd(inv, target, outcome, f'set {name} <file> --location x,y,z{write}')}` to move it"]
    if r["type"] == "MESH":
        hints.append("Run `" + _mutate.hint_cmd(inv, target, outcome,
                                                 f"material {name} <file> --name <mat> --color r,g,b{write}")
                     + "` to give it a material")
    return _mutate.finish(inv, SPEC, target, outcome, data, hints)


SPEC = CommandSpec(
    name="add",
    summary="Add a primitive, camera, light or empty to a .blend (or the open session with --live)",
    positionals=(
        Positional("kind", help="One of: " + ", ".join(KINDS)),
        Positional("file", required=False, help="The .blend file (default: the single .blend in the current directory)"),
    ),
    flags=(
        Flag("--name", "str", help="Object name (default: Blender's, e.g. Cube; a clash gets a .001 suffix)"),
        Flag("--location", "str", help="x,y,z in scene units (default 0,0,0)"),
        Flag("--rotation", "str", help="x,y,z Euler rotation in degrees (default 0,0,0)"),
        Flag("--scale", "str", help="x,y,z scale (default 1,1,1)"),
        Flag("--size", "float", help="Edge length of a cube or plane (default 2)"),
        Flag("--radius", "float", help="Radius of a sphere, icosphere, cylinder or cone (default 1)"),
        Flag("--depth", "float", help="Height of a cylinder or cone (default 2)"),
        Flag("--segments", "int", help="Segments of a sphere, cylinder or cone (default 32, min 3); subdivisions of an icosphere (default 2, 1-6)"),
        Flag("--collection", "str", help="Collection to link it to (default: the active collection)"),
        Flag("--type", "str", choices=LIGHT_TYPES, help="Light type (light only; default POINT)"),
        Flag("--energy", "float", help="Light power in watts (light only)"),
        *_mutate.MUTATE_FLAGS,
    ),
    examples=(
        "blender-axi add cube scene.blend --name Box --location 0,0,1 --in-place",
        "blender-axi add cylinder scene.blend --radius 0.5 --depth 3 --out scene2.blend",
        "blender-axi add light scene.blend --type SUN --energy 3 --in-place",
        "blender-axi add cube --live",
    ),
    run=run,
)
