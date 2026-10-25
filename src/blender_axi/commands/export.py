"""`export`: write a scene, collection or named objects to glb/gltf/fbx/obj/stl/ply/usd/abc (plan U5)."""

import os

from ..blender_side.op_export import FORMAT_EXTS
from ..errors import AxiError
from ..spec import CommandSpec, Flag, Invocation, Positional, Result
from ._common import display_path, invalid, live_suffix, refused, resolve_blend_inferred

_FORMATS = tuple(FORMAT_EXTS)


def _resolve_format(out: str, fmt: str | None) -> str:
    ext = os.path.splitext(out)[1].lower()
    if fmt is None:
        if ext[1:] in FORMAT_EXTS and ext in FORMAT_EXTS[ext[1:]]:
            return ext[1:]
        raise invalid(f"cannot infer the export format from extension '{ext}'" if ext else "--out has no extension",
                       ["Use an extension like .glb, .fbx, .obj, .stl, .ply, .usd, .abc, or pass --format <name>"])
    if ext not in FORMAT_EXTS[fmt]:
        raise invalid(f"--out extension '{ext or '(none)'}' does not match --format {fmt}",
                       [f"Use --out with a {FORMAT_EXTS[fmt][0]} extension, or drop --format"])
    return fmt


def run(inv: Invocation) -> Result:
    flags = inv.flags
    out_raw = flags.get("out")
    if not out_raw:  # parser enforces --out; keep the guard for direct callers
        raise invalid("missing required flag --out", ["Example: " + SPEC.examples[0]])
    out_str = str(out_raw)
    if os.path.splitext(out_str)[1] == "" and flags.get("format"):
        out_str += FORMAT_EXTS[flags["format"]][0]
    out = os.path.abspath(os.path.expanduser(out_str))
    fmt = _resolve_format(out, flags.get("format"))

    collection = flags.get("collection")
    names = flags.get("objects") or None
    if collection and names:
        raise invalid("--collection and --objects are mutually exclusive",
                       ["Use --collection <name> or --objects <a,b>, not both"])

    blend, inferred = resolve_blend_inferred(inv, "export")
    if os.path.exists(out) and not flags.get("force"):
        raise refused(inv, SPEC, out)

    params = {"out": out, "format": fmt, "collection": collection, "objects": names,
              "force": bool(flags.get("force"))}
    try:
        outcome = inv.transport.run("export", params, blend=blend, timeout=inv.globals.timeout)
    except AxiError as err:
        if err.code == "WRITE_REFUSED":
            raise refused(inv, SPEC, err.message.removeprefix("output exists: ")) from err
        if err.code == "NOT_FOUND":
            raise AxiError("NOT_FOUND", err.message, help=[
                "List what is in the file with `blender-axi objects <file>`"]) from err
        raise

    res = outcome.result
    data: dict = {}
    if inferred and blend is not None:
        data["file"] = blend.name
    data["exported"] = display_path(res["path"])
    data["format"] = res["format"]
    data["objects"] = res["objects"]
    data["size_kb"] = round(res["size_bytes"] / 1024, 1)
    data["duration_ms"] = outcome.duration_ms
    if outcome.warnings:
        data["warning"] = "; ".join(outcome.warnings)
    return Result(data=data, help=[f"Run `blender-axi render <file> --out <path.png>{live_suffix(inv)}` to render a preview"])


SPEC = CommandSpec(
    name="export",
    summary="Export a scene, collection or objects to glb, fbx, obj, stl, ply, usd or abc",
    positionals=(Positional("file", required=False, help="The .blend file (default: the single .blend in the cwd)"),),
    flags=(
        Flag("--out", "path", required=True, help="Output file; the extension picks the format"),
        Flag("--format", "str", choices=_FORMATS, help="Export format (default: from the --out extension)"),
        Flag("--collection", "str", help="Export only this collection (and its children)"),
        Flag("--objects", "list", help="Export only these objects, comma-separated"),
        Flag("--force", "bool", help="Overwrite an existing output file"),
    ),
    examples=(
        "blender-axi export scene.blend --out model.glb",
        "blender-axi export scene.blend --collection Props --out props.fbx --force",
    ),
    run=run,
)
