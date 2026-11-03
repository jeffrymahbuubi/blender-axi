"""blender_side.auto_light with plain stubs (0.6.0 plan U2): apply, undo, best effort."""

import math
from types import SimpleNamespace as NS

import pytest

from blender_axi.blender_side import auto_light


class Inputs(dict):
    pass


class Registry:
    """A bpy.data collection stub: `new` records, `remove` records (or raises)."""

    def __init__(self, factory, fail=False):
        self.factory, self.fail, self.items, self.removed = factory, fail, [], []

    def new(self, *args):
        item = self.factory(*args)
        self.items.append(item)
        return item

    def remove(self, item):
        if self.fail:
            raise RuntimeError("cannot remove")
        self.removed.append(item)


class Linked(list):
    def link(self, obj):
        self.append(obj)


def make(world=None, fail=()):
    bg = NS(type="BACKGROUND", inputs=Inputs(Color=NS(default_value=(0.05, 0.05, 0.05, 1.0)),
                                             Strength=NS(default_value=1.0)))
    data = NS(
        lights=Registry(lambda name, kind: NS(name=name, type=kind, energy=1.0), "lights" in fail),
        objects=Registry(lambda name, d: NS(name=name, data=d, rotation_euler=(0, 0, 0)), "objects" in fail),
        worlds=Registry(lambda name: NS(name=name, use_nodes=False, node_tree=NS(nodes=[bg])), "worlds" in fail),
    )
    updates = []
    bpy = NS(data=data, context=NS(view_layer=NS(update=lambda: updates.append(1))))
    scene = NS(world=world, collection=NS(objects=Linked()))
    return bpy, scene, bg, updates


def test_apply_creates_and_links_the_sun_with_radian_rotation():
    bpy, scene, _, updates = make()
    state = auto_light.AutoLightState()
    res = auto_light.apply(bpy, scene, state)
    sun = scene.collection.objects[0]
    assert sun.name == "AxiSun" and sun.data.type == "SUN" and sun.data.energy == auto_light.SUN_ENERGY == 3.0
    assert sun.rotation_euler == pytest.approx(tuple(math.radians(d) for d in (45.0, 0.0, 45.0)))
    assert res == {"applied": True, "sun": "AxiSun", "fill": "world created"} and updates == [1]


def test_world_is_created_only_when_the_scene_has_none():
    bpy, scene, bg, _ = make(world=None)
    state = auto_light.AutoLightState()
    auto_light.apply(bpy, scene, state)
    world = scene.world
    assert world is state.world and world.name == "AxiFill" and world.use_nodes is True
    assert bg.inputs["Color"].default_value == (0.3, 0.3, 0.3, 1.0) and bg.inputs["Strength"].default_value == 0.5
    assert state.world_set and state.previous_world is None


def test_an_existing_world_is_kept_untouched():
    mine = NS(name="World")
    bpy, scene, bg, _ = make(world=mine)
    state = auto_light.AutoLightState()
    res = auto_light.apply(bpy, scene, state)
    assert scene.world is mine and bpy.data.worlds.items == [] and not state.world_set
    assert res["fill"] == "none (world kept)" and bg.inputs["Strength"].default_value == 1.0
    assert auto_light.AutoLightState().undo(bpy, scene) == [] and scene.world is mine


def test_undo_restores_world_none_and_removes_everything():
    bpy, scene, _, _ = make(world=None)
    state = auto_light.AutoLightState()
    auto_light.apply(bpy, scene, state)
    sun, data, world = state.sun_object, state.sun_data, state.world
    assert state.undo(bpy, scene) == []
    assert scene.world is None
    assert bpy.data.objects.removed == [sun] and bpy.data.lights.removed == [data] and bpy.data.worlds.removed == [world]


def test_undo_restores_a_previous_world_pointer():
    bpy, scene, _, _ = make(world=None)
    state = auto_light.AutoLightState()
    state.world_set, state.previous_world, state.world = True, NS(name="Old"), NS(name="AxiFill")
    scene.world = state.world
    assert state.undo(bpy, scene) == [] and scene.world.name == "Old"


def test_undo_is_best_effort_and_reports_each_failure():
    bpy, scene, _, _ = make(fail=("lights", "objects", "worlds"))
    state = auto_light.AutoLightState()
    auto_light.apply(bpy, scene, state)
    failures = state.undo(bpy, scene)
    assert scene.world is None  # the pointer is restored even though the removals fail
    assert len(failures) == 3 and all("cannot remove" in f for f in failures)
    assert any("sun object" in f for f in failures) and any("world" in f for f in failures)


def test_a_failure_midway_is_still_undone():
    bpy, scene, _, _ = make()
    scene.collection.objects.link = lambda obj: (_ for _ in ()).throw(RuntimeError("link failed"))
    state = auto_light.AutoLightState()
    with pytest.raises(RuntimeError, match="link failed"):
        auto_light.apply(bpy, scene, state)
    assert state.undo(bpy, scene) == []
    assert bpy.data.lights.removed == [state.sun_data] and bpy.data.objects.removed == [state.sun_object]
    assert scene.world is None


def test_undo_of_an_unused_state_touches_nothing():
    bpy = NS(data=NS())  # no collections at all: nothing is looked up
    assert auto_light.AutoLightState().undo(bpy, NS(world=None)) == []
