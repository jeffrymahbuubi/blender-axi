"""blender_side.lighting with plain stubs (0.6.0 plan §3.1)."""

from types import SimpleNamespace as NS

from blender_axi.blender_side import lighting


class Inputs(dict):
    pass


def socket(value):
    return NS(default_value=value)


def light(name="Sun", energy=3.0, visible=True, hide_render=False):
    return NS(name=name, type="LIGHT", data=NS(energy=energy), hide_render=hide_render,
              visible_get=lambda: visible)


def node_world(color=(0.05, 0.05, 0.05, 1.0), strength=1.0, name="World"):
    bg = NS(type="BACKGROUND", inputs=Inputs(Color=socket(color), Strength=socket(strength)))
    return NS(name=name, use_nodes=True, node_tree=NS(nodes=[bg]))


def principled(strength=0.0, color=(1, 1, 1, 1), name="Mat"):
    node = NS(type="BSDF_PRINCIPLED", inputs=Inputs({"Emission Strength": socket(strength),
                                                     "Emission Color": socket(color)}))
    return NS(name=name, use_nodes=True, node_tree=NS(nodes=[node]))


def make(objects=(), world=None, materials=(), engine="BLENDER_EEVEE"):
    scene = NS(objects=list(objects), world=world, render=NS(engine=engine))
    bpy = NS(data=NS(materials=list(materials)))
    return bpy, scene


def test_empty_eevee_scene_is_unlit():
    a = lighting.assess(*make())
    assert a["unlit"] and a["world"] == "none" and a["needs_light"]


def test_workbench_never_unlit():
    assert not lighting.assess(*make(engine="BLENDER_WORKBENCH"))["unlit"]


def test_engine_override():
    bpy, scene = make(engine="BLENDER_WORKBENCH")
    assert lighting.assess(bpy, scene, engine="CYCLES")["unlit"]


def test_sun_lights_the_scene():
    a = lighting.assess(*make(objects=[light()]))
    assert not a["unlit"] and a["lights"] == ["Sun"]


def test_zero_energy_hidden_and_render_disabled_lights_do_not_count():
    assert lighting.assess(*make(objects=[light(energy=0)]))["unlit"]
    assert lighting.assess(*make(objects=[light(hide_render=True)]))["unlit"]
    assert lighting.assess(*make(objects=[light(visible=False)]))["unlit"]


def test_world_strength_zero_or_black_is_black():
    assert lighting.assess(*make(world=node_world(strength=0)))["world"] == "black"
    assert lighting.assess(*make(world=node_world(color=(0, 0, 0, 1))))["world"] == "black"


def test_grey_world_lights_the_scene():
    a = lighting.assess(*make(world=node_world()))
    assert a["world"] == "World" and not a["unlit"]


def test_world_without_nodes_uses_color():
    w = NS(name="Flat", use_nodes=False, node_tree=None, color=(0, 0, 0))
    assert lighting.assess(*make(world=w))["world"] == "black"
    w.color = (0.2, 0.2, 0.2)
    assert lighting.assess(*make(world=w))["world"] == "Flat"


def test_emissive_principled_material_lights_the_scene():
    a = lighting.assess(*make(materials=[principled(strength=1.0)]))
    assert a["emissive"] == ["Mat"] and not a["unlit"]
    assert lighting.assess(*make(materials=[principled(strength=0.0)]))["unlit"]


def test_warning_text():
    assert lighting.warning(lighting.assess(*make()), "EEVEE") == lighting.UNLIT_TEXT.format(engine="EEVEE")
    assert lighting.warning(lighting.assess(*make(objects=[light()])), "EEVEE") is None
