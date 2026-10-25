"""Session helpers for live mode: the "modified by blender-axi" marker and the best-effort undo push (plan K2/K3).

`bpy.data.is_dirty` ignores direct `bpy.data` edits (verified on Blender 5.2), so a live op that changed the
session records a marker on the window manager. ID properties survive across requests and do not dirty the file.
The marker remembers the file path and its mtime; any save (by Blender or by us) changes the mtime and expires it.
An unsaved session (`filepath == ""`) has nothing to compare, so the marker stays valid until cleared.

Stdlib only; `bpy` is passed in so unit tests can use a stub.
"""

import os

MARKER = "blender_axi_modified"


def _mtime(path: str) -> str:
    """mtime_ns of `path` as a string (ID properties are 32-bit ints), "-1" when there is no file."""
    try:
        return str(os.stat(path).st_mtime_ns)
    except OSError:
        return "-1"


def _wm(bpy):
    return getattr(bpy.context, "window_manager", None)


def mark_modified(bpy) -> None:
    wm = _wm(bpy)
    if wm is None:
        return
    path = bpy.data.filepath
    try:
        wm[MARKER] = {"file": path, "mtime_ns": _mtime(path) if path else "-1"}
    except Exception:  # the marker is a convenience; never fail the op over it
        pass


def clear_marker(bpy) -> None:
    wm = _wm(bpy)
    if wm is None:
        return
    try:
        if MARKER in wm:
            del wm[MARKER]
    except Exception:
        pass


def unsaved_changes(bpy) -> bool:
    """True when Blender says the file is dirty or a still-valid blender-axi marker is set."""
    if bool(bpy.data.is_dirty):
        return True
    wm = _wm(bpy)
    if wm is None:
        return False
    try:
        marker = wm.get(MARKER)
        if marker is None:
            return False
        path = bpy.data.filepath
        if marker.get("file") != path:
            return False
        return path == "" or marker.get("mtime_ns") == _mtime(path)
    except Exception:
        return False


def undo_push(bpy, message: str) -> bool:
    """One undo step so Ctrl+Z reverts the exec. False when the context cannot (headless MCP server)."""
    try:
        bpy.ops.ed.undo_push(message=message)
    except Exception:  # RuntimeError "poll() failed, context is incorrect" in the headless server
        return False
    return True
