"""`render`: render one or more frames of the loaded scene to image files (plan U5).

params: out (absolute path or `name_####.png` template), frames (list[int] | None = current frame),
engine (WORKBENCH|EEVEE|CYCLES | None), resolution ([w, h] | None), percent, samples, camera,
format (image format enum | None), force.

Pure-Python helpers (`output_paths`, `ENGINE_IDS`, `EXT_FORMATS`) have no `bpy` dependency; the CLI
imports them so the write-refusal pre-check and the op agree on every path.
"""

import os
import re

from .ops import OpError

ENGINE_IDS = {"WORKBENCH": "BLENDER_WORKBENCH", "EEVEE": "BLENDER_EEVEE", "CYCLES": "CYCLES"}
EXT_FORMATS = {
    ".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP", ".exr": "OPEN_EXR",
    ".tif": "TIFF", ".tiff": "TIFF", ".bmp": "BMP", ".tga": "TARGA",
}
FORMAT_EXT = {
    "PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp", "OPEN_EXR": ".exr", "OPEN_EXR_MULTILAYER": ".exr",
    "TIFF": ".tif", "BMP": ".bmp", "TARGA": ".tga",
}
_HASHES = re.compile(r"#+")


def output_paths(template: str, frames: list[int], multi: bool | None = None) -> list[str]:
    """Output file for each frame. `#` runs are replaced by the zero-padded frame number; without `#`
    a multi-frame render appends `_####` before the extension and a single frame uses the path as is."""
    if multi is None:
        multi = len(frames) > 1
    if not _HASHES.search(template):
        if not multi:
            return [template] * len(frames)
        stem, ext = os.path.splitext(template)
        template = f"{stem}_####{ext}"
    return [_HASHES.sub(lambda m, f=f: str(f).zfill(len(m.group(0))), template, count=1) for f in frames]


def _apply_engine(scene, engine: str) -> None:
    ident = ENGINE_IDS[engine]
    try:
        scene.render.engine = ident
    except TypeError as exc:
        raise OpError("OP_FAILED", f"engine {engine} is not available in this Blender ({exc})") from exc


def _apply_samples(scene, samples: int) -> bool:
    """Set the sample count for the active engine; False when the engine has no such setting."""
    engine = scene.render.engine
    if engine == "CYCLES":
        scene.cycles.samples = samples
        return True
    if engine == "BLENDER_EEVEE":
        scene.eevee.taa_render_samples = samples
        return True
    return False


def _effective_samples(scene):
    engine = scene.render.engine
    if engine == "CYCLES":
        return int(scene.cycles.samples)
    if engine == "BLENDER_EEVEE":
        return int(scene.eevee.taa_render_samples)
    return None


def _engine_name(ident: str) -> str:
    for name, value in ENGINE_IDS.items():
        if value == ident:
            return name
    return ident


_RENDER_ATTRS = ("engine", "resolution_x", "resolution_y", "resolution_percentage", "filepath", "use_file_extension")
# file_format first: changing it coerces the others, so it is restored before them
_IMAGE_ATTRS = ("file_format", "color_mode", "color_depth", "compression", "quality", "exr_codec")
_SAMPLE_ATTRS = (("cycles", "samples"), ("eevee", "taa_render_samples"))
_MISSING = object()


def _read(obj, name):
    try:
        return getattr(obj, name, _MISSING)
    except Exception:  # an RNA property that is unreadable in this state is simply not snapshotted
        return _MISSING


def _snapshot_attrs(obj, names) -> dict:
    out = {}
    for name in names:
        value = _read(obj, name)
        if value is not _MISSING:
            out[name] = value
    return out


def _restore_attrs(obj, snap: dict, label: str, failures: list) -> None:
    for name, value in snap.items():
        if _read(obj, name) == value:
            continue
        try:
            setattr(obj, name, value)
        except Exception as exc:
            failures.append(f"{label}{name}: {exc}")


def _snapshot(scene) -> dict:
    """Every scene property `run` may assign; `_restore` puts them back so live mode (the user's open
    session) is left exactly as found. The same path runs headless, where it is harmless."""
    render = scene.render
    return {
        "render": _snapshot_attrs(render, _RENDER_ATTRS),
        "image": _snapshot_attrs(render.image_settings, _IMAGE_ATTRS),
        "samples": {(sub, attr): v for sub, attr in _SAMPLE_ATTRS
                    if (sub_obj := getattr(scene, sub, None)) is not None
                    and (v := _read(sub_obj, attr)) is not _MISSING},
        "camera": scene.camera,
        "frame": scene.frame_current,
        "subframe": _read(scene, "frame_subframe"),
    }


def _restore(scene, snap: dict) -> list[str]:
    """Best effort: every property is attempted; returns descriptions of the ones that could not be restored."""
    failures: list[str] = []
    render = scene.render
    _restore_attrs(render, snap["render"], "render.", failures)
    _restore_attrs(render.image_settings, snap["image"], "render.image_settings.", failures)
    for (sub, attr), value in snap["samples"].items():
        _restore_attrs(getattr(scene, sub, None), {attr: value}, f"{sub}.", failures)
    try:
        if scene.camera is not snap["camera"]:
            scene.camera = snap["camera"]
    except Exception as exc:
        failures.append(f"camera: {exc}")
    try:
        sub = snap["subframe"]
        if scene.frame_current != snap["frame"] or (sub is not _MISSING and _read(scene, "frame_subframe") != sub):
            if sub not in (_MISSING, 0, 0.0):
                scene.frame_set(snap["frame"], subframe=sub)
            else:
                scene.frame_set(snap["frame"])
    except Exception as exc:
        failures.append(f"frame_current: {exc}")
    return failures


def _render(bpy, scene, params: dict) -> dict:
    render = scene.render

    if params.get("engine"):
        _apply_engine(scene, params["engine"])
    if params.get("resolution"):
        render.resolution_x, render.resolution_y = (int(v) for v in params["resolution"])
    if params.get("percent") is not None:
        render.resolution_percentage = int(params["percent"])
    samples_ignored = False
    if params.get("samples") is not None:
        samples_ignored = not _apply_samples(scene, int(params["samples"]))

    camera = params.get("camera")
    if camera:
        obj = bpy.data.objects.get(camera)
        if obj is None or obj.type != "CAMERA":
            raise OpError("NOT_FOUND", f"camera not found: {camera}")
        scene.camera = obj
    elif scene.camera is None:
        raise OpError("NOT_FOUND", "scene has no active camera")

    fmt = params.get("format") or EXT_FORMATS.get(os.path.splitext(params["out"])[1].lower(), "PNG")
    render.image_settings.file_format = fmt
    render.use_file_extension = False  # write exactly the path we computed

    frames = params.get("frames") or [scene.frame_current]
    files = output_paths(params["out"], frames)
    if not params.get("force"):
        existing = [f for f in files if os.path.exists(f)]
        if existing:
            raise OpError("WRITE_REFUSED", f"output exists: {existing[0]}")
    for f in files:
        os.makedirs(os.path.dirname(os.path.abspath(f)), exist_ok=True)

    for frame, path in zip(frames, files):
        scene.frame_set(frame)
        render.filepath = path
        bpy.ops.render.render(write_still=True)
        if not os.path.isfile(path):
            raise OpError("OP_FAILED", f"Blender rendered frame {frame} but wrote no file at {path}")

    scale = render.resolution_percentage / 100
    return {
        "frames": frames,
        "files": files,
        "size": f"{int(render.resolution_x * scale)}x{int(render.resolution_y * scale)}",
        "engine": _engine_name(render.engine),
        "samples": _effective_samples(scene),
        "samples_ignored": samples_ignored,
        "format": fmt,
    }


def run(params: dict) -> dict:
    import bpy

    scene = bpy.context.scene
    snap = _snapshot(scene)
    try:
        result = _render(bpy, scene, params)  # the result is computed from the render settings, before restoring
    finally:
        failures = _restore(scene, snap)  # also on failure; never masks the original exception
    if failures:
        result["restore_warnings"] = failures
    return result
