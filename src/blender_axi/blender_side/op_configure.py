"""`configure`: persist scene settings (engine, resolution, frame range, fps, samples, camera, world colour).

params (each None = leave alone): engine (WORKBENCH|EEVEE|CYCLES), resolution [w, h], percent, frame_start, frame_end,
fps (int or float), samples, camera (object name), world_color [r, g, b], world_strength.
Result: {scene, engine (friendly name after the change), changed: [{field, from, to}], samples_ignored}. `changed` lists
only fields whose value really differed (display values: friendly engine, "WxH", "a-b", fps as fps/fps_base rounded to
2 dp, camera name or "none", world "none"/name), so a repeat call changes nothing and the caller's fingerprint then
reports modified: false. Everything referenced is looked up before anything is changed.
"""

from .common import find_object
from .op_render import ENGINE_IDS
from .ops import OpError

TOLERANCE = 1e-4


def _engine_name(ident: str) -> str:
    for name, value in ENGINE_IDS.items():
        if value == ident:
            return name
    return ident


def _fps_value(render):
    """fps/fps_base rounded to 2 dp; an int when it is one (30/1.0 -> 30, 30/1.001 -> 29.97)."""
    value = render.fps / (render.fps_base or 1.0)
    return int(round(value)) if abs(value - round(value)) < 1e-6 else round(value, 2)


def _background(world):
    tree = getattr(world, "node_tree", None)
    for node in (tree.nodes if tree is not None else []):
        if node.type == "BACKGROUND":
            return node
    raise OpError("OP_FAILED", f"world '{world.name}' has no Background node")


def _rgb(value) -> list:
    return [round(float(v), 3) + 0.0 for v in list(value)[:3]]


def _differs(a, b) -> bool:
    return any(abs(float(x) - float(y)) > TOLERANCE for x, y in zip(a, b))


def _sample_attr(scene):
    """(owner, attribute) holding the sample count of the active engine, or None for engines without one."""
    engine = scene.render.engine
    if engine == "CYCLES":
        return scene.cycles, "samples"
    if engine == "BLENDER_EEVEE":
        return scene.eevee, "taa_render_samples"
    return None


def _set_fps(render, wanted: float) -> None:
    wanted = float(wanted)
    if abs(wanted - round(wanted)) < 1e-9:
        render.fps, render.fps_base = int(round(wanted)), 1.0
    else:
        render.fps = int(round(wanted))
        render.fps_base = round(wanted) / wanted


def _change(changed: list, field: str, before, after) -> None:
    changed.append({"field": field, "from": before, "to": after})


def run(params: dict) -> dict:
    import bpy

    scene = bpy.context.scene
    render = scene.render
    changed: list[dict] = []

    # look everything up first: a bad name leaves the scene untouched
    camera = None
    if params.get("camera"):
        camera = find_object(bpy, params["camera"], "camera")
        if camera.type != "CAMERA":
            raise OpError("OP_FAILED", f"'{camera.name}' is a {camera.type}, not a CAMERA")
    wants_world = params.get("world_color") is not None or params.get("world_strength") is not None
    if wants_world and scene.world is not None:
        _background(scene.world)

    engine = params.get("engine")
    if engine:
        before = _engine_name(render.engine)
        if before != engine:
            try:
                render.engine = ENGINE_IDS[engine]
            except TypeError as exc:
                raise OpError("OP_FAILED", f"engine {engine} is not available in this Blender ({exc})") from exc
            _change(changed, "engine", before, engine)

    resolution = params.get("resolution")
    if resolution:
        want = [int(v) for v in resolution]
        have = [render.resolution_x, render.resolution_y]
        if want != have:
            render.resolution_x, render.resolution_y = want
            _change(changed, "resolution", f"{have[0]}x{have[1]}", f"{want[0]}x{want[1]}")

    if params.get("percent") is not None and int(params["percent"]) != render.resolution_percentage:
        before = render.resolution_percentage
        render.resolution_percentage = int(params["percent"])
        _change(changed, "percent", before, render.resolution_percentage)

    start, end = params.get("frame_start"), params.get("frame_end")
    if start is not None or end is not None:
        before = f"{scene.frame_start}-{scene.frame_end}"
        # start first: Blender pushes the end up with it, then the end is set to its real value
        if start is not None and int(start) != scene.frame_start:
            scene.frame_start = int(start)
        if end is not None and int(end) != scene.frame_end:
            scene.frame_end = int(end)
        after = f"{scene.frame_start}-{scene.frame_end}"
        if after != before:
            _change(changed, "frames", before, after)

    if params.get("fps") is not None:
        before = _fps_value(render)
        if abs(float(params["fps"]) - (render.fps / (render.fps_base or 1.0))) > 0.005:
            _set_fps(render, params["fps"])
            _change(changed, "fps", before, _fps_value(render))

    samples_ignored = False
    if params.get("samples") is not None:
        target = _sample_attr(scene)
        if target is None:
            samples_ignored = True
        else:
            owner, attr = target
            before = int(getattr(owner, attr))
            if before != int(params["samples"]):
                setattr(owner, attr, int(params["samples"]))
                _change(changed, "samples", before, int(getattr(owner, attr)))

    if camera is not None and scene.camera is not camera:
        before = scene.camera.name if scene.camera else "none"
        scene.camera = camera
        _change(changed, "camera", before, camera.name)

    if wants_world:
        _configure_world(bpy, scene, params, changed)

    return {"scene": scene.name, "engine": _engine_name(render.engine), "changed": changed,
            "samples_ignored": samples_ignored}


def _configure_world(bpy, scene, params: dict, changed: list) -> None:
    if scene.world is None:
        world = bpy.data.worlds.new("World")
        scene.world = world
        _change(changed, "world", "none", world.name)
    world = scene.world
    node = _background(world)
    color = params.get("world_color")
    if color is not None:
        socket = node.inputs["Color"]
        before = _rgb(socket.default_value)
        want = [float(v) for v in color]
        if _differs(before, want):
            alpha = float(socket.default_value[3]) if len(socket.default_value) > 3 else 1.0
            socket.default_value = (*want, alpha)
            world.color = tuple(want)
            _change(changed, "world_color", before, _rgb(socket.default_value))
    if params.get("world_strength") is not None:
        socket = node.inputs["Strength"]
        before = round(float(socket.default_value), 3)
        if abs(before - float(params["world_strength"])) > TOLERANCE:
            socket.default_value = float(params["world_strength"])
            _change(changed, "world_strength", before, round(float(socket.default_value), 3))
