"""`ping`: report the Blender version, its Python, the loaded file, where Blender keeps its temp files,
whether it started with factory or user preferences, and which add-ons are enabled."""


def run(params: dict) -> dict:
    import os
    import platform
    import sys
    from pathlib import Path

    import bpy

    session_tmp = bpy.app.tempdir  # <TEMP>/blender_<pid>/ — Blender's per-session temp dir
    prefs = getattr(bpy.context, "preferences", None)  # absent only under test doubles
    addons = getattr(prefs, "addons", None) or []
    return {
        "blender_version": bpy.app.version_string,
        "python": platform.python_version(),
        "file": bpy.data.filepath,
        "tempdir": str(Path(session_tmp).parent) if session_tmp else "",
        "env_temp": os.environ.get("TEMP", ""),
        "prefs": "factory" if "--factory-startup" in sys.argv else "user",
        "addons": sorted(a.module for a in addons),
    }
