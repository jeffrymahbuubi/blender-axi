"""`new`: create a fresh .blend on disk from the empty or the default startup template.

params: out (absolute path), template ("empty" | "default"). The CLI refuses to overwrite before it gets here.
"""

import os

from .ops import OpError

TEMPLATES = ("empty", "default")


def run(params: dict) -> dict:
    import bpy

    out = params["out"]
    template = params.get("template", "empty")
    if template not in TEMPLATES:
        raise OpError("OP_FAILED", f"unknown template {template!r}")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=template == "empty")
    bpy.ops.wm.save_as_mainfile(filepath=out)
    if not os.path.isfile(out):
        raise OpError("OP_FAILED", f"Blender did not write {out}")
    return {"path": out, "template": template, "objects": len(bpy.data.objects),
            "size_bytes": os.path.getsize(out)}
