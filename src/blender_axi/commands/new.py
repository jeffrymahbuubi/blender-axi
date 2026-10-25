"""`new`: create a fresh .blend (empty, or Blender's default Camera/Cube/Light) so an agent can build from nothing."""

import os

from ..blender_side.op_new import TEMPLATES
from ..runner import SaveSpec
from ..spec import CommandSpec, Flag, Invocation, Positional, Result
from ._common import carry_suffix, display_path, invalid, quote_name, refused, with_warnings


def run(inv: Invocation) -> Result:
    if inv.globals.live:
        raise invalid("new creates a file on disk; live mode works on the open session",
                      ["Drop --live: `blender-axi new <file.blend>`", "To change the open session use `blender-axi exec --live ...`"])
    raw = inv.positionals["file"]
    if os.path.splitext(raw)[1].lower() != ".blend":
        raise invalid(f"expected a .blend file name, got '{raw}'", [f"Example: {SPEC.examples[0]}"])
    template = inv.flags.get("template") or "empty"
    out = os.path.abspath(os.path.expanduser(raw))
    if os.path.exists(out) and not inv.flags.get("force"):
        raise refused(inv, SPEC, out, "Or choose another file name: `blender-axi new <other.blend>`")

    outcome = inv.transport.run("new", {"out": out, "template": template}, blend=None, save=SaveSpec("none"),
                                timeout=inv.globals.timeout)
    res = outcome.result
    shown = quote_name(display_path(res["path"]))
    suffix = carry_suffix(inv)
    data = {
        "created": display_path(res["path"]),
        "template": res["template"],
        "objects": res["objects"],
        "size_kb": round(res["size_bytes"] / 1024, 1),
        "blender": outcome.blender_version,
        "duration_ms": outcome.duration_ms,
    }
    with_warnings(data, outcome.warnings)
    return Result(data=data, help=[
        f"Run `blender-axi objects {shown}{suffix}` to list its objects",
        f"Run `blender-axi exec {shown} --script <build.py> --in-place --allow-exec{suffix}` to build the scene",
        f"Run `blender-axi render {shown} --out <path.png>{suffix}` to render it",
    ])


SPEC = CommandSpec(
    name="new",
    summary="Create a new .blend file (empty, or with Blender's default Camera, Cube and Light)",
    positionals=(Positional("file", help="The .blend file to create"),),
    flags=(
        Flag("--template", "str", default="empty", choices=TEMPLATES,
             help="empty: no objects (default); default: Camera, Cube and Light"),
        Flag("--force", "bool", help="Overwrite an existing file"),
    ),
    examples=("blender-axi new scene.blend", "blender-axi new scene.blend --template default --force"),
    run=run,
)
