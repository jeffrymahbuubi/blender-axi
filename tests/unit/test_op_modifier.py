"""v1.3 U2: `op_modifier` (fake `bpy`): create / upsert / remove, change detection, `--prop` coercion, errors."""

import math
import types

import pytest

from blender_axi.blender_side import op_modifier
from blender_axi.blender_side.ops import OpError

# -- op_modifier (fake bpy) -------------------------------------------------------------------------------------------

class Prop:
    def __init__(self, kind, array_length=0, items=(), pointer=None, readonly=False):
        self.type, self.array_length, self.is_readonly = kind, array_length, readonly
        self.enum_items = [types.SimpleNamespace(identifier=i) for i in items]
        self.fixed_type = types.SimpleNamespace(identifier=pointer) if pointer else None


class Props(dict):
    pass


SCHEMA = {
    "BEVEL": ({"width": 0.1, "segments": 1, "limit_method": "ANGLE", "angle_limit": math.radians(30),
               "name": "", "type": "BEVEL"},
              {"width": Prop("FLOAT"), "segments": Prop("INT"), "limit_method": Prop("ENUM", items=("NONE", "ANGLE", "WEIGHT")),
               "angle_limit": Prop("FLOAT"), "name": Prop("STRING"), "type": Prop("ENUM", readonly=True)}),
    "SUBSURF": ({"levels": 1, "render_levels": 2, "subdivision_type": "CATMULL_CLARK"},
                {"levels": Prop("INT"), "render_levels": Prop("INT"),
                 "subdivision_type": Prop("ENUM", items=("CATMULL_CLARK", "SIMPLE"))}),
    "ARRAY": ({"count": 2, "relative_offset_displace": (1.0, 0.0, 0.0), "use_relative_offset": True,
               "use_constant_offset": False, "constant_offset_displace": (1.0, 0.0, 0.0)},
              {"count": Prop("INT"), "relative_offset_displace": Prop("FLOAT", 3), "use_relative_offset": Prop("BOOLEAN"),
               "use_constant_offset": Prop("BOOLEAN"), "constant_offset_displace": Prop("FLOAT", 3)}),
    "MIRROR": ({"use_axis": (True, False, False), "use_clip": False, "mirror_object": None},
               {"use_axis": Prop("BOOLEAN", 3), "use_clip": Prop("BOOLEAN"), "mirror_object": Prop("POINTER", pointer="Object")}),
    "BOOLEAN": ({"object": None, "operation": "DIFFERENCE", "solver": "EXACT", "collection": None},
                {"object": Prop("POINTER", pointer="Object"), "operation": Prop("ENUM", items=("DIFFERENCE", "UNION", "INTERSECT")),
                 "solver": Prop("ENUM", items=("EXACT", "FLOAT")), "collection": Prop("POINTER", pointer="Collection")}),
    "SOLIDIFY": ({"thickness": 0.01, "offset": -1.0, "use_even_offset": False},
                 {"thickness": Prop("FLOAT"), "offset": Prop("FLOAT"), "use_even_offset": Prop("BOOLEAN")}),
    "WIREFRAME": ({"thickness": 0.02, "use_replace": True, "scale_vec": (1.0, 1.0)},
                  {"thickness": Prop("FLOAT"), "use_replace": Prop("BOOLEAN"), "scale_vec": Prop("FLOAT", 2),
                   "label": Prop("STRING"), "limit": Prop("INT")}),
}


def make_modifier(name, kind):
    values, props = SCHEMA[kind]
    mod = types.SimpleNamespace(name=name, type=kind, bl_rna=types.SimpleNamespace(properties=Props(props)))
    for key, value in values.items():
        if key not in ("name", "type"):
            setattr(mod, key, value)
    return mod


class Modifiers(list):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def new(self, name, kind):
        if kind not in SCHEMA:
            raise TypeError(f'enum "{kind}" not found in (...)')
        if self.owner.type in ("CAMERA", "LIGHT", "EMPTY"):
            return None
        mod = make_modifier(name, kind)
        self.append(mod)
        return mod

    def get(self, name, default=None):
        return next((m for m in self if m.name == name), default)


def make_object(name="Cube", kind="MESH", with_modifiers=True):
    obj = types.SimpleNamespace(name=name, type=kind)
    if with_modifiers:
        obj.modifiers = Modifiers(obj)
    return obj


@pytest.fixture
def world(fake_bpy):
    cube, cutter = make_object("Cube"), make_object("Cutter")
    fake_bpy.data = types.SimpleNamespace(objects=[cube, cutter, make_object("Camera", "CAMERA")], collections=[])
    return types.SimpleNamespace(bpy=fake_bpy, cube=cube, cutter=cutter)


def modifier_op(**params):
    base = {"object": "Cube", "kind": "BEVEL", "name": None, "remove": False, "settings": {}, "props": {}}
    return op_modifier.run({**base, **params})


def test_op_creates_with_the_default_name_and_reports_changed(world):
    r = modifier_op(settings={"width": 0.05, "segments": 3})
    assert r["created"] is True and r["removed"] is False and r["modifier"] == "Bevel" and r["type"] == "BEVEL"
    assert r["changed"] == [{"field": "width", "from": 0.1, "to": 0.05}, {"field": "segments", "from": 1, "to": 3}]
    assert r["stack"] == ["Bevel"] and world.cube.modifiers[0].width == 0.05


@pytest.mark.parametrize("kind,name", [("BEVEL", "Bevel"), ("SUBSURF", "Subdivision"), ("ARRAY", "Array"),
                                       ("MIRROR", "Mirror"), ("SOLIDIFY", "Solidify"), ("WIREFRAME", "Wireframe"),
                                       ("SIMPLE_DEFORM", "SimpleDeform")])
def test_default_names(kind, name):
    assert op_modifier.default_name(kind) == name


def test_op_create_without_settings_has_no_changes(world):
    r = modifier_op(kind="SUBSURF")
    assert r["created"] is True and r["changed"] == [] and r["modifier"] == "Subdivision"


def test_op_upsert_is_a_no_op_the_second_time(world):
    modifier_op(settings={"width": 0.05})
    r = modifier_op(settings={"width": 0.05})
    assert r["created"] is False and r["changed"] == [] and len(world.cube.modifiers) == 1


def test_op_upsert_changes_only_what_differs(world):
    modifier_op(settings={"width": 0.05, "segments": 3})
    r = modifier_op(settings={"width": 0.2, "segments": 3})
    assert r["created"] is False and r["changed"] == [{"field": "width", "from": 0.05, "to": 0.2}]


def test_op_custom_name_and_two_modifiers(world):
    modifier_op(name="Rim", settings={"width": 0.5})
    r = modifier_op(kind="SUBSURF")
    assert r["stack"] == ["Rim", "Subdivision"]


def test_op_type_mismatch_is_op_failed(world):
    modifier_op(kind="SUBSURF", name="Bevel")
    with pytest.raises(OpError) as exc:
        modifier_op(kind="BEVEL")
    assert exc.value.code == "OP_FAILED" and exc.value.message == "'Bevel' is a SUBSURF, not BEVEL"


def test_op_angle_is_reported_in_degrees(world):
    modifier_op(settings={"width": 0.1})
    r = modifier_op(settings={"angle_limit": math.radians(45)})
    assert r["changed"] == [{"field": "angle_limit", "from": 30.0, "to": 45.0}]


def test_op_array_vectors_and_flags(world):
    r = modifier_op(kind="ARRAY", settings={"constant_offset_displace": [0.0, 3.0, 0.0], "use_constant_offset": True,
                                           "use_relative_offset": False})
    fields = {c["field"]: c for c in r["changed"]}
    assert fields["constant_offset_displace"] == {"field": "constant_offset_displace", "from": [1.0, 0.0, 0.0],
                                                  "to": [0.0, 3.0, 0.0]}
    assert fields["use_constant_offset"]["to"] is True and fields["use_relative_offset"]["to"] is False


def test_op_mirror_use_axis_bools(world):
    r = modifier_op(kind="MIRROR", settings={"use_axis": [True, True, False]})
    assert r["changed"] == [{"field": "use_axis", "from": [True, False, False], "to": [True, True, False]}]
    assert world.cube.modifiers[0].use_axis == (True, True, False)


def test_op_mirror_object_is_looked_up(world):
    r = modifier_op(kind="MIRROR", settings={"mirror_object": "Cutter"})
    assert r["changed"] == [{"field": "mirror_object", "from": None, "to": "Cutter"}]
    assert world.cube.modifiers[0].mirror_object is world.cutter


def test_op_cutter_not_found_leaves_the_object_untouched(world):
    with pytest.raises(OpError) as exc:
        modifier_op(kind="BOOLEAN", settings={"object": "Cuter"})
    assert exc.value.code == "NOT_FOUND" and "cutter 'Cuter' not found" in exc.value.message
    assert "Cutter" in exc.value.message and len(world.cube.modifiers) == 0


def test_op_boolean_with_object(world):
    r = modifier_op(kind="BOOLEAN", settings={"object": "Cutter", "operation": "UNION"})
    assert r["created"] is True and r["modifier"] == "Boolean"
    assert {c["field"]: c["to"] for c in r["changed"]} == {"object": "Cutter", "operation": "UNION"}
    assert world.cube.modifiers[0].object is world.cutter


def test_op_boolean_created_without_object_is_op_failed(world):
    with pytest.raises(OpError) as exc:
        modifier_op(kind="BOOLEAN", settings={"operation": "UNION"})
    assert exc.value.code == "OP_FAILED" and exc.value.message == "boolean needs --object <cutter>"
    assert len(world.cube.modifiers) == 0


def test_op_boolean_cutter_via_prop_is_enough(world):
    r = modifier_op(kind="BOOLEAN", props={"object": "Cutter"})
    assert r["created"] is True and world.cube.modifiers[0].object is world.cutter


def test_op_existing_boolean_needs_no_object(world):
    modifier_op(kind="BOOLEAN", settings={"object": "Cutter"})
    r = modifier_op(kind="BOOLEAN", settings={"operation": "UNION"})
    assert r["created"] is False and r["changed"] == [{"field": "operation", "from": "DIFFERENCE", "to": "UNION"}]


def test_op_remove(world):
    modifier_op(settings={"width": 0.05})
    r = modifier_op(remove=True)
    assert r["removed"] is True and r["created"] is False and r["stack"] == [] and len(world.cube.modifiers) == 0


def test_op_remove_missing_is_a_no_op(world):
    r = modifier_op(remove=True)
    assert r["removed"] is False and r["stack"] == []


def test_op_remove_wrong_kind_is_op_failed(world):
    modifier_op(kind="SUBSURF", name="Bevel")
    with pytest.raises(OpError) as exc:
        modifier_op(remove=True)
    assert exc.value.code == "OP_FAILED" and len(world.cube.modifiers) == 1


def test_op_camera_cannot_take_modifiers(world):
    with pytest.raises(OpError) as exc:
        modifier_op(object="Camera")
    assert exc.value.code == "OP_FAILED" and exc.value.message == "'Camera' (CAMERA) cannot take modifiers"


def test_op_object_without_a_modifiers_attribute(world):
    world.bpy.data.objects.append(make_object("Odd", "ARMATURE", with_modifiers=False))
    with pytest.raises(OpError) as exc:
        modifier_op(object="Odd")
    assert exc.value.message == "'Odd' (ARMATURE) cannot take modifiers"


def test_op_unknown_object_is_not_found_with_a_suggestion(world):
    with pytest.raises(OpError) as exc:
        modifier_op(object="Cub")
    assert exc.value.code == "NOT_FOUND" and "did you mean: Cube" in exc.value.message


def test_op_unknown_modifier_type_is_op_failed(world):  # A4: Blender raises TypeError for an unknown identifier
    with pytest.raises(OpError) as exc:
        modifier_op(kind="NOPE")
    assert exc.value.code == "OP_FAILED" and exc.value.message == "unknown modifier type 'NOPE'"


# -- --prop coercion -----------------------------------------------------------------------------------------------

def test_prop_float_bool_and_array(world):
    r = modifier_op(kind="WIREFRAME", props={"thickness": "0.05", "use_replace": "false", "scale_vec": "2,3"})
    got = {c["field"]: c["to"] for c in r["changed"]}
    assert got == {"thickness": 0.05, "use_replace": False, "scale_vec": [2.0, 3.0]}
    assert world.cube.modifiers[0].scale_vec == (2.0, 3.0)


def test_prop_int_enum_and_case_insensitive_enum(world):
    r = modifier_op(props={"segments": "4", "limit_method": "weight"})
    assert {c["field"]: c["to"] for c in r["changed"]} == {"segments": 4, "limit_method": "WEIGHT"}


def test_prop_string(world):
    r = modifier_op(kind="WIREFRAME", props={"label": "x"})
    assert world.cube.modifiers[0].label == "x" and r["changed"] == [{"field": "label", "from": None, "to": "x"}]


def test_prop_pointer_to_object_and_none(world):
    modifier_op(kind="BOOLEAN", settings={"object": "Cutter"})
    r = modifier_op(kind="BOOLEAN", props={"object": "none"})
    assert r["changed"] == [{"field": "object", "from": "Cutter", "to": None}]


def test_prop_pointer_object_not_found(world):
    with pytest.raises(OpError) as exc:
        modifier_op(kind="BOOLEAN", settings={"object": "Cutter"}, props={"object": "Missing"})
    assert exc.value.code == "NOT_FOUND" and len(world.cube.modifiers) == 0


def test_prop_pointer_to_collection(world):
    world.bpy.data.collections = [types.SimpleNamespace(name="Cutters")]
    modifier_op(kind="BOOLEAN", settings={"object": "Cutter"}, props={"collection": "Cutters"})
    assert world.cube.modifiers[0].collection.name == "Cutters"


@pytest.mark.parametrize("props,message", [
    ({"nope": "1"}, "unknown property 'nope' for BEVEL"),
    ({"type": "X"}, "property 'type' of BEVEL is read-only"),
    ({"limit_method": "SIDEWAYS"}, "invalid value 'SIDEWAYS' for 'limit_method'; one of NONE, ANGLE, WEIGHT"),
    ({"segments": "many"}, "invalid value 'many' for 'segments'; expected an integer"),
    ({"width": "wide"}, "invalid value 'wide' for 'width'; expected a number"),
])
def test_prop_errors_are_op_failed_and_remove_a_just_created_modifier(world, props, message):
    with pytest.raises(OpError) as exc:
        modifier_op(props=props)
    assert exc.value.code == "OP_FAILED" and exc.value.message == message
    assert len(world.cube.modifiers) == 0


def test_prop_array_length_and_boolean_errors(world):
    with pytest.raises(OpError) as exc:
        modifier_op(kind="WIREFRAME", props={"scale_vec": "1,2,3"})
    assert exc.value.message == "'scale_vec' expects 2 comma-separated values, got '1,2,3'"
    with pytest.raises(OpError) as exc:
        modifier_op(kind="WIREFRAME", props={"use_replace": "maybe"})
    assert exc.value.message == "invalid value 'maybe' for 'use_replace'; expected true or false"


def test_prop_unsettable_pointer_type(world):
    SCHEMA["WIREFRAME"][1]["texture"] = Prop("POINTER", pointer="Texture")
    try:
        with pytest.raises(OpError) as exc:
            modifier_op(kind="WIREFRAME", props={"texture": "T"})
    finally:
        del SCHEMA["WIREFRAME"][1]["texture"]
    assert exc.value.message == "'texture' (Texture) cannot be set from a name"
