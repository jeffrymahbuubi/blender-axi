"""Unit tests for the pure snapshot comparison behind `diff` (no Blender)."""

from __future__ import annotations

import copy

from blender_axi.commands import _diff


def mod(name="Bevel", kind="BEVEL", **settings):
    return {"name": name, "type": kind, "settings": settings}


def obj(name, kind="MESH", **kw):
    base = {"name": name, "type": kind, "collections": ["Collection"], "parent": None, "location": [0.0, 0.0, 0.0],
            "rotation_deg": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0], "dimensions": [2.0, 2.0, 2.0], "visible": True,
            "hide_render": False, "data": name + "Mesh", "verts": 8, "faces": 6, "materials": ["Mat"], "modifiers": [],
            "custom_props": {}}
    base.update(kw)
    return base


def snap(*objects, **scene):
    s = {"name": "Scene", "engine": "BLENDER_EEVEE", "resolution": [1920, 1080, 100], "frames": [1, 250], "fps": 24.0,
         "camera": "Cam", "world": "World"}
    s.update(scene)
    return {"filepath": "x.blend", "blender_version": "5.2.0", "scene": s, "objects": list(objects)}


def one_change(before: dict, after: dict) -> str:
    res = _diff.compare(snap(before), snap(after))
    assert len(res["changed"]) == 1 and not res["added"] and not res["removed"] and res["unchanged"] == 0
    return res["changed"][0]["changes"]


def test_identical_snapshots():
    a = snap(obj("A"), obj("B"))
    res = _diff.compare(a, copy.deepcopy(a))
    assert res == {"added": [], "removed": [], "changed": [], "scene": [], "unchanged": 2}


def test_added_removed_changed_unchanged_counts():
    a = snap(obj("A"), obj("B", "EMPTY"), obj("Keep"))
    b = snap(obj("A", location=[1.0, 0.0, 0.0]), obj("C", "LIGHT"), obj("Keep"))
    res = _diff.compare(a, b)
    assert res["added"] == [{"name": "C", "type": "LIGHT"}]
    assert res["removed"] == [{"name": "B", "type": "EMPTY"}]
    assert res["changed"] == [{"name": "A", "changes": "location 0,0,0 -> 1,0,0"}]
    assert res["unchanged"] == 1 and res["scene"] == []


def test_every_field_label_and_order():
    before = obj("A")
    after = obj("A", kind="EMPTY", parent="P", collections=["Collection", "Props"], location=[1, 2, 3],
                rotation_deg=[0, 0, 90], scale=[2, 2, 2], dimensions=[4, 4, 4], visible=False, hide_render=True,
                data="Other", verts=9, faces=7, materials=["Red", "Blue"],
                modifiers=[mod("Sub", "SUBSURF")], custom_props={"k": 2})
    text = one_change(before, after)
    assert text.split("; ") == [
        "type MESH -> EMPTY", "parent none -> P", "collections Collection -> Collection|Props",
        "location 0,0,0 -> 1,2,3", "rotation 0,0,0 -> 0,0,90", "scale 1,1,1 -> 2,2,2", "dimensions 2,2,2 -> 4,4,4",
        "visible true -> false", "hide_render false -> true", "data AMesh -> Other", "verts 8 -> 9", "faces 6 -> 7",
        "materials Mat -> Red|Blue", "+Sub(SUBSURF)", "custom_props.k none -> 2"]


def test_vectors_use_the_tolerance():
    near = obj("A", location=[0.0005, 0.0, 0.0])
    assert _diff.compare(snap(obj("A")), snap(near))["unchanged"] == 1
    far = obj("A", location=[0.002, 0.0, 0.0])
    assert one_change(obj("A"), far) == "location 0,0,0 -> 0.002,0,0"
    assert _diff.compare(snap(obj("A")), snap(far), tol=0.01)["unchanged"] == 1


def test_material_removed_shows_none():
    assert one_change(obj("A"), obj("A", materials=[])) == "materials Mat -> none"


def test_modifier_added_removed_setting_and_type_changes():
    a = obj("A", modifiers=[mod("Bevel", width=0.05, segments=1), mod("Old", "MIRROR")])
    b = obj("A", modifiers=[mod("Bevel", width=0.1, segments=1), mod("New", "ARRAY")])
    assert one_change(a, b).split("; ") == ["-Old(MIRROR)", "+New(ARRAY)", "Bevel.width 0.05 -> 0.1"]
    c = obj("A", modifiers=[mod("Bevel", "WEIGHTED_NORMAL", width=0.05, segments=1)])
    assert one_change(obj("A", modifiers=[mod("Bevel", width=0.05, segments=1)]), c) == "Bevel.type BEVEL -> WEIGHTED_NORMAL"


def test_modifier_float_tolerance_and_new_setting():
    a = obj("A", modifiers=[mod(width=0.05)])
    assert _diff.compare(snap(a), snap(obj("A", modifiers=[mod(width=0.0504)])))["unchanged"] == 1
    assert one_change(a, obj("A", modifiers=[mod(width=0.05, extra=True)])) == "Bevel.extra none -> true"


def test_modifier_reorder():
    a = obj("A", modifiers=[mod("One", "MIRROR"), mod("Two", "ARRAY")])
    b = obj("A", modifiers=[mod("Two", "ARRAY"), mod("One", "MIRROR")])
    assert one_change(a, b) == "modifiers reordered"


def test_custom_props_changed_and_removed():
    assert one_change(obj("A", custom_props={"k": 1, "z": "a"}), obj("A", custom_props={"k": 2})) == \
        "custom_props.k 1 -> 2; custom_props.z a -> none"


def test_long_values_are_cut():
    text = one_change(obj("A", custom_props={"k": "x" * 100}), obj("A", custom_props={"k": "y"}))
    assert text.endswith("-> y") and "..." in text and len(text) < 80


def test_scene_settings():
    a = snap()
    b = snap(engine="CYCLES", resolution=[320, 240, 50], frames=[1, 10], fps=30.0, camera=None, world=None)
    rows = {r["setting"]: (r["from"], r["to"]) for r in _diff.compare(a, b)["scene"]}
    assert rows == {"engine": ("EEVEE", "CYCLES"), "resolution": ("1920x1080@100%", "320x240@50%"),
                    "frames": ("1-250", "1-10"), "fps": ("24", "30"), "camera": ("Cam", "none"), "world": ("World", "none")}
    assert list(rows) == ["engine", "resolution", "frames", "fps", "camera", "world"]


def test_eevee_next_equals_eevee():
    assert _diff.compare(snap(engine="BLENDER_EEVEE"), snap(engine="BLENDER_EEVEE_NEXT"))["scene"] == []


def test_no_rename_detection():
    res = _diff.compare(snap(obj("Old")), snap(obj("New")))
    assert [r["name"] for r in res["removed"]] == ["Old"] and [r["name"] for r in res["added"]] == ["New"]


def test_restrict_by_object_and_collection():
    a = snap(obj("A"), obj("B", collections=["Props"]), obj("C"))
    b = snap(obj("A"), obj("B", collections=["Other"]), obj("D", collections=["Props"]))
    ra, rb, missing = _diff.restrict(a, b, ["A", "B"], None)
    assert [o["name"] for o in ra["objects"]] == ["A", "B"] and [o["name"] for o in rb["objects"]] == ["A", "B"]
    assert missing == []
    ra, rb, _ = _diff.restrict(a, b, None, "Props")  # a name in the collection in EITHER file stays in both
    assert [o["name"] for o in ra["objects"]] == ["B"] and [o["name"] for o in rb["objects"]] == ["B", "D"]
    _, _, missing = _diff.restrict(a, b, ["A", "Ghost"], None)
    assert missing == ["Ghost"]
    same = _diff.restrict(a, b, None, None)
    assert same[0] is a and same[1] is b
