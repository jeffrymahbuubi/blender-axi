"""Is a scene lit? Shared by `scene`, `render` and `audit` (0.6.0 plan §3.1, K2).

Data API only; `bpy` is passed in so the unit tests can drive it with plain stubs.
"""

NEEDS_LIGHT = {"BLENDER_EEVEE", "BLENDER_EEVEE_NEXT", "CYCLES"}
UNLIT_TEXT = "unlit scene: no light, no world background and no emissive material; {engine} renders black"


def _rgb_max(value) -> float:
    return max(list(value)[:3])


def _lights(scene) -> list[str]:
    return [
        obj.name for obj in scene.objects
        if obj.type == "LIGHT" and obj.visible_get() and not obj.hide_render and obj.data.energy > 0
    ]


def _world(scene) -> str:
    world = scene.world
    if world is None:
        return "none"
    if getattr(world, "use_nodes", False) and world.node_tree is not None:
        for node in world.node_tree.nodes:
            if node.type == "BACKGROUND":
                color = node.inputs["Color"].default_value
                strength = node.inputs["Strength"].default_value
                return "black" if _rgb_max(color) == 0 or strength == 0 else world.name
        return "black"
    return "black" if _rgb_max(world.color) == 0 else world.name


def _emits(node) -> bool:
    if node.type == "BSDF_PRINCIPLED":
        strength, color = node.inputs["Emission Strength"], node.inputs["Emission Color"]
    elif node.type == "EMISSION":
        strength, color = node.inputs["Strength"], node.inputs["Color"]
    else:
        return False
    return strength.default_value > 0 and _rgb_max(color.default_value) > 0


def _emissive(bpy) -> list[str]:
    return [
        mat.name for mat in bpy.data.materials
        if getattr(mat, "use_nodes", False) and mat.node_tree is not None
        and any(_emits(node) for node in mat.node_tree.nodes)
    ]


def assess(bpy, scene, engine: str | None = None) -> dict:
    """{"engine", "needs_light", "lights", "world": "none"|"black"|<name>, "emissive", "unlit"}."""
    engine = engine or scene.render.engine
    needs_light = engine in NEEDS_LIGHT
    lights, world, emissive = _lights(scene), _world(scene), _emissive(bpy)
    unlit = needs_light and not lights and world in ("none", "black") and not emissive
    return {"engine": engine, "needs_light": needs_light, "lights": lights, "world": world,
            "emissive": emissive, "unlit": unlit}


def warning(assessment: dict, engine_label: str) -> str | None:
    """UNLIT_TEXT for an unlit scene, else None (engine_label = EEVEE|CYCLES as the CLI shows it)."""
    return UNLIT_TEXT.format(engine=engine_label) if assessment["unlit"] else None
