"""`render --auto-light`: light an unlit scene for one render without leaving anything behind (0.6.0 plan K2, §3.3).

A temporary Sun is linked into the scene's own collection; when the file has no world at all, a temporary grey world
fill is created and assigned too (a Sun alone leaves the shadow side black in EEVEE). An existing world, even a black
one, is never edited: restoring user nodes is riskier than creating and removing datablocks. `AutoLightState.undo`
puts `scene.world` back and removes everything `apply` created, best effort, also when the render raised.

Data API only; `bpy` is passed in so the unit tests can drive it with plain stubs.
"""

import math

SUN_NAME, WORLD_NAME = "AxiSun", "AxiFill"
SUN_ENERGY = 3.0
SUN_ROTATION_DEG = (45.0, 0.0, 45.0)
FILL_RGB, FILL_STRENGTH = (0.3, 0.3, 0.3), 0.5


class AutoLightState:
    """What `apply` created, recorded as it goes so `undo` is complete even when `apply` fails midway."""

    def __init__(self):
        self.sun_object = None
        self.sun_data = None
        self.world = None
        self.previous_world = None
        self.world_set = False

    def undo(self, bpy, scene) -> list[str]:
        """Best effort; returns descriptions of what could not be removed or restored."""
        failures: list[str] = []
        if self.world_set:
            try:
                scene.world = self.previous_world
            except Exception as exc:
                failures.append(f"auto-light world: {exc}")
        for label, attr, item in (("world", "worlds", self.world), ("sun object", "objects", self.sun_object),
                                  ("sun data", "lights", self.sun_data)):
            if item is None:
                continue
            try:
                getattr(bpy.data, attr).remove(item)
            except Exception as exc:
                failures.append(f"auto-light {label}: {exc}")
        return failures


def _fill_world(bpy, state: AutoLightState):
    world = bpy.data.worlds.new(WORLD_NAME)
    state.world = world
    world.use_nodes = True
    background = next((n for n in world.node_tree.nodes if n.type == "BACKGROUND"), None)
    if background is not None:
        background.inputs["Color"].default_value = (*FILL_RGB, 1.0)
        background.inputs["Strength"].default_value = FILL_STRENGTH
    return world


def apply(bpy, scene, state: AutoLightState) -> dict:
    """Add the temporary Sun (and the world fill when `scene.world` is None). Returns the `auto_light` result."""
    data = bpy.data.lights.new(SUN_NAME, "SUN")
    state.sun_data = data
    data.energy = SUN_ENERGY
    sun = bpy.data.objects.new(SUN_NAME, data)
    state.sun_object = sun
    scene.collection.objects.link(sun)
    sun.rotation_euler = tuple(math.radians(d) for d in SUN_ROTATION_DEG)

    fill = "none (world kept)"
    if scene.world is None:
        world = _fill_world(bpy, state)
        state.previous_world = scene.world
        state.world_set = True
        scene.world = world
        fill = "world created"
    bpy.context.view_layer.update()
    return {"applied": True, "sun": sun.name, "fill": fill}
