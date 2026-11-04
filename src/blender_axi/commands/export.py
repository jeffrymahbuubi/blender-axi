"""`export`: write a scene, collection or named objects to glb/gltf/fbx/obj/stl/ply/usd/abc (plan U5, 0.5.0 U1)."""

import os

from ..blender_side.op_export import FORMAT_EXTS
from ..errors import AxiError
from ..spec import CommandSpec, Flag, Invocation, Positional, Result
from ._common import (Hints, carry_suffix, display_path, invalid, names_summary, quote_name, refused,
                      resolve_blend_inferred)

_FORMATS = tuple(FORMAT_EXTS)
SET_ROWS = 20  # `export_set` rows of a dry run (--full shows all)


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


def _merged_objects(flags: dict) -> list[str] | None:
    """`--objects a,b` plus every `--object X` (verbatim), order kept, duplicates dropped."""
    merged = list(dict.fromkeys([*(flags.get("objects") or []), *(flags.get("object") or [])]))
    return merged or None


def _skipped_text(hidden: list[str], excluded: list[str]) -> str:
    def group(label: str, names: list[str]) -> str:
        count, _, rest = names_summary(names).partition(" ")
        return f"{count} {label} {rest}"

    parts = [group("hidden", hidden)] if hidden else []
    if excluded:
        parts.append(group("excluded", excluded))
    return ", ".join(parts) if parts else "none"


def _modifiers_text(res: dict) -> str:
    if res["modifiers"] == "evaluated":
        return f"evaluated ({res['format']} exports the evaluated scene)"
    return res["modifiers"]


def _also_text(rows: list[dict]) -> str:
    """`1 empty (Grp)` / `2 empties (Grp, Rig), 1 camera (Cam)`: non-geometry objects the format writes."""
    parts = []
    for kind, singular, plural in (("EMPTY", "empty", "empties"), ("CAMERA", "camera", "cameras")):
        names = [r["name"] for r in rows if r["type"] == kind]
        if names:
            n, _, rest = names_summary(names).partition(" ")
            parts.append(f"{n} {singular if len(names) == 1 else plural} {rest}")
    return ", ".join(parts)


def _replay(inv: Invocation, out_raw: str) -> str:
    """The same export without --dry-run (the hint after a dry run)."""
    f = inv.flags
    parts = ["blender-axi", "export"]
    if inv.positionals.get("file"):
        parts.append(quote_name(inv.positionals["file"]))
    parts += ["--out", quote_name(out_raw)]
    if f.get("format"):
        parts += ["--format", f["format"]]
    if f.get("collection"):
        parts += ["--collection", quote_name(f["collection"])]
    if f.get("objects"):
        parts += ["--objects", quote_name(",".join(f["objects"]))]
    for name in f.get("object") or []:
        parts += ["--object", quote_name(name)]
    if f.get("exclude"):
        parts += ["--exclude", quote_name(",".join(f["exclude"]))]
    if f.get("include_hidden"):
        parts.append("--include-hidden")
    if f.get("cameras"):
        parts.append("--cameras")
    if not f.get("apply_modifiers", True):
        parts.append("--no-apply-modifiers")
    if f.get("force"):
        parts.append("--force")
    return " ".join(parts) + carry_suffix(inv)


def _dry_run_result(inv: Invocation, res: dict, outcome, out_raw: str, file_name: str | None) -> Result:
    geometry = [row["name"] for row in res["export_set"]]
    rows = res["export_set"] if (inv.globals.full or inv.globals.json) else res["export_set"][:SET_ROWS]
    data: dict = {}
    if file_name:
        data["file"] = file_name
    data["format"] = res["format"]
    data["dry_run"] = True
    data["would_export"] = names_summary(geometry)
    data["export_set"] = rows
    if res.get("also"):
        data["also"] = _also_text(res["also"])
    data["skipped"] = _skipped_text(res["skipped_hidden"], res["excluded"])
    if res.get("triangles") is not None:
        data["triangles"] = res["triangles"]
    data["modifiers"] = _modifiers_text(res)
    data["duration_ms"] = outcome.duration_ms
    warnings = ([res["modifiers_note"]] if res.get("modifiers_note") else []) + list(outcome.warnings)
    if warnings:
        data["warning"] = "; ".join(warnings)
    help_ = [f"Run `{_replay(inv, out_raw)}` to write it"]
    if len(rows) < len(geometry):
        help_.append(f"export_set shows {len(rows)} of {len(geometry)} objects; --full shows all")
    if res["skipped_hidden"]:
        help_.append("Hidden objects are skipped; add --include-hidden to export them")
    return Result(data=data, help=help_)


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
    if flags.get("cameras") and fmt not in ("glb", "gltf"):
        raise invalid("--cameras applies to glb and gltf", ["Use --out with a .glb or .gltf extension, or drop --cameras"])

    collection = flags.get("collection")
    names = _merged_objects(flags)
    exclude = flags.get("exclude") or None
    dry_run = bool(flags.get("dry_run"))
    if collection and names:
        raise invalid("--collection and --objects are mutually exclusive",
                       ["Use --collection <name> or --objects <a,b>, not both"])
    if exclude and flags.get("objects"):  # --object (verbatim names) may still be narrowed by --exclude
        raise invalid("--exclude and --objects are mutually exclusive (a keep-list already leaves the rest out)",
                       ["Drop --exclude, or list only the objects to keep in --objects"])

    blend, inferred = resolve_blend_inferred(inv, "export")
    if not dry_run and os.path.exists(out) and not flags.get("force"):
        raise refused(inv, SPEC, out)

    params = {"out": out, "format": fmt, "collection": collection, "objects": names, "exclude": exclude,
              "include_hidden": bool(flags.get("include_hidden")),
              "apply_modifiers": bool(flags.get("apply_modifiers", True)),
              "dry_run": dry_run, "force": bool(flags.get("force")), "cameras": bool(flags.get("cameras"))}
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
    file_name = blend.name if inferred and blend is not None else None
    if dry_run:
        return _dry_run_result(inv, res, outcome, str(out_raw), file_name)

    data: dict = {}
    if file_name:
        data["file"] = file_name
    data["exported"] = display_path(res["path"])
    data["format"] = res["format"]
    data["objects"] = res["objects"]
    if res.get("triangles") is not None:
        data["triangles"] = res["triangles"]
    if res.get("cameras") is not None:
        data["cameras"] = names_summary(res["cameras"])
    data["modifiers"] = _modifiers_text(res)
    data["skipped"] = _skipped_text(res.get("skipped_hidden") or [], res.get("excluded") or [])
    data["size_kb"] = round(res["size_bytes"] / 1024, 1)
    data["duration_ms"] = outcome.duration_ms
    warnings = ([res["modifiers_note"]] if res.get("modifiers_note") else []) + list(outcome.warnings)
    if warnings:
        data["warning"] = "; ".join(warnings)
    help_ = [f"Run `{Hints(inv).cmd('render <file> --out <path.png>')}` to render a preview"]
    if res.get("skipped_hidden"):
        help_.append("Hidden objects are skipped; add --include-hidden to export them")
    return Result(data=data, help=help_)


SPEC = CommandSpec(
    name="export",
    summary="Export a scene, collection or objects to glb, fbx, obj, stl, ply, usd or abc",
    positionals=(Positional("file", required=False, help="The .blend file (default: the single .blend in the cwd)"),),
    flags=(
        Flag("--out", "path", required=True, help="Output file; the extension picks the format"),
        Flag("--format", "str", choices=_FORMATS, help="Export format (default: from the --out extension)"),
        Flag("--collection", "str", help="Export only this collection (and its children)"),
        Flag("--objects", "list", help="Export only these objects, comma-separated"),
        Flag("--object", "list", split=False,
             help="One object name, repeatable (use for names that contain a comma)"),
        Flag("--exclude", "list", help="Leave these objects out, comma-separated"),
        Flag("--include-hidden", "bool", help="Also export objects hidden in the viewport (visible: false in `objects`)"),
        Flag("--apply-modifiers", "bool", default=True,
             help="Export the evaluated meshes (default); --no-apply-modifiers exports the raw meshes "
                  "(glb, gltf, fbx, obj, stl, ply)"),
        Flag("--cameras", "bool", help="Also write the cameras (glb, gltf): one glTF camera node per camera in the export set"),
        Flag("--dry-run", "bool", help="Print the export set and write nothing"),
        Flag("--force", "bool", help="Overwrite an existing output file"),
    ),
    examples=(
        "blender-axi export scene.blend --out model.glb",
        "blender-axi export scene.blend --collection Props --out props.fbx --force",
    ),
    run=run,
)
