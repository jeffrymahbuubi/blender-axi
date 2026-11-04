"""qa_cleanup judge (plain bpy, never imports blender_axi).

    blender --background --factory-startup --python check.py -- <out_dir> <start.blend>

Reads the facts of `start.blend` first, then opens `<out_dir>/clean.blend` and compares. Prints one `CHECK {json}` line.
Checks: clean_exists, no_debug, d05_visible, no_empty_collections, no_unused_materials, no_missing_images, object_count,
objects_preserved, d05_dimensions, report_exists, report_before_after.
"Unused" = users minus the fake user equals 0 (Blender does not save zero-user data, so the start file uses fake users).
"""

import json
import os
import sys
from pathlib import Path

DISTRICT = "District05"
PREFIX = "Debug_"
FILE_SOURCES = ("FILE", "SEQUENCE", "MOVIE")
TOLERANCE = 1e-3


def facts(bpy):
    """Names and measurements of the file that is open now."""
    bpy.context.view_layer.update()
    district = bpy.data.collections.get(DISTRICT)
    dims = {o.name: [round(float(v), 4) for v in o.dimensions] for o in (district.all_objects if district else ())}
    return {"objects": sorted(o.name for o in bpy.data.objects), "d05_dims": dims}


def missing_images(bpy):
    bad = []
    for image in bpy.data.images:
        if image.source in FILE_SOURCES and not image.packed_file:
            if not image.filepath or not os.path.exists(os.path.normpath(bpy.path.abspath(image.filepath))):
                bad.append(image.name)
    return bad


def main():
    import bpy

    argv = sys.argv[sys.argv.index("--") + 1:]
    out_dir = Path(argv[0])
    start = None if len(argv) < 2 or argv[1] == "-" else Path(argv[1])
    clean, report = out_dir / "clean.blend", out_dir / "report.md"
    checks, notes = {}, []

    before = None
    if start is not None and start.is_file():
        bpy.ops.wm.open_mainfile(filepath=str(start))
        before = facts(bpy)
    else:
        notes.append("start.blend not found: object comparisons fail")

    checks["clean_exists"] = clean.is_file() and clean.stat().st_size > 0
    after = None
    if checks["clean_exists"]:
        try:
            bpy.ops.wm.open_mainfile(filepath=str(clean))
            after = facts(bpy)
        except Exception as exc:
            checks["clean_exists"] = False
            notes.append(f"clean.blend does not open: {str(exc)[:160]}")

    if after is not None:
        names = after["objects"]
        debug = [n for n in names if n.startswith(PREFIX)]
        checks["no_debug"] = not debug
        if debug:
            notes.append(f"Debug objects left: {len(debug)}")

        district = bpy.data.collections.get(DISTRICT)
        view_layer = bpy.context.view_layer
        hidden = []
        for o in (district.all_objects if district else ()):
            if o.hide_viewport or o.hide_render or o.hide_get(view_layer=view_layer):
                hidden.append(o.name)
        checks["d05_visible"] = district is not None and not hidden
        if hidden:
            notes.append(f"{len(hidden)} District05 objects still hidden or render-off, e.g. {hidden[0]}")

        empty = [c.name for c in bpy.data.collections if not c.objects and not c.children]
        checks["no_empty_collections"] = not empty
        if empty:
            notes.append(f"empty collections left: {', '.join(empty[:4])}")

        unused = [m.name for m in bpy.data.materials if m.users - (1 if m.use_fake_user else 0) <= 0]
        checks["no_unused_materials"] = not unused
        if unused:
            notes.append(f"unused materials left: {', '.join(unused[:5])}")

        bad_images = missing_images(bpy)
        checks["no_missing_images"] = not bad_images
        if bad_images:
            notes.append(f"images with a missing file left: {', '.join(bad_images)}")

        if before is not None:
            expected = sorted(n for n in before["objects"] if not n.startswith(PREFIX))
            checks["object_count"] = len(names) == len(expected)
            checks["objects_preserved"] = names == expected
            drift = [n for n, d in before["d05_dims"].items()
                     if n in after["d05_dims"] and any(abs(a - b) > TOLERANCE for a, b in zip(d, after["d05_dims"][n]))]
            checks["d05_dimensions"] = not drift and all(n in after["d05_dims"] for n in before["d05_dims"]
                                                         if not n.startswith(PREFIX))
            notes.append(f"objects {len(before['objects'])} -> {len(names)} (expected {len(expected)})")
            if drift:
                notes.append(f"District05 dimensions changed on {len(drift)} objects, e.g. {drift[0]}")
        else:
            checks["object_count"] = checks["objects_preserved"] = checks["d05_dimensions"] = False
    else:
        for key in ("no_debug", "d05_visible", "no_empty_collections", "no_unused_materials", "no_missing_images",
                    "object_count", "objects_preserved", "d05_dimensions"):
            checks[key] = False

    text = report.read_text(encoding="utf-8", errors="replace") if report.is_file() else ""
    checks["report_exists"] = bool(text.strip())
    checks["report_before_after"] = "before" in text.lower() and "after" in text.lower()
    print("CHECK " + json.dumps({"passed": all(checks.values()), "checks": checks, "notes": notes}))


main()
