"""`render`: render still frames or a frame range of a .blend to image files (plan U5)."""

import os
import re
from pathlib import Path

from ..blender_side.camera_fit import TEMP_ROTATION_DEG
from ..blender_side.op_render import ENGINE_IDS, EXT_FORMATS, FORMAT_EXT, output_paths
from ..errors import AxiError
from ..spec import CommandSpec, Flag, Invocation, Positional, Result
from ._common import Hints, display_path, fmt_vec, invalid, live_suffix, quote_name, refused, resolve_blend_inferred

_RESOLUTION = re.compile(r"^(\d+)[xX](\d+)$")
_RANGE = re.compile(r"^(\d+)\.\.(\d+)$")
_FORMATS = ("PNG", "JPEG", "WEBP", "OPEN_EXR", "OPEN_EXR_MULTILAYER", "TIFF", "BMP", "TARGA")
_MAX_SIDE = 65536
_MAX_PERCENT = 32767
_FIT_MARGIN = 1.1


# --- render ----------------------------------------------------------------------------------------

def _parse_resolution(raw: str) -> list[int]:
    m = _RESOLUTION.match(raw.strip())
    if not m or not (1 <= int(m.group(1)) <= _MAX_SIDE and 1 <= int(m.group(2)) <= _MAX_SIDE):
        raise invalid(f"--resolution expects <width>x<height> (1-{_MAX_SIDE}), got '{raw}'",
                       ["Example: --resolution 1280x720", "blender-axi render --help"])
    return [int(m.group(1)), int(m.group(2))]


def _parse_frames(inv: Invocation) -> list[int] | None:
    frame, frames = inv.flags.get("frame"), inv.flags.get("frames")
    if frame is not None and frames is not None:
        raise invalid("--frame and --frames are mutually exclusive",
                       ["Use --frame <n> for one frame or --frames <a..b> for a range"])
    if frame is not None:
        if frame < 0:
            raise invalid(f"--frame must be >= 0, got {frame}", ["blender-axi render --help"])
        return [frame]
    if frames is not None:
        m = _RANGE.match(frames.strip())
        if not m or int(m.group(1)) > int(m.group(2)):
            raise invalid(f"--frames expects <a..b> with a <= b, got '{frames}'",
                           ["Example: --frames 1..24", "blender-axi render --help"])
        return list(range(int(m.group(1)), int(m.group(2)) + 1))
    return None


def _resolve_out(inv: Invocation, blend: Path | None) -> tuple[str, str | None]:
    """Absolute output template (may contain `#`) and the explicit --format (or None to infer)."""
    fmt = inv.flags.get("format")
    raw = inv.flags.get("out")
    if raw is None:
        stem = blend.stem if blend is not None else "render"
        raw = f"{stem}_f####" + FORMAT_EXT.get(fmt or "PNG", ".png")
    ext = os.path.splitext(raw)[1].lower()
    if ext == "":
        raw += FORMAT_EXT.get(fmt or "PNG", ".png")
    elif fmt is None and ext not in EXT_FORMATS:
        raise invalid(f"unrecognised image extension '{ext}' in --out",
                       ["Use one of " + ", ".join(sorted(EXT_FORMATS)) + " or pass --format <PNG|JPEG|...>"])
    elif fmt is not None and ext in EXT_FORMATS and EXT_FORMATS[ext] != fmt.replace("_MULTILAYER", ""):
        raise invalid(f"--out extension {ext} does not match --format {fmt}",
                       [f"Use --out with a {FORMAT_EXT[fmt]} extension, or drop --format"])
    return os.path.abspath(os.path.expanduser(raw)), fmt


def _pattern(template: str, multi: bool) -> str:
    if multi and "#" not in template:
        stem, ext = os.path.splitext(template)
        return f"{stem}_####{ext}"
    return template


def _fit_text(fit: dict) -> str:
    """`Cam moved 0,-8,4 -> 0,-17.2,8.3` | `temporary camera at 0,-17.2,8.3 (rotation 63.6,0,46.7)`."""
    if fit["temporary"]:
        return f"temporary camera at {fmt_vec(fit['location'])} (rotation {fmt_vec(TEMP_ROTATION_DEG)})"
    text = f"{fit['camera']} moved {fmt_vec(fit['from'])} -> {fmt_vec(fit['location'])}"
    if fit.get("ortho_scale") is not None:
        text += f", ortho scale {fmt_vec([fit['ortho_scale']])}"
    return text


def _keep_hint(inv: Invocation, blend: Path | None, fit: dict) -> str | None:
    """The one call that makes the fitted framing permanent (the render itself never writes the file)."""
    h = Hints(inv)
    loc = fmt_vec(fit["location"])
    f = quote_name(display_path(str(blend))) if blend is not None else "<file>"
    if fit["temporary"]:
        return (f"Run `{h.cmd('add camera ' + f + ' --name Cam --location ' + loc + ' --rotation ' + fmt_vec(TEMP_ROTATION_DEG))}"
                f"{'' if inv.globals.live else ' --in-place'}` to keep it")
    if fit.get("ortho_scale") is not None:
        return None  # `set` cannot change ortho_scale, so a location alone would not reproduce the framing
    return (f"Run `{h.cmd('set ' + quote_name(fit['camera']) + ' ' + f + ' --location ' + loc)}"
            f"{'' if inv.globals.live else ' --in-place'}` to keep this framing")


def run(inv: Invocation) -> Result:
    flags = inv.flags
    frames = _parse_frames(inv)
    resolution = _parse_resolution(flags["resolution"]) if flags.get("resolution") else None
    if flags.get("percent") is not None and not 1 <= flags["percent"] <= _MAX_PERCENT:
        raise invalid(f"--percent must be 1-{_MAX_PERCENT}, got {flags['percent']}", ["blender-axi render --help"])
    if flags.get("samples") is not None and flags["samples"] < 1:
        raise invalid(f"--samples must be >= 1, got {flags['samples']}", ["blender-axi render --help"])

    fit = bool(flags.get("fit"))
    margin = flags.get("fit_margin")
    if margin is None:
        margin = _FIT_MARGIN
    if margin < 1:
        raise invalid(f"--fit-margin must be >= 1, got {margin:g}", ["1 = tight, 1.1 = 10% extra room (default)"])
    if margin != _FIT_MARGIN and not fit:
        raise invalid("--fit-margin needs --fit", ["Example: blender-axi render <file> --fit --fit-margin 1.3"])

    blend, inferred = resolve_blend_inferred(inv, "render")
    out, fmt = _resolve_out(inv, blend)

    known = frames if frames is not None else ([0] if "#" not in out else None)
    if known is not None and not flags.get("force"):
        existing = [p for p in output_paths(out, known) if os.path.exists(p)]
        if existing:
            raise refused(inv, SPEC, existing[0])

    params = {
        "out": out, "frames": frames, "engine": flags.get("engine"), "resolution": resolution,
        "percent": flags.get("percent"), "samples": flags.get("samples"), "camera": flags.get("camera"),
        "format": fmt, "force": bool(flags.get("force")),
        "fit": fit, "fit_margin": float(margin), "temp_camera_ok": not inv.globals.live,
    }
    try:
        outcome = inv.transport.run("render", params, blend=blend, timeout=inv.globals.timeout)
    except AxiError as err:
        if err.code == "WRITE_REFUSED":
            raise refused(inv, SPEC, err.message.removeprefix("output exists: ")) from err
        if err.code == "NOT_FOUND" and fit and "no active camera" in err.message:
            raise AxiError("NOT_FOUND", err.message, help=[
                f"Run `{Hints(inv).cmd('add camera <file>')}` then rerun with --fit"]) from err
        if err.code == "NOT_FOUND" and "camera" in err.message:
            raise AxiError("NOT_FOUND", err.message, help=[
                "List cameras with `blender-axi objects <file> --type CAMERA`", "Pick one with --camera <name>"]) from err
        raise

    res = outcome.result
    n = len(res["files"])
    data: dict = {}
    if inferred and blend is not None:
        data["file"] = blend.name
    data["rendered"] = f"{n} frame" + ("" if n == 1 else "s")
    data["output"] = display_path(res["files"][0]) if n == 1 else display_path(_pattern(out, True))
    data["size"] = res["size"]
    data["engine"] = res["engine"]
    data["samples"] = res["samples"] if res.get("samples") is not None else "n/a"
    if res.get("fit"):
        data["fit"] = _fit_text(res["fit"])
    data["duration_ms"] = outcome.duration_ms
    warnings = list(outcome.warnings)
    if res.get("samples_ignored"):
        warnings.append(f"--samples ignored: {res['engine']} has no sample setting")
    if warnings:
        data["warning"] = "; ".join(warnings)
    live = live_suffix(inv)
    help_ = [
        f"Run `blender-axi render <file> --frames <a..b> --out <dir/name_####.png>{live}` to render a range",
        f"Run `blender-axi export <file> --out <path.glb>{live}` to export geometry",
    ]
    if res.get("fit"):
        keep = _keep_hint(inv, blend, res["fit"])
        if keep:
            help_.insert(0, keep)
    return Result(data=data, help=help_)


SPEC = CommandSpec(
    name="render",
    summary="Render a frame or frame range of a .blend to image files",
    positionals=(Positional("file", required=False, help="The .blend file (default: the single .blend in the cwd)"),),
    flags=(
        Flag("--frame", "int", help="Frame to render (default: the scene's current frame)"),
        Flag("--frames", "str", help="Frame range <a..b>, inclusive; writes one file per frame"),
        Flag("--out", "path", help="Output path; `#` runs are the frame number (default ./<stem>_f####.png)"),
        Flag("--engine", "str", choices=tuple(ENGINE_IDS), help="Render engine (default: the scene's own)"),
        Flag("--resolution", "str", help="Output size <width>x<height>, e.g. 1280x720"),
        Flag("--percent", "int", help="Resolution percentage, e.g. 50"),
        Flag("--samples", "int", help="Sample count (CYCLES, EEVEE)"),
        Flag("--camera", "str", help="Camera object name (default: the scene camera)"),
        Flag("--fit", "bool", help="Move the camera back along its view axis so every visible object is in frame "
             "(the file is not changed)"),
        Flag("--fit-margin", "float", default=_FIT_MARGIN, help="Extra room around the fitted objects, 1 = tight"),
        Flag("--format", "str", choices=_FORMATS, help="Image format (default: from the --out extension)"),
        Flag("--force", "bool", help="Overwrite existing output files"),
    ),
    examples=(
        "blender-axi render scene.blend --engine WORKBENCH --resolution 640x360 --out preview.png",
        "blender-axi render scene.blend --frames 1..24 --out renders/shot_####.png --force",
        "blender-axi render scene.blend --fit --out framed.png",
    ),
    run=run,
)
