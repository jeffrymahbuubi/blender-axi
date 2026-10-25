"""Runs inside Blender: `blender ... --python bootstrap.py -- --request <request.json>` (plan section 3.7).

Reads the request, runs the op, applies the save spec and writes the response file.
Errors are data: this always returns 0 so the CLI can read `response.json`.
Imports only stdlib at module level; `bpy` is imported inside `main`.

Change detection: `bpy.data.is_dirty` is only set by operators/UI edits, not by direct `bpy.data`
edits such as `bpy.data.objects.remove(...)` (verified on Blender 5.2). When a save is requested we
therefore also compare a fingerprint of `bpy.data` taken before and after the op.

Moving the current frame alone is not a modification (plan K4): the fingerprint is compared at the frame the
op started on, and a moved frame is reported separately (`frame_changed: "1 -> 10"`).
"""

import contextlib
import hashlib
import io
import json
import os
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

_PRIMITIVE = {"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"}
_SKIP_PROPS = {"rna_type", "session_uid", "users", "is_evaluated", "original", "tag", "is_runtime_data"}
_SKIP_COLLECTIONS = {"window_managers", "screens", "workspaces", "all_ids"}  # all_ids duplicates the rest


def _walk(bpy, struct, depth: int, prefix: str, feed) -> None:
    for prop in struct.bl_rna.properties:
        pid = prop.identifier
        if pid in _SKIP_PROPS:
            continue
        try:
            value = getattr(struct, pid)
        except Exception:
            continue
        kind = prop.type
        if kind in _PRIMITIVE:
            feed(f"{prefix}.{pid}={value!r}")
        elif kind == "POINTER":
            if value is None:
                feed(f"{prefix}.{pid}=None")
            elif isinstance(value, bpy.types.ID):
                feed(f"{prefix}.{pid}->{value.name}")
            elif depth > 0:
                _walk(bpy, value, depth - 1, f"{prefix}.{pid}", feed)
        elif kind == "COLLECTION":
            try:
                items = list(value)
            except Exception:
                continue
            feed(f"{prefix}.{pid}#{len(items)}")
            if items and isinstance(items[0], bpy.types.ID):
                feed(f"{prefix}.{pid}:" + ",".join(i.name for i in items))


def fingerprint(bpy) -> str:
    """Hash of every ID's name and primitive properties (2 pointer levels deep) plus mesh vertex positions."""
    from array import array

    h = hashlib.sha1()

    def feed(text: str) -> None:
        h.update(text.encode("utf-8", "replace"))
        h.update(b"\n")

    for attr in dir(bpy.data):
        if attr.startswith("_") or attr in _SKIP_COLLECTIONS:
            continue
        try:
            coll = getattr(bpy.data, attr)
            if not (hasattr(coll, "rna_type") and hasattr(coll, "keys")):
                continue
            ids = list(coll)
        except Exception:
            continue
        if not ids or not isinstance(ids[0], bpy.types.ID):
            continue
        feed(f"[{attr}]")
        for ident in ids:
            feed(f"id:{ident.name}")
            if attr == "images":  # pixel/has_data state is lazy and changes on access; names and paths suffice
                feed(f"image:{ident.filepath}:{ident.source}")
                continue
            _walk(bpy, ident, 2, ident.name, feed)
            if attr == "meshes":
                arr = array("f", bytes(4 * 3 * len(ident.vertices)))
                if len(arr):
                    ident.vertices.foreach_get("co", arr)
                feed("co:" + hashlib.sha1(arr.tobytes()).hexdigest())
    return h.hexdigest()


@dataclass
class Baseline:
    fingerprint: str
    frame: int | None


def _frame(bpy) -> int | None:
    scene = getattr(bpy.context, "scene", None)
    return scene.frame_current if scene is not None else None


def baseline(bpy) -> Baseline:
    """Fingerprint of `bpy.data` plus the current frame, taken before the op runs."""
    return Baseline(fingerprint(bpy), _frame(bpy))


def modified_since(bpy, base: Baseline) -> tuple[bool, str | None]:
    """(fingerprint differs at `base.frame`, "old -> new" when the frame moved).

    If the frame moved it is set back to the original for the comparison (which also neutralises animated
    properties) and then returned to where the op left it.
    """
    now = _frame(bpy)
    if base.frame is None or now is None or now == base.frame:
        return fingerprint(bpy) != base.fingerprint, None
    scene = bpy.context.scene
    scene.frame_set(base.frame)
    try:
        changed = fingerprint(bpy) != base.fingerprint
    finally:
        scene.frame_set(now)
    return changed, f"{base.frame} -> {now}"


def _parse(argv: list[str]) -> str:
    if "--request" not in argv or argv.index("--request") + 1 >= len(argv):
        raise SystemExit("bootstrap: missing --request <path>")
    return argv[argv.index("--request") + 1]


def _write(path: str, payload: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(payload, default=repr), encoding="utf-8")


def main(argv: list[str]) -> int:
    request = json.loads(Path(_parse(argv)).read_text(encoding="utf-8"))
    response_path = request["response_path"]
    src = str(Path(__file__).resolve().parents[2])
    if src not in sys.path:
        sys.path.insert(0, src)
    started = time.perf_counter()
    buf = io.StringIO()
    try:
        import bpy
        from blender_axi.blender_side import ops

        save = request.get("save") or {}
        mode = save.get("mode", "none")
        with contextlib.redirect_stdout(buf):
            before = baseline(bpy) if mode != "none" else None
            result = ops.run(request["op"], request.get("params") or {})
            modified = bool(bpy.data.is_dirty)
            frame_changed = None
            if before is not None:
                changed, frame_changed = modified_since(bpy, before)
                modified = modified or changed
        saved = None
        with contextlib.redirect_stdout(io.StringIO()):  # the save's own `Info: Saved copy as ...` is not op output
            if modified and mode == "copy" and save.get("path"):
                os.makedirs(os.path.dirname(os.path.abspath(save["path"])), exist_ok=True)
                bpy.ops.wm.save_as_mainfile(filepath=save["path"], copy=True)
                saved = save["path"]
            elif modified and mode == "in_place":
                bpy.ops.wm.save_mainfile()
                saved = bpy.data.filepath
        payload = {
            "protocol": 1, "ok": True, "result": result, "modified": modified, "saved": saved,
            "blender_version": bpy.app.version_string,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "stdout": buf.getvalue(), "warnings": [], "frame_changed": frame_changed,
        }
    except BaseException as exc:  # errors are data
        if isinstance(exc, KeyboardInterrupt):
            raise
        payload = {
            "protocol": 1, "ok": False,
            "error": {
                "code": getattr(exc, "code", "OP_FAILED"), "message": getattr(exc, "message", None) or str(exc),
                "traceback": traceback.format_exc(),
            },
        }
    _write(response_path, payload)
    return 0


if __name__ == "__main__":
    main(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
