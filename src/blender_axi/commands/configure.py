"""`configure`: persist scene settings (engine, resolution, frame range, fps, samples, camera, world colour).

Built with the data API (see `op_configure`), so it behaves the same headless, in the headless MCP server and in the GUI.
Headless writes need `--in-place` or `--out <copy.blend>` (`_mutate`). Setting what is already there changes nothing:
`modified: false` and no file is written. There is no `--frame`: moving the current frame is never a modification
(`render --frame` renders one).
"""

from __future__ import annotations

import re

from ..blender_side.op_render import ENGINE_IDS
from ..errors import AxiError
from ..spec import BatchSpec, CommandSpec, Flag, Invocation, Positional, Result
from . import _mutate
from ._common import Hints, fmt_num, fmt_vec, invalid, quote_name, translate_not_found

_RESOLUTION = re.compile(r"^(\d+)[xX](\d+)$")
_RANGE = re.compile(r"^(\d+)\.\.(\d+)$")
_MAX_SIDE = 65536
_MAX_PERCENT = 32767
SETTING_FLAGS = ("--engine", "--resolution", "--percent", "--frames", "--fps", "--samples", "--camera", "--world",
                 "--world-strength")
NO_CHANGE = "none (values already as requested)"


def _resolution(raw: str) -> list[int]:
    m = _RESOLUTION.match(raw.strip())
    if not m or not (1 <= int(m.group(1)) <= _MAX_SIDE and 1 <= int(m.group(2)) <= _MAX_SIDE):
        raise invalid(f"--resolution expects <width>x<height> (1-{_MAX_SIDE}), got '{raw}'",
                      ["Example: --resolution 1920x1080"])
    return [int(m.group(1)), int(m.group(2))]


def _frames(raw: str) -> tuple[int, int]:
    m = _RANGE.match(raw.strip())
    if not m or int(m.group(1)) > int(m.group(2)):
        raise invalid(f"--frames expects <a..b> with a <= b, got '{raw}'", ["Example: --frames 1..48"])
    return int(m.group(1)), int(m.group(2))


def _params(inv: Invocation) -> dict:
    """Pure argument checks (no Blender, no filesystem) and the op params."""
    f = inv.flags
    if all(f.get(k) is None for k in ("engine", "resolution", "percent", "frames", "fps", "samples", "camera",
                                      "world", "world_strength")):
        raise invalid("configure needs at least one setting flag",
                      ["Setting flags: " + " ".join(SETTING_FLAGS), f"Example: {SPEC.examples[0]}"])
    params: dict = {"engine": f.get("engine"), "resolution": None, "percent": None, "frame_start": None,
                    "frame_end": None, "fps": None, "samples": None, "camera": None, "world_color": None,
                    "world_strength": None}
    if f.get("resolution") is not None:
        params["resolution"] = _resolution(f["resolution"])
    if f.get("percent") is not None:
        if not 1 <= f["percent"] <= _MAX_PERCENT:
            raise invalid(f"--percent must be 1-{_MAX_PERCENT}, got {f['percent']}", ["Example: --percent 50"])
        params["percent"] = f["percent"]
    if f.get("frames") is not None:
        params["frame_start"], params["frame_end"] = _frames(f["frames"])
    if f.get("fps") is not None:
        if not f["fps"] > 0:
            raise invalid(f"--fps must be > 0, got {f['fps']:g}", ["Example: --fps 30 or --fps 29.97"])
        params["fps"] = f["fps"]
    if f.get("samples") is not None:
        if f["samples"] < 1:
            raise invalid(f"--samples must be >= 1, got {f['samples']}", ["Example: --samples 64"])
        params["samples"] = f["samples"]
    if f.get("camera") is not None:
        if not str(f["camera"]).strip():
            raise invalid("--camera is empty", ["Example: --camera Camera"])
        params["camera"] = f["camera"]
    if f.get("world") is not None:
        color = _mutate.parse_vec(SPEC, "--world", f["world"], (3,))
        if not all(0 <= v <= 1 for v in color):
            raise invalid(f"--world values must be between 0 and 1, got '{f['world']}'",
                          ["Example: --world 0.1,0.1,0.2 (r,g,b)"])
        params["world_color"] = color
    if f.get("world_strength") is not None:
        if f["world_strength"] < 0:
            raise invalid(f"--world-strength must be >= 0, got {f['world_strength']:g}", ["Example: --world-strength 1"])
        params["world_strength"] = f["world_strength"]
    return params


def _describe(change: dict) -> str:
    def show(value) -> str:
        if isinstance(value, list):
            return fmt_vec(value)
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, float):
            return fmt_num(value)
        return "none" if value is None else str(value)

    return f"{change['field']}: {show(change['from'])} -> {show(change['to'])}"


def _line(r: dict) -> str:
    return f"{r['scene']} ({', '.join(c['field'] for c in r['changed']) or 'no change'})"


def run(inv: Invocation) -> Result:
    params = _params(inv)
    try:
        target, outcome = _mutate.run_mutation(inv, SPEC, "configure", params)
    except AxiError as err:
        if err.message.startswith("file not found"):
            raise
        given = inv.positionals.get("file")
        objects = f"objects {quote_name(given)} --type CAMERA" if given and not inv.globals.live else "objects --type CAMERA"
        raise translate_not_found(err, [f"Run `{Hints(inv).cmd(objects)}` to list cameras"]) from err
    r = outcome.result
    changed = [_describe(c) for c in r["changed"]]
    data: dict = {"file": target.label, "scene": r["scene"], "changed": changed or NO_CHANGE}
    if r.get("samples_ignored"):
        outcome.warnings = [*outcome.warnings, f"--samples ignored: {r.get('engine', 'this engine')} has no sample setting"]
    hints = [f"Run `{_mutate.hint_cmd(inv, target, outcome, 'scene <file>')}` to check the settings",
             f"Run `{_mutate.hint_cmd(inv, target, outcome, 'render <file> --out <path.png>')}` to render a still"]
    return _mutate.finish(inv, SPEC, target, outcome, data, hints)


SPEC = CommandSpec(
    name="configure",
    summary="Persist scene settings: engine, resolution, frame range, fps, samples, camera, world colour",
    positionals=(Positional("file", required=False, help="The .blend file (default: the single .blend in the current directory)"),),
    flags=(
        Flag("--engine", "str", choices=tuple(ENGINE_IDS), help="Render engine"),
        Flag("--resolution", "str", help="Output size <width>x<height>, e.g. 1920x1080"),
        Flag("--percent", "int", help="Resolution percentage, e.g. 50"),
        Flag("--frames", "str", help="Frame range <a..b>, inclusive (the current frame is not changed)"),
        Flag("--fps", "float", help="Frames per second, e.g. 30 or 29.97"),
        Flag("--samples", "int", help="Sample count (CYCLES, EEVEE; ignored with a warning on WORKBENCH)"),
        Flag("--camera", "str", help="Make this camera object the scene camera"),
        Flag("--world", "str", help="World background colour r,g,b (0-1); creates a world when the file has none"),
        Flag("--world-strength", "float", help="World background strength (>= 0)"),
        *_mutate.MUTATE_FLAGS,
    ),
    examples=(
        "blender-axi configure scene.blend --engine CYCLES --resolution 1920x1080 --samples 64 --in-place",
        "blender-axi configure scene.blend --frames 1..48 --fps 30 --out scene2.blend",
        "blender-axi configure scene.blend --world 0.1,0.1,0.3 --camera Cam --in-place",
        "blender-axi configure --live --resolution 1280x720",
    ),
    run=run,
)

BATCH = BatchSpec("configure", _params, _line)
