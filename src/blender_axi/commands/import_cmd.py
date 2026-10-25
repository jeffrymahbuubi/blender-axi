"""`import`: bring a glb/gltf/fbx/obj/stl/ply/usd/abc file into a .blend (or a new one, or the open session with --live).

The module is `import_cmd` because `import` is a Python keyword. Headless writes need `--in-place` or `--out <copy.blend>`
(`_mutate`); `--new --out <file.blend>` imports into an empty file instead. `--collection` is created when missing
(unlike `add --collection`, where a missing collection is a typo).
"""

from __future__ import annotations

import os
from pathlib import Path

from ..blender_side.op_import import FORMAT_EXTS
from ..errors import AxiError
from ..runner import SaveSpec
from ..spec import BatchSpec, CommandSpec, Flag, Invocation, Positional, Result
from . import _mutate
from ._common import display_path, invalid, names_summary

_FORMATS = tuple(FORMAT_EXTS)


def _resolve_format(source: str, fmt: str | None) -> str:
    ext = os.path.splitext(source)[1].lower()
    if fmt is None:
        for name in _FORMATS:  # exact names first (".usdc" -> usdc), then the family (".usdz" -> usd)
            if ext == f".{name}":
                return name
        for name, exts in FORMAT_EXTS.items():
            if ext in exts:
                return name
        raise invalid(f"cannot infer the import format from extension '{ext}'" if ext else "the source has no extension",
                      ["Use a file ending in .glb, .gltf, .fbx, .obj, .stl, .ply, .usd, .abc, or pass --format <name>"])
    if ext not in FORMAT_EXTS[fmt]:
        raise invalid(f"source extension '{ext or '(none)'}' does not match --format {fmt}",
                      [f"Use a {FORMAT_EXTS[fmt][0]} file, or drop --format"])
    return fmt


def _source(inv: Invocation) -> tuple[str, str]:
    """Pure checks plus a read-only existence test: (absolute source path, format)."""
    raw = str(inv.positionals["source"])
    fmt = _resolve_format(raw, inv.flags.get("format"))
    if inv.flags.get("collection") is not None and not str(inv.flags["collection"]).strip():
        raise invalid("--collection is empty", ["Example: --collection Props"])
    path = Path(os.path.abspath(os.path.expanduser(raw)))
    if not path.is_file():
        raise AxiError("NOT_FOUND", f"source not found: {raw}",
                       help=["Check the path; the importer reads one file (glb, gltf, fbx, obj, stl, ply, usd, abc)"])
    return str(path), fmt


def _params(inv: Invocation) -> dict:
    if inv.flags.get("new"):
        raise invalid("--new cannot be used inside a batch step", ["Create the file first with `blender-axi new`"])
    source, fmt = _source(inv)
    return {"source": source, "format": fmt, "collection": inv.flags.get("collection"), "fresh": False}


def _new_target(inv: Invocation) -> _mutate.Target:
    """`--new --out <file.blend>`: nothing to read, one copy to write; every failure is raised before Blender starts."""
    if inv.globals.live:
        raise invalid("--new creates a file on disk; live mode works on the open session",
                      ["Drop --new to import into the open session: `blender-axi import <source> --live`"])
    if inv.positionals.get("file"):
        raise invalid("--new starts from an empty file; drop <file>",
                      [f"Run `blender-axi import {inv.positionals['source']} --new --out <file.blend>`"])
    if inv.flags.get("in_place"):
        raise invalid("--new has no file to change in place; use --out <file.blend>", ["Example: " + SPEC.examples[1]])
    out = inv.flags.get("out")
    if out is None:
        raise invalid("--new needs --out <file.blend> (the file to create)", ["Example: " + SPEC.examples[1]])
    if not str(out).lower().endswith(".blend"):
        raise invalid(f"--out must be a .blend path, got {out}", ["Example: --out model.blend"])
    target = Path(str(out)).expanduser().resolve()
    if target.exists() and not inv.flags.get("force"):
        raise AxiError("WRITE_REFUSED", f"output exists: {display_path(str(target))}", help=[
            f"Run `{_mutate.replay(inv, SPEC, '--force', ())}` to overwrite", "Or choose another path with --out <path>"])
    return _mutate.Target(None, "(new)", SaveSpec("copy", str(target)), None)


def _types_text(types: dict) -> str:
    if not types:
        return "none"
    return ", ".join(f"{name} {n}" for name, n in sorted(types.items(), key=lambda kv: (-kv[1], kv[0])))


def run(inv: Invocation) -> Result:
    source, fmt = _source(inv)
    fresh = bool(inv.flags.get("new"))
    params = {"source": source, "format": fmt, "collection": inv.flags.get("collection"), "fresh": fresh}
    if fresh:
        target = _new_target(inv)
        outcome = inv.transport.run("import", params, blend=None, save=target.save, timeout=inv.globals.timeout)
    else:
        target, outcome = _mutate.run_mutation(inv, SPEC, "import", params)

    r = outcome.result
    data: dict = {
        "file": target.label, "imported": display_path(r["source"]), "format": r["format"],
        "added": names_summary(r["added"]), "types": _types_text(r["types"]), "materials": r["materials_added"],
    }
    if r.get("collection"):
        data["collection"] = r["collection"] + (" (created)" if r.get("collection_created") else "")
    if r.get("restore_warnings"):
        outcome.warnings = [*outcome.warnings, *r["restore_warnings"]]
    hints = ["Run `" + _mutate.hint_cmd(inv, target, outcome, "render <file> --engine WORKBENCH --out <path.png>")
             + "` to render a preview"]
    return _mutate.finish(inv, SPEC, target, outcome, data, hints)


def _line(r: dict) -> str:
    n = len(r["added"])
    return f"{n} object{'s' if n != 1 else ''} from {r['source_name']}"


BATCH = BatchSpec("import", _params, _line)

SPEC = CommandSpec(
    name="import",
    summary="Import glb, gltf, fbx, obj, stl, ply, usd or abc into a .blend (or a new one with --new, or the open session with --live)",
    positionals=(
        Positional("source", help="The file to import (glb, gltf, fbx, obj, stl, ply, usd, usdc, usda, usdz, abc)"),
        Positional("file", required=False, help="The .blend file (default: the single .blend in the current directory)"),
    ),
    flags=(
        Flag("--collection", "str", help="Put the imported objects in this collection (created when missing)"),
        Flag("--format", "str", choices=_FORMATS, help="Import format (default: from the source extension)"),
        Flag("--new", "bool", help="Import into a new empty file; needs --out <file.blend> and no <file>"),
        *_mutate.MUTATE_FLAGS,
    ),
    examples=(
        "blender-axi import model.glb scene.blend --collection Props --in-place",
        "blender-axi import model.fbx --new --out model.blend",
        "blender-axi import model.obj --live",
    ),
    run=run,
)
