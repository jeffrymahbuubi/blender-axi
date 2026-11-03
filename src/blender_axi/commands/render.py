"""`render`: render still frames, a frame range or a turntable (png or mp4) of a .blend to image files."""

import os
import re
from pathlib import Path

from ..blender_side import lighting
from ..blender_side.camera_fit import TEMP_ROTATION_DEG
from .. import paths
from ..blender_side.op_render import ENGINE_IDS, EXT_FORMATS, FORMAT_EXT, output_paths
from ..blender_side.sheet import grid
from ..blender_side.video import VIDEO_EXTS
from ..errors import AxiError
from ..spec import CommandSpec, Flag, Invocation, Positional, Result
from ._common import (MAX_NAMES, Hints, display_path, fmt_vec, invalid, live_suffix, names_summary, quote_name, refused,
                      resolve_blend_inferred, translate_not_found)

_RESOLUTION = re.compile(r"^(\d+)[xX](\d+)$")
_RANGE = re.compile(r"^(\d+)\.\.(\d+)$")
_FORMATS = ("PNG", "JPEG", "WEBP", "OPEN_EXR", "OPEN_EXR_MULTILAYER", "TIFF", "BMP", "TARGA")
_MAX_SIDE = 65536
_MAX_PERCENT = 32767
_FIT_MARGIN = 1.1
_DEFAULT_FPS = 12


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


def _resolve_out(inv: Invocation, blend: Path | None, multi: bool = False, sheet: bool = False,
                 turntable: bool = False) -> tuple[str, str | None]:
    """Absolute output template (may contain `#`) and the explicit --format (or None to infer). `multi` = a turntable
    or a `--frames` range (the only renders that can pack into one mp4 or one sheet image)."""
    fmt = inv.flags.get("format")
    raw = inv.flags.get("out")
    if sheet and not multi:
        raise invalid("--sheet needs --frames <a..b> or --turntable N",
                      ["Example: blender-axi render <file> --turntable 12 --fit --sheet --out sheet.png",
                       "Example: blender-axi render <file> --frames 1..12 --sheet --out sheet.png"])
    if raw is None:
        stem = blend.stem if blend is not None else "render"
        name = (f"{stem}_turn_sheet" if turntable else f"{stem}_sheet") if sheet else (
            f"{stem}_turn_####" if turntable else f"{stem}_f####")
        raw = name + FORMAT_EXT.get(fmt or "PNG", ".png")
    ext = os.path.splitext(raw)[1].lower()
    if ext in VIDEO_EXTS:
        if sheet:
            raise invalid("--sheet writes one image grid; use --out name.png", ["Example: --sheet --out sheet.png"])
        if not multi:
            raise invalid(f"--out name{ext} needs --frames <a..b> or --turntable N",
                           ["Example: blender-axi render <file> --frames 1..24 --out clip.mp4",
                            "Example: blender-axi render <file> --turntable 12 --fit --out turn.mp4"])
        if fmt is not None:
            raise invalid("--format does not apply to mp4 output", ["Drop --format; the .mp4 extension selects H.264 video"])
        return os.path.abspath(os.path.expanduser(raw)), None
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


def _subject_text(subject: dict) -> str | None:
    """`2 named (Mug, MugHandle)` | `12 objects (ground excluded: Floor)`; None when nothing was chosen or excluded."""
    if subject["mode"] == "named":
        return names_summary(subject["objects"]).replace(" (", " named (", 1)
    if not subject["excluded"]:
        return None
    gone = subject["excluded"]
    shown = ", ".join(gone[:MAX_NAMES]) + (f", +{len(gone) - MAX_NAMES} more" if len(gone) > MAX_NAMES else "")
    return f"{len(subject['objects'])} objects (ground excluded: {shown})"


def _aim_text(aim: dict, camera: str, subject: dict | None) -> str:
    """`Cam turned 63.6,0,46.7 -> 65.1,0,46.7 toward 0,0,0.9`."""
    text = f"{camera} turned {fmt_vec(aim['from'])} -> {fmt_vec(aim['to'])}"
    if subject and subject.get("centre"):
        text += f" toward {fmt_vec(subject['centre'])}"
    return text


def _keep_hint(inv: Invocation, blend: Path | None, fit: dict, aim: dict | None = None) -> str | None:
    """The one call that makes the fitted framing permanent (the render itself never writes the file)."""
    h = Hints(inv)
    loc = fmt_vec(fit["location"])
    f = quote_name(display_path(str(blend))) if blend is not None else "<file>"
    if fit["temporary"]:
        return (f"Run `{h.cmd('add camera ' + f + ' --name Cam --location ' + loc + ' --rotation ' + fmt_vec(TEMP_ROTATION_DEG))}"
                f"{'' if inv.globals.live else ' --in-place'}` to keep it")
    if fit.get("ortho_scale") is not None:
        return None  # `set` cannot change ortho_scale, so a location alone would not reproduce the framing
    rot = f" --rotation {fmt_vec(aim['to'])}" if aim else ""  # the turntable turned the camera too
    return (f"Run `{h.cmd('set ' + quote_name(fit['camera']) + ' ' + f + ' --location ' + loc + rot)}"
            f"{'' if inv.globals.live else ' --in-place'}` to keep this framing")


def _auto_light_text(light: dict) -> str:
    if not light["applied"]:
        return "not needed (scene is lit)"
    if light["fill"] == "world created":
        return "temporary Sun + world fill (removed after)"
    return "temporary Sun (removed after; the file's world is black)"


def _light_hint(inv: Invocation, blend: Path | None) -> str:
    """Unlit scene rendered as is: the opt-in preview fix, then the permanent one."""
    h = Hints(inv)
    f = quote_name(display_path(str(blend))) if blend is not None else "<file>"
    return (f"Rerun with --auto-light for a lit preview, or run `{h.cmd('add light ' + f + ' --type SUN')}"
            f"{'' if inv.globals.live else ' --in-place'}` to light the file")


def _parse_turntable(inv: Invocation, frames: list[int] | None) -> int | None:
    """`--turntable N` (>= 2, current frame only); also the `--fps` lower bound."""
    n = inv.flags.get("turntable")
    fps = inv.flags.get("fps")
    if fps is not None and fps < 1:
        raise invalid(f"--fps must be >= 1, got {fps}", ["blender-axi render --help"])
    if n is None:
        return None
    if n < 2:
        raise invalid(f"--turntable needs at least 2 steps, got {n}", ["Example: blender-axi render <file> --turntable 12 --fit"])
    if frames is not None:
        raise invalid("--turntable renders the current frame; drop --frame/--frames",
                       ["A turntable orbits the camera around the scene at one frame"])
    return n


def run(inv: Invocation) -> Result:
    flags = inv.flags
    frames = _parse_frames(inv)
    turn = _parse_turntable(inv, frames)
    resolution = _parse_resolution(flags["resolution"]) if flags.get("resolution") else None
    if flags.get("percent") is not None and not 1 <= flags["percent"] <= _MAX_PERCENT:
        raise invalid(f"--percent must be 1-{_MAX_PERCENT}, got {flags['percent']}", ["blender-axi render --help"])
    if flags.get("samples") is not None and flags["samples"] < 1:
        raise invalid(f"--samples must be >= 1, got {flags['samples']}", ["blender-axi render --help"])

    fit = bool(flags.get("fit"))
    subject = flags.get("subject") or None
    if flags.get("subject") is not None and not subject:
        raise invalid("--subject needs at least one object name", ["Example: blender-axi render <file> --turntable 12 --fit --subject Mug"])
    if subject and turn is None and not fit:
        raise invalid("--subject needs --turntable or --fit",
                      ["Example: blender-axi render <file> --turntable 12 --fit --subject Mug,MugHandle",
                       "Example: blender-axi render <file> --fit --subject Mug --out mug.png"])
    margin = flags.get("fit_margin")
    if margin is None:
        margin = _FIT_MARGIN
    if margin < 1:
        raise invalid(f"--fit-margin must be >= 1, got {margin:g}", ["1 = tight, 1.1 = 10% extra room (default)"])
    if margin != _FIT_MARGIN and not fit:
        raise invalid("--fit-margin needs --fit", ["Example: blender-axi render <file> --fit --fit-margin 1.3"])

    blend, inferred = resolve_blend_inferred(inv, "render")
    sheet = bool(flags.get("sheet"))
    ranged = flags.get("frames") is not None  # a `--frames` range (not a single `--frame`)
    out, fmt = _resolve_out(inv, blend, turn is not None or ranged, sheet, turn is not None)
    mp4 = os.path.splitext(out)[1].lower() in VIDEO_EXTS
    fps = flags.get("fps")
    if fps is not None and not mp4:
        raise invalid("--fps applies to mp4 output", ["Example: blender-axi render <file> --turntable 12 --out turn.mp4 --fps 24"])
    pack = mp4 or sheet  # one output file for the whole turn / range

    if pack:
        known = [0]
    elif turn is not None:
        known = list(range(1, turn + 1))
    else:
        known = frames if frames is not None else ([0] if "#" not in out else None)
    if known is not None and not flags.get("force"):
        existing = ([out] if os.path.exists(out) else []) if pack else [p for p in output_paths(out, known) if os.path.exists(p)]
        if existing:
            raise refused(inv, SPEC, existing[0])

    params = {
        "out": out, "frames": frames, "engine": flags.get("engine"), "resolution": resolution,
        "percent": flags.get("percent"), "samples": flags.get("samples"), "camera": flags.get("camera"),
        "format": fmt, "force": bool(flags.get("force")),
        "fit": fit, "fit_margin": float(margin), "temp_camera_ok": not inv.globals.live,
        "auto_light": bool(flags.get("auto_light")),
        "subject": subject, "turntable": turn, "video": None, "sheet": None,
    }
    run_dir = None
    if pack:  # where the stills go; made here so a live Blender (same machine) can write into it
        run_dir = paths.run_dir("turntable" if turn is not None else "frames")
        if mp4:  # fps None = the scene's own (ranges); a turntable has no scene rate and keeps 12
            params["video"] = {"out": out, "frames_dir": str(run_dir),
                               "fps": int(fps) if fps is not None else (_DEFAULT_FPS if turn is not None else None)}
        else:
            n = turn if turn is not None else len(frames)
            params["sheet"] = {"out": out, "cols": grid(n)[0], "frames_dir": str(run_dir)}
    try:
        outcome = _transport_render(inv, params, blend, fit)
    finally:
        if run_dir is not None:
            paths.cleanup(run_dir, inv.globals.keep_tmp)
    return _result(inv, blend, inferred, out, outcome, turn, "video" if mp4 else "sheet" if sheet else None)


def _transport_render(inv: Invocation, params: dict, blend: Path | None, fit: bool):
    try:
        return inv.transport.run("render", params, blend=blend, timeout=inv.globals.timeout)
    except AxiError as err:
        if err.code == "WRITE_REFUSED":
            raise refused(inv, SPEC, err.message.removeprefix("output exists: ")) from err
        if err.code == "NOT_FOUND" and fit and "no active camera" in err.message:
            raise AxiError("NOT_FOUND", err.message, help=[
                f"Run `{Hints(inv).cmd('add camera <file>')}` then rerun with --fit"]) from err
        if err.code == "NOT_FOUND" and err.message.startswith("subject "):
            raise translate_not_found(err, [f"Run `{Hints(inv).cmd('objects <file>')}` to list objects"]) from None
        if err.code == "NOT_FOUND" and "camera" in err.message:
            raise AxiError("NOT_FOUND", err.message, help=[
                "List cameras with `blender-axi objects <file> --type CAMERA`", "Pick one with --camera <name>"]) from err
        raise


def _turntable_text(turn: dict) -> str:
    return f"{turn['steps']} steps around {fmt_vec(turn['centre'])} ({turn['camera']})"


def _video_text(video: dict) -> str:
    size = video["size_bytes"]
    return f"{video['codec']} {video['fps']} fps, " + (f"{size} B" if size < 1024 else f"{round(size / 1024)} KB")


def _sheet_text(sheet: dict) -> str:
    size = sheet["size_bytes"]
    return (f"{sheet['cols']}x{sheet['rows']} tiles, {sheet['width']}x{sheet['height']}, "
            + (f"{size} B" if size < 1024 else f"{round(size / 1024)} KB"))


def _result(inv: Invocation, blend: Path | None, inferred: bool, out: str, outcome, turn: int | None,
            pack: str | None = None) -> Result:
    """`pack` = "video" | "sheet" when the turn / range was packed into one file, else None."""
    res = outcome.result
    n = len(res["files"])
    data: dict = {}
    if inferred and blend is not None:
        data["file"] = blend.name
    if pack:
        count = res["turntable"]["steps"] if turn is not None else len(res["frames"])
        data["rendered"] = f"{count} frames -> 1 " + ("video" if pack == "video" else "sheet")
        data["output"] = display_path(out)
    elif turn is not None:
        data["rendered"] = f"{res['turntable']['steps']} frames"
        data["output"] = display_path(_pattern(out, True))
    else:
        data["rendered"] = f"{n} frame" + ("" if n == 1 else "s")
        data["output"] = display_path(res["files"][0]) if n == 1 else display_path(_pattern(out, True))
    data["size"] = res["size"]
    data["engine"] = res["engine"]
    data["samples"] = res["samples"] if res.get("samples") is not None else "n/a"
    if res.get("fit"):
        data["fit"] = _fit_text(res["fit"])
    if res.get("auto_light"):
        data["auto_light"] = _auto_light_text(res["auto_light"])
    if res.get("subject"):
        text = _subject_text(res["subject"])
        if text:
            data["subject"] = text
    if res.get("aim"):
        data["aim"] = _aim_text(res["aim"], (res.get("turntable") or {}).get("camera", "Camera"), res.get("subject"))
    if res.get("turntable"):
        data["turntable"] = _turntable_text(res["turntable"])
    if res.get("video"):
        data["video"] = _video_text(res["video"])
    if res.get("sheet"):
        data["sheet"] = _sheet_text(res["sheet"])
    data["duration_ms"] = outcome.duration_ms
    warnings = list(outcome.warnings)
    if res.get("samples_ignored"):
        warnings.append(f"--samples ignored: {res['engine']} has no sample setting")
    lit = res.get("lighting")
    unlit_unfixed = bool(lit and lit.get("unlit")) and not (res.get("auto_light") or {}).get("applied")
    if unlit_unfixed:
        warnings.append(lighting.warning(lit, res["engine"]))
    if warnings:
        data["warning"] = "; ".join(warnings)
    live = live_suffix(inv)
    if turn is not None:
        flag, target = {"video": ("", "<turn.mp4>"), "sheet": (" --sheet", "<sheet.png>")}.get(pack, ("", "<dir/turn_####.png>"))
        first = f"Run `blender-axi render <file> --turntable 12 --fit{flag} --out {target}{live}` for a finer turn"
    else:
        flag, target = {"video": ("", "<clip.mp4>"), "sheet": (" --sheet", "<sheet.png>")}.get(pack, ("", "<dir/name_####.png>"))
        first = f"Run `blender-axi render <file> --frames <a..b>{flag} --out {target}{live}` to render a range"
    help_ = [first, f"Run `blender-axi export <file> --out <path.glb>{live}` to export geometry"]
    pos = 0  # new hints go after the keep-framing hint, ahead of the range and export hints
    if res.get("fit"):
        keep = _keep_hint(inv, blend, res["fit"], res.get("aim"))
        if keep:
            help_.insert(0, keep)
            pos = 1
    if unlit_unfixed:
        help_.insert(pos, _light_hint(inv, blend))
    return Result(data=data, help=help_)


SPEC = CommandSpec(
    name="render",
    summary="Render a frame, frame range or turntable (png or mp4) of a .blend to image files",
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
        Flag("--auto-light", "bool", help="If the scene is unlit, render with a temporary Sun and world fill; "
             "nothing is saved"),
        Flag("--turntable", "int", help="Render N stills of the current frame with the camera orbited 360 degrees around "
             "the visible geometry (name_####.png, or one mp4 with --out name.mp4); the file is not changed"),
        Flag("--subject", "list", help="Objects to orbit and fit around, comma-separated (default: all visible "
             "geometry minus a ground plane)"),
        Flag("--sheet", "bool", help="Write one contact-sheet image (grid of all frames) instead of N files "
             "(--turntable or --frames; --out name.png)"),
        Flag("--fps", "int", help="Frames per second of the mp4 (default: 12 for --turntable, the scene fps for --frames)"),
        Flag("--format", "str", choices=_FORMATS, help="Image format (default: from the --out extension)"),
        Flag("--force", "bool", help="Overwrite existing output files"),
    ),
    examples=(
        "blender-axi render scene.blend --engine WORKBENCH --resolution 640x360 --out preview.png",
        "blender-axi render scene.blend --frames 1..24 --out renders/shot_####.png --force",
        "blender-axi render scene.blend --fit --out framed.png",
        "blender-axi render scene.blend --fit --auto-light --out preview.png",
        "blender-axi render scene.blend --turntable 12 --fit --out turn.mp4 --fps 24",
        "blender-axi render scene.blend --frames 1..24 --out clip.mp4",
        "blender-axi render scene.blend --turntable 12 --fit --sheet --out sheet.png",
    ),
    run=run,
)
