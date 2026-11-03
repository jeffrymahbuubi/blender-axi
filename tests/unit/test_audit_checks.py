"""`audit_checks` and `op_audit` with plain stubs for bpy and bmesh (0.6.0 plan §3.4)."""

from __future__ import annotations

import sys
from types import SimpleNamespace as NS

import pytest

from blender_axi.blender_side import audit_checks as ac
from blender_axi.blender_side import op_audit
from blender_axi.blender_side.ops import OpError

MAT = NS(name="Mat")


def mesh_data(faces=(4, 4), manifold=(True,) * 12, loose=(False,) * 8):
    return NS(polygons=[NS(loop_total=n) for n in faces], manifold=list(manifold), loose=list(loose))


def obj(name, type_="MESH", scale=(1, 1, 1), mats=(MAT,), data=None, visible=True, hide_render=False,
        hide_viewport=False):
    return NS(name=name, type=type_, scale=scale, material_slots=[NS(material=m) for m in mats],
              data=data if data is not None else mesh_data(), hide_render=hide_render, hide_viewport=hide_viewport,
              visible_get=lambda: visible)


def light(energy=3.0):
    return NS(name="Sun", type="LIGHT", data=NS(energy=energy), hide_render=False, hide_viewport=False,
              visible_get=lambda: True, scale=(1, 1, 1), material_slots=[])


class Coll(NS):
    def __init__(self, name, objects=(), children=()):
        super().__init__(name=name, objects=list(objects), children=list(children), all_objects=list(objects))


def make(objects=(), camera=True, **data):
    objs = list(objects)
    scene = NS(objects=objs, camera=NS(name="Cam") if camera else None, world=None,
               render=NS(engine="BLENDER_WORKBENCH"), collection=NS(name="Scene Collection"))
    d = dict(objects=objs, collections=[], meshes=[], materials=[], images=[], textures=[], worlds=[], cameras=[],
             lights=[], actions=[], node_groups=[], libraries=[], filepath="x.blend")
    d.update(data)
    bpy = NS(data=NS(**d), context=NS(scene=scene), path=NS(abspath=lambda p: p.replace("//", "/nowhere/")))
    return bpy, scene


class FakeBM:
    created = 0

    def __init__(self):
        FakeBM.created += 1
        self.edges, self.verts, self.freed = [], [], False

    def from_mesh(self, mesh):
        self.edges = [NS(is_manifold=m) for m in mesh.manifold]
        self.verts = [NS(link_edges=[] if lone else [1]) for lone in mesh.loose]

    def free(self):
        self.freed = True


@pytest.fixture
def bmesh_stub(monkeypatch):
    FakeBM.created = 0
    monkeypatch.setitem(sys.modules, "bmesh", NS(new=FakeBM))
    return FakeBM


def run_op(monkeypatch, bpy, **params):
    monkeypatch.setitem(sys.modules, "bpy", bpy)
    return op_audit.run(params)


def one(check_id, objects=(), **kw):
    bpy, scene = make(objects, **kw)
    return ac.run_checks(bpy, scene, [check_id], list(scene.objects))[check_id]


# -- registry --------------------------------------------------------------------------------------

def test_registry_levels_scopes_and_order():
    assert list(ac.CHECKS) == list(ac.CHECK_IDS)
    warn = {"camera", "lighting", "scale", "materials", "nonmanifold", "files"}
    assert {k for k, c in ac.CHECKS.items() if c.level == "warn"} == warn
    assert {k for k, c in ac.CHECKS.items() if c.level == "info"} == set(ac.CHECK_IDS) - warn
    assert ac.SCENE_IDS == {"camera", "lighting", "unused", "files", "collections"}


# -- scene checks ----------------------------------------------------------------------------------

def test_camera():
    assert one("camera", camera=False) == ["no scene camera"]
    assert one("camera") == []


def test_lighting_unlit_and_lit():
    bpy, scene = make()
    scene.render.engine = "BLENDER_EEVEE"
    items = ac.run_checks(bpy, scene, ["lighting"], [])["lighting"]
    assert items == ["unlit scene: no light, no world background and no emissive material; EEVEE renders black"]
    scene.objects.append(light())
    assert ac.run_checks(bpy, scene, ["lighting"], [])["lighting"] == []
    bpy, scene = make()  # WORKBENCH never needs a light
    assert ac.run_checks(bpy, scene, ["lighting"], [])["lighting"] == []


def test_unused_type_words():
    zero, used = NS(users=0, name="A"), NS(users=2, name="B")
    items = one("unused", meshes=[NS(users=0, name="Cube.001"), used], materials=[zero], images=[NS(users=0, name="t.png")],
                node_groups=[NS(users=0, name="Group")], collections=[NS(users=0, name="Old")], lights=[zero])
    assert items == ["mesh Cube.001", "material A", "image t.png", "light A", "node group Group", "collection Old"]


def test_files_missing_image_library_and_exceptions(tmp_path):
    here = tmp_path / "ok.png"
    here.write_bytes(b"x")
    images = [
        NS(name="Wood.png", source="FILE", packed_file=None, filepath="//tex/Wood.png"),
        NS(name="ok.png", source="FILE", packed_file=None, filepath=str(here)),
        NS(name="packed.png", source="FILE", packed_file=object(), filepath="//gone.png"),
        NS(name="gen", source="GENERATED", packed_file=None, filepath=""),
        NS(name="movie.mp4", source="MOVIE", packed_file=None, filepath=str(tmp_path / "movie.mp4")),
        NS(name="blank", source="FILE", packed_file=None, filepath=""),
    ]
    libs = [NS(name="lib.blend", filepath="//lib.blend"), NS(name="here.blend", filepath=str(here))]
    items = one("files", images=images, libraries=libs)
    assert items[0] == "image Wood.png (missing: /nowhere/tex/Wood.png)"
    assert items[1] == f"image movie.mp4 (missing: {(tmp_path / 'movie.mp4').as_posix()})"
    assert items[2] == "image blank (missing: no path)"
    assert items[3] == "library lib.blend (missing: /nowhere/lib.blend)"
    assert len(items) == 4


def test_collections():
    empty = Coll("Empty")
    items = one("collections", collections=[empty, Coll("Full", objects=[obj("A")]), Coll("Parent", children=[empty])])
    assert items == ["Empty"]


# -- object checks ---------------------------------------------------------------------------------

def test_scale_flags_every_non_unit_and_negative():
    objs = [obj("Unit"), obj("Big", scale=(2, 2, 2)), obj("Tiny", scale=(1.00005, 1, 1)), obj("Flip", scale=(-1, 1, 1)),
            obj("Odd", scale=(1, 0.5, 1.25)), obj("Lamp", type_="LIGHT", scale=(3, 3, 3)),
            obj("Arm", type_="ARMATURE", scale=(2, 2, 2))]
    assert one("scale", objs) == ["Big (2,2,2)", "Flip (-1,1,1, negative)", "Odd (1,0.5,1.25)", "Arm (2,2,2)"]


def test_materials_only_types_that_take_one():
    objs = [obj("Has"), obj("None", mats=()), obj("EmptySlot", mats=(None,)), obj("Curve", type_="CURVE", mats=()),
            obj("Cam", type_="CAMERA", mats=()), obj("Empty", type_="EMPTY", mats=())]
    assert one("materials", objs) == ["None", "EmptySlot", "Curve"]


def test_ngons_counts_faces_over_four_loops():
    objs = [obj("Quads"), obj("Poly", data=mesh_data(faces=(3, 5, 12, 4))), obj("Cu", type_="CURVE"),
            obj("One", data=mesh_data(faces=(8,)))]
    assert one("ngons", objs) == ["Poly (2 faces)", "One (1 face)"]


def test_visibility_texts():
    objs = [obj("Shown"), obj("Hidden", visible=False), obj("Off", hide_render=True),
            obj("Both", visible=False, hide_render=True)]
    assert one("visibility", objs) == ["Hidden (hidden, renders)", "Off (render off)", "Both (render off)"]


def test_visibility_falls_back_when_not_in_view_layer():
    def raiser():
        raise RuntimeError("not in view layer")

    o = obj("Away", hide_viewport=True)
    o.visible_get = raiser
    assert one("visibility", [o]) == ["Away (hidden, renders)"]


def test_nonmanifold_and_loose_share_one_bmesh_per_object(bmesh_stub):
    open_mesh = mesh_data(manifold=[True] * 8 + [False] * 4, loose=[False] * 5 + [True] * 3)
    objs = [obj("Open", data=open_mesh), obj("Closed"), obj("Lamp", type_="LIGHT")]
    bpy, scene = make(objs)
    found = ac.run_checks(bpy, scene, ["nonmanifold", "loose"], objs)
    assert found == {"nonmanifold": ["Open (4 edges)"], "loose": ["Open (3 verts)"]}
    assert bmesh_stub.created == 2  # one per mesh object, not per check


def test_bmesh_not_built_without_a_bmesh_check(bmesh_stub):
    objs = [obj("A")]
    bpy, scene = make(objs)
    ac.run_checks(bpy, scene, ["scale", "ngons", "materials"], objs)
    assert bmesh_stub.created == 0


# -- run_checks / op -------------------------------------------------------------------------------

def test_run_checks_keeps_check_order_and_skips_scene_when_asked():
    objs = [obj("A", scale=(2, 2, 2))]
    bpy, scene = make(objs, camera=False)
    found = ac.run_checks(bpy, scene, ["scale", "camera"], objs)
    assert list(found) == ["camera", "scale"]
    assert list(ac.run_checks(bpy, scene, ["scale", "camera"], objs, scene_level=False)) == ["scale"]


def test_op_result_shape_and_order(monkeypatch, bmesh_stub):
    objs = [obj("Cube", scale=(2, 2, 2)), obj("Bare", mats=(), data=mesh_data(faces=(5,)))]
    bpy, _ = make(objs, camera=False)
    r = run_op(monkeypatch, bpy, checks=None, objects=None, collection=None)
    assert list(r) == ["filepath", "objects_checked", "checks_run", "scene_checks_skipped", "findings"]
    assert r["objects_checked"] == 2 and r["checks_run"] == list(ac.CHECK_IDS) and r["scene_checks_skipped"] is False
    assert [f["check"] for f in r["findings"]] == ["camera", "scale", "materials", "ngons"]
    assert r["findings"][0] == {"check": "camera", "level": "warn", "count": 1, "items": ["no scene camera"]}
    assert r["findings"][1]["items"] == ["Cube (2,2,2)"] and r["findings"][3]["level"] == "info"


def test_op_clean_scene_has_no_findings(monkeypatch, bmesh_stub):
    bpy, _ = make([obj("Cube"), light()])
    r = run_op(monkeypatch, bpy, checks=None, objects=None, collection=None)
    assert r["findings"] == [] and r["objects_checked"] == 2


def test_op_checks_subset_and_unknown(monkeypatch):
    bpy, _ = make([obj("Cube", scale=(2, 2, 2))], camera=False)
    r = run_op(monkeypatch, bpy, checks=["scale", "camera"])
    assert r["checks_run"] == ["camera", "scale"]
    with pytest.raises(OpError) as e:
        run_op(monkeypatch, bpy, checks=["bogus"])
    assert e.value.code == "OP_FAILED"


def test_op_object_filter_skips_scene_checks(monkeypatch):
    a, b = obj("A", scale=(2, 2, 2)), obj("B", scale=(3, 3, 3))
    bpy, _ = make([a, b], camera=False)
    r = run_op(monkeypatch, bpy, checks=None, objects=["B"], collection=None)
    assert r["scene_checks_skipped"] is True and r["objects_checked"] == 1
    assert "camera" not in r["checks_run"] and "lighting" not in r["checks_run"]
    assert [f["check"] for f in r["findings"]] == ["scale"] and r["findings"][0]["items"] == ["B (3,3,3)"]


def test_op_unknown_object_is_not_found(monkeypatch):
    bpy, _ = make([obj("Cube")])
    with pytest.raises(OpError) as e:
        run_op(monkeypatch, bpy, objects=["Cub"])
    assert e.value.code == "NOT_FOUND" and "Cube" in e.value.message


def test_op_collection_filter_and_object_restricted_by_it(monkeypatch):
    a, b = obj("A", scale=(2, 2, 2)), obj("B", scale=(2, 2, 2))
    bpy, _ = make([a, b], collections=[Coll("Props", objects=[b])])
    r = run_op(monkeypatch, bpy, collection="Props")
    assert r["objects_checked"] == 1 and r["findings"][0]["items"] == ["B (2,2,2)"]
    r = run_op(monkeypatch, bpy, objects=["A"], collection="Props")  # --object wins, the collection only restricts
    assert r["objects_checked"] == 0 and r["findings"] == []
    with pytest.raises(OpError) as e:
        run_op(monkeypatch, bpy, collection="Nope")
    assert e.value.code == "NOT_FOUND"


def test_op_without_bmesh_warns_and_drops_those_checks(monkeypatch):
    monkeypatch.setitem(sys.modules, "bmesh", None)  # makes `import bmesh` raise ImportError
    bpy, _ = make([obj("Cube", scale=(2, 2, 2))])
    r = run_op(monkeypatch, bpy)
    assert "nonmanifold" not in r["checks_run"] and "loose" not in r["checks_run"] and "scale" in r["checks_run"]
    assert r["warnings"] == ["mesh checks unavailable in this session: nonmanifold, loose"]
