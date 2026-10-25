"""`material`: give an object a Principled BSDF material in a .blend (or the open session with --live).

Replaces the object's first material slot with the named material (created when it does not exist); `--slot add`
appends instead. Built with the data API (see `op_material`); headless writes need `--in-place` or
`--out <copy.blend>` (`_mutate`). Colours are linear 0..1 values; the colour also sets the viewport colour, which is
what `render --engine WORKBENCH` shows.
"""

from __future__ import annotations

from ..errors import AxiError
from ..spec import BatchSpec, CommandSpec, Flag, Invocation, Positional, Result
from . import _mutate
from ._common import Hints, fmt_num, fmt_vec, invalid, names_summary, quote_name, translate_not_found

SLOTS = ("replace", "add")


def _unit_range(inv: Invocation, flag: str, values: list[float] | None) -> list[float] | None:
    if values is not None and not all(0.0 <= v <= 1.0 for v in values):
        raise invalid(f"{flag} values must be between 0 and 1, got {','.join(fmt_num(v) for v in values)}",
                      [f"Example: {flag} {'1,0.5,0' if flag != '--color' else '1,0,0'}"])
    return values


def _scalar(inv: Invocation, key: str, high: float | None = 1.0) -> float | None:
    value = inv.flags.get(key)
    flag = "--" + key.replace("_", "-")
    if value is not None and (value < 0 or (high is not None and value > high)):
        want = "between 0 and 1" if high is not None else "0 or more"
        raise invalid(f"{flag} must be {want}, got {fmt_num(value)}", [f"Example: {flag} 0.5"])
    return value


def _params(inv: Invocation) -> dict:
    """Pure argument checks (no Blender, no filesystem); returns the op params (shared with `apply`)."""
    f = inv.flags
    name = str(f.get("name") or "").strip()
    if not name:
        raise invalid("--name is empty", ["Example: --name Red"])
    color = _unit_range(inv, "--color", _mutate.parse_vec(SPEC, "--color", f.get("color"), (3, 4)))
    if color is not None and len(color) == 3:
        color.append(1.0)
    emission = _unit_range(inv, "--emission", _mutate.parse_vec(SPEC, "--emission", f.get("emission")))
    return {
        "object": inv.positionals["object"], "name": name, "color": color,
        "metallic": _scalar(inv, "metallic"), "roughness": _scalar(inv, "roughness"),
        "emission": emission, "emission_strength": _scalar(inv, "emission_strength", None),
        "slot": f.get("slot") or "replace",
    }


def _line(r: dict) -> str:
    return f"{r['object']} <- {r['material']} ({'created' if r['created'] else 'reused'})"


def run(inv: Invocation) -> Result:
    params = _params(inv)
    try:
        target, outcome = _mutate.run_mutation(inv, SPEC, "material", params)
    except AxiError as err:
        given = inv.positionals.get("file")
        listing = f"objects {quote_name(given)}" if given and not inv.globals.live else "objects"
        raise translate_not_found(err, [f"Run `{Hints(inv).cmd(listing)}` to list the objects"]) from err
    r = outcome.result
    data: dict = {
        "file": target.label, "object": r["object"], "material": r["material"], "created": r["created"],
        "color": fmt_vec(r["color"]), "metallic": fmt_num(r["metallic"]), "roughness": fmt_num(r["roughness"]),
        "slots": names_summary(r["slots"]),
    }
    hints = ["Run `" + _mutate.hint_cmd(inv, target, outcome, "render <file> --engine WORKBENCH --out <path.png>")
             + "` to preview it"]
    return _mutate.finish(inv, SPEC, target, outcome, data, hints)


SPEC = CommandSpec(
    name="material",
    summary="Give an object a material (Principled BSDF) in a .blend (or the open session with --live)",
    positionals=(
        Positional("object", help="Name of the mesh (or curve, text) object that gets the material"),
        Positional("file", required=False, help="The .blend file (default: the single .blend in the current directory)"),
    ),
    flags=(
        Flag("--name", "str", required=True, help="Material name; an existing material of that name is reused and edited"),
        Flag("--color", "str", help="Base colour r,g,b or r,g,b,a, each 0..1 (linear)"),
        Flag("--metallic", "float", help="Metallic, 0..1"),
        Flag("--roughness", "float", help="Roughness, 0..1"),
        Flag("--emission", "str", help="Emission colour r,g,b, each 0..1 (glows only with --emission-strength above 0)"),
        Flag("--emission-strength", "float", help="Emission strength, 0 or more"),
        Flag("--slot", "str", choices=SLOTS, default="replace",
             help="replace the object's first material slot (default) or add another slot"),
        *_mutate.MUTATE_FLAGS,
    ),
    examples=(
        "blender-axi material Cube scene.blend --name Red --color 1,0,0 --in-place",
        "blender-axi material Cube scene.blend --name Steel --color 0.6,0.6,0.65 --metallic 1 --roughness 0.3 --out scene2.blend",
        "blender-axi material Panel scene.blend --name Glow --emission 1,0.8,0.4 --emission-strength 10 --slot add --in-place",
        "blender-axi material Cube --name Blue --color 0,0,1 --live",
    ),
    run=run,
)

BATCH = BatchSpec("material", _params, _line)
