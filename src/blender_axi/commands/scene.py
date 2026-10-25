"""`scene`: one-call overview of a .blend file (plan U4). Helpers shared with `objects` live in `_common`."""

from __future__ import annotations

from ..spec import CommandSpec, Invocation, Positional, Result
from ._common import Hints, names_summary, posix_path, resolve_blend, with_warnings

# -- scene ----------------------------------------------------------------------------------------

_ENGINES = {"BLENDER_EEVEE": "EEVEE", "BLENDER_EEVEE_NEXT": "EEVEE", "BLENDER_WORKBENCH": "WORKBENCH", "CYCLES": "CYCLES"}


def _fps(r: dict) -> int | float:
    base = r.get("fps_base") or 1
    value = r["fps"] / base
    return int(value) if abs(value - round(value)) < 1e-6 else round(value, 2)


def run(inv: Invocation) -> Result:
    blend, label = resolve_blend(inv)
    out = inv.transport.run("scene_summary", {}, blend=blend, timeout=inv.globals.timeout)
    r = out.result
    hints = Hints(inv)
    types = sorted(r["types"].items(), key=lambda kv: (-kv[1], kv[0]))
    count = r["object_count"]
    objects = f"{count} ({', '.join(f'{t} {n}' for t, n in types)})" if count else "0"
    data: dict = {
        "file": label if label is not None else (posix_path(r.get("filepath") or "") or "(unsaved)"),
        "blender": r.get("blender_version") or out.blender_version,
        "scene": r["scene"],
        "engine": _ENGINES.get(r["engine"], r["engine"]),
        "resolution": f"{r['res_x']}x{r['res_y']}@{r['percent']}%",
        "frames": f"{r['frame_start']}-{r['frame_end']} (current {r['frame_current']})",
        "fps": _fps(r),
        "camera": r["camera"] or "none",
        "objects": objects,
        "collections": names_summary(["Scene Collection", *r["collections"]]),
        "materials": names_summary(r["materials"]),
    }
    if inv.globals.live:
        data["unsaved_changes"] = bool(r.get("is_dirty"))
    with_warnings(data, out.warnings)
    help_ = [f"Run `{hints.cmd('objects <file>')}` to list objects",
             f"Run `{hints.cmd('render <file> --out <path.png>')}` to render a still",
             f"Run `{hints.cmd('configure <file> --engine CYCLES --resolution 1920x1080 --in-place')}` to change these settings",
             f"Run `{hints.cmd('export <file> --out <path.glb>')}` to export the scene"]
    return Result(data=data, help=help_)


SPEC = CommandSpec(
    name="scene",
    summary="Overview of a .blend file: engine, resolution, frames, camera, object and collection counts",
    positionals=(Positional("file", False, "The .blend file (optional when the current directory has exactly one)"),),
    flags=(),
    examples=("blender-axi scene scene.blend", "blender-axi scene", "blender-axi scene --live"),
    run=run,
)
