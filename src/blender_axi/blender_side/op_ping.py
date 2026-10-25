"""`ping`: report the Blender version, its Python, the loaded file and where Blender keeps its temp files."""


def run(params: dict) -> dict:
    import os
    import platform
    from pathlib import Path

    import bpy

    session_tmp = bpy.app.tempdir  # <TEMP>/blender_<pid>/ — Blender's per-session temp dir
    return {
        "blender_version": bpy.app.version_string,
        "python": platform.python_version(),
        "file": bpy.data.filepath,
        "tempdir": str(Path(session_tmp).parent) if session_tmp else "",
        "env_temp": os.environ.get("TEMP", ""),
    }
